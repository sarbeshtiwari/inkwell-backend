"""Distributed, lease-based locks in MongoDB, shared by every process, worker and restart.

A lock is one document in `locks`, keyed by its scope: `u:<uid>:<resource>` for one user's resource, `g:<resource>`
for something global (a background loop). Holding it means holding a *lease*:

- **Acquire** is atomic: one conditional update takes the lock only if it is free or its lease has expired, judged by
  the database server's clock (`$$NOW`), never the caller's. The record is created (free) the first time. Each
  acquisition gets a fresh random `token` (ownership) and a larger `fence` (a fencing number that only grows: one
  more than before, and at least the server clock in ms, so it never restarts low even if lock records are removed).
- **Renew**: a background thread in each process extends every held lease well before it runs out. A renewal only
  succeeds while the lease is still ours and unexpired; if it fails, the lease is marked lost.
- **Release** is idempotent and owner-only: it matches on our token, so it can never free someone else's lock.
  The record is kept (not deleted) so `fence` keeps growing across holders.
- **A process that crashes** simply stops renewing; its lease expires and the next acquirer takes over. Expiry is
  decided at acquisition time, not by TTL deletion.
- **Stale holders can't keep writing.** Each lease tracks, on the local monotonic clock, a conservative "valid until"
  (measured from *before* the request that set the expiry). `ensure()` refuses once fewer than SAFETY_SECONDS remain.
  Writes guarded by a lease (see Workspace.save) also carry the fence: a document already written by a newer holder
  rejects older fences. Remaining window: a process paused for longer than SAFETY_SECONDS between `ensure()` and its
  write, *and* writing a document the new holder hasn't written yet; closing that needs transactions (see
  docs/locks-progress-cancellation.md).

If MongoDB is unavailable, acquiring fails with LockUnavailable (no silent fallback to a process-local lock), and a
held lease can't be renewed, so its holder stops at its next `ensure()` instead of carrying on unprotected.

Settings (seconds): LOCK_LEASE_SECONDS (30), LOCK_RENEW_SECONDS (10), LOCK_SAFETY_SECONDS (5).
"""

import datetime as dt
import os
import random
import re
import socket
import threading
import time
import uuid
import weakref

from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from core.db import M
from core.validation import Invalid


def _seconds(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


LEASE_SECONDS = _seconds("LOCK_LEASE_SECONDS", 30.0)
RENEW_SECONDS = _seconds("LOCK_RENEW_SECONDS", 10.0)
SAFETY_SECONDS = _seconds("LOCK_SAFETY_SECONDS", 5.0)
EPOCH = dt.datetime(1970, 1, 1)
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
UID_OK = re.compile(r"^[\w-]{1,64}$")


class LockTimeout(Invalid):
    """Someone else holds the lock and it didn't come free in time."""

    def __init__(self, message="This is busy right now. Try again in a moment."):
        super().__init__(message, status=409)


class LockUnavailable(Invalid):
    """The database couldn't be reached, so the lock couldn't be taken (never falls back to a local lock)."""

    def __init__(self, message="The database is unavailable right now. Try again in a moment."):
        super().__init__(message, status=503)


class LeaseLost(Invalid):
    """This process no longer holds the lock (it expired, or a newer holder took over): stop and don't write."""

    def __init__(self, message="This work lost its lock and was stopped. Try again."):
        super().__init__(message, status=503)


def ensure_indexes():
    M.locks.create_index([("uid", 1), ("resource", 1)])


def lock_id(resource, uid=None):
    if not NAME_OK.match(resource or ""):
        raise ValueError(f"Bad lock name: {resource!r}")
    if uid is None:
        return f"g:{resource}"
    if not UID_OK.match(str(uid)):
        raise ValueError("Bad user id for a lock.")
    return f"u:{uid}:{resource}"


def _owner():
    return f"{socket.gethostname()}:{os.getpid()}:{threading.current_thread().name}"[:200]


class Lease:
    def __init__(self, doc, ttl, started):
        self.id, self.uid, self.resource = doc["_id"], doc.get("uid"), doc["resource"]
        self.token, self.fence, self.ttl = doc["token"], doc["fence"], ttl
        self._valid_until = started + ttl
        self._renewed = time.monotonic()
        self._lost = self._released = False
        self._mu = threading.Lock()

    def __repr__(self):
        return f"<Lease {self.id} fence={self.fence}{' lost' if self._lost else ''}{' released' if self._released else ''}>"

    @property
    def held(self):
        return not (self._lost or self._released) and time.monotonic() < self._valid_until

    def remaining(self):
        return max(0.0, self._valid_until - time.monotonic())

    def ensure(self, margin=None):
        """Raise LeaseLost unless we still hold the lock with at least `margin` seconds to spare
        (default SAFETY_SECONDS, at most a third of the lease)."""
        margin = min(SAFETY_SECONDS, self.ttl / 3) if margin is None else margin
        if self._released:
            raise LeaseLost("This lock was already released.")
        if self._lost or time.monotonic() > self._valid_until - margin:
            self._lost = True
            raise LeaseLost()
        return self

    def renew(self):
        """Extend the lease. Raises LeaseLost if it's no longer ours; PyMongoError if the database can't be reached."""
        with self._mu:
            if self._released or self._lost:
                raise LeaseLost()
            t0 = time.monotonic()
            r = M.locks.update_one(
                {"_id": self.id, "token": self.token, "$expr": {"$gt": ["$expires_at", "$$NOW"]}},
                [{"$set": {"expires_at": {"$add": ["$$NOW", int(self.ttl * 1000)]}, "renewed_at": "$$NOW"}}])
            if not r.matched_count:
                self._lost = True
                raise LeaseLost()
            self._valid_until, self._renewed = t0 + self.ttl, t0

    def release(self):
        """Idempotent; only ever frees our own acquisition. If the database is down the lease just expires."""
        with self._mu:
            if self._released:
                return
            self._released = True
        _RENEWER.discard(self)
        try:
            M.locks.update_one({"_id": self.id, "token": self.token},
                               {"$set": {"token": None, "owner": None, "expires_at": EPOCH, "released_at": dt.datetime.now(dt.timezone.utc)}})
        except PyMongoError as e:
            print(f"[Inkwell] lock {self.id}: release not recorded ({type(e).__name__}); it will expire on its own.", flush=True)

    def __enter__(self):
        push(self)
        return self

    def __exit__(self, *exc):
        pop(self)
        self.release()
        return False


def _take(lid, uid, resource, ttl, token):
    update = [{"$set": {"uid": uid, "resource": resource, "token": token, "owner": _owner(),
                        # grows on every acquisition, and never restarts low even if the record is deleted:
                        # at least the server's clock in milliseconds
                        "fence": {"$max": [{"$add": [{"$ifNull": ["$fence", 0]}, 1]}, {"$toLong": "$$NOW"}]},
                        "acquired_at": "$$NOW", "renewed_at": "$$NOW",
                        "expires_at": {"$add": ["$$NOW", int(ttl * 1000)]}}}]
    free = {"_id": lid, "$expr": {"$lte": ["$expires_at", "$$NOW"]}}
    doc = M.locks.find_one_and_update(free, update, return_document=ReturnDocument.AFTER)
    if doc is None and M.locks.update_one({"_id": lid}, {"$setOnInsert": {"uid": uid, "resource": resource, "fence": 0,
                                                                          "token": None, "expires_at": EPOCH}},
                                          upsert=True).upserted_id is not None:
        doc = M.locks.find_one_and_update(free, update, return_document=ReturnDocument.AFTER)  # first use of this lock
    return doc


def acquire(resource, uid=None, *, wait=0.0, ttl=None, poll=0.1, busy="This is busy right now. Try again in a moment."):
    """Take the lock, waiting at most `wait` seconds. Returns a Lease (use it as a context manager).
    Raises LockTimeout if it stays taken, LockUnavailable if the database can't be reached."""
    lid, ttl = lock_id(resource, uid), ttl or LEASE_SECONDS
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        token, t0 = uuid.uuid4().hex, time.monotonic()
        try:
            doc = _take(lid, uid, resource, ttl, token)
        except PyMongoError as e:
            raise LockUnavailable() from e
        if doc is not None and doc.get("token") == token:
            lease = Lease(doc, ttl, t0)
            _RENEWER.add(lease)
            return lease
        left = deadline - time.monotonic()
        if left <= 0:
            raise LockTimeout(busy)
        time.sleep(min(left, poll * (0.5 + random.random())))


def try_acquire(resource, uid=None, **kw):
    """Like acquire() without waiting; None if someone else holds it."""
    try:
        return acquire(resource, uid, wait=0, **kw)
    except LockTimeout:
        return None


def holder(resource, uid=None):
    """The current holder's {token, fence, owner, expires_at} if the lock is held right now (server time), else None."""
    return M.locks.find_one({"_id": lock_id(resource, uid), "token": {"$ne": None},
                             "$expr": {"$gt": ["$expires_at", "$$NOW"]}}, {"token": 1, "fence": 1, "owner": 1, "expires_at": 1})


# ---- leases held by the current thread (used to fence writes) --------------------------------------------------

_CTX = threading.local()


def push(lease):
    _CTX.__dict__.setdefault("stack", []).append(lease)


def pop(lease):
    stack = _CTX.__dict__.get("stack", [])
    if lease in stack:
        stack.remove(lease)


def current(uid):
    """Leases this thread holds for `uid`, innermost last."""
    return [lease for lease in _CTX.__dict__.get("stack", []) if lease.uid == uid]


def fence_key(lease):
    return "fences." + re.sub(r"[^a-z0-9_]", "_", lease.resource)


def fenced(leases, query, values):
    """Add fencing to a write: refuse it if a newer holder of any of these locks already wrote the document, and record
    our fences on it. Every lease must still be valid (ensure())."""
    conds = []
    for lease in leases:
        lease.ensure()
        k = fence_key(lease)
        conds.append({"$or": [{k: {"$exists": False}}, {k: {"$lte": lease.fence}}]})
        values[k] = lease.fence
    return ({**query, "$and": conds} if conds else query), values


# ---- renewal --------------------------------------------------------------------------------------------------

class _Renewer:
    """One daemon thread per process renews every live lease every RENEW_SECONDS (leases no longer referenced
    anywhere are forgotten, so a lease that code forgets to release still expires)."""

    def __init__(self):
        self._leases = weakref.WeakSet()
        self._mu = threading.Lock()
        self._thread = None

    def add(self, lease):
        with self._mu:
            self._leases.add(lease)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="lock-renewer")
                self._thread.start()

    def discard(self, lease):
        with self._mu:
            self._leases.discard(lease)

    def _run(self):
        while True:
            time.sleep(0.1 if any(lease.ttl < 3 for lease in list(self._leases)) else min(1.0, RENEW_SECONDS / 4))
            with self._mu:
                due = [lease for lease in self._leases
                       if time.monotonic() - lease._renewed >= min(RENEW_SECONDS, lease.ttl / 3)]
            for lease in due:
                try:
                    lease.renew()
                except LeaseLost:
                    self.discard(lease)
                    print(f"[Inkwell] lock {lease.id}: lease lost.", flush=True)
                except PyMongoError:
                    pass  # retried next round; if it never succeeds, ensure() stops the holder before it expires


_RENEWER = _Renewer()


# ---- the per-user workspace lock (Workspace.lock) ----------------------------------------------------------------

class UserLock:
    """Re-entrant, cross-process lock for one user's workspace, used as `with ws.lock:`.

    Threads of this process queue on a local re-entrant lock first (cheap); the first entry takes the distributed lease
    and the last exit releases it. Same semantics as the old per-process RLock, now across processes."""

    def __init__(self, uid, resource="workspace", wait=None):
        self.uid, self.resource = uid, resource
        self.wait = LEASE_SECONDS + 15 if wait is None else wait  # long enough to outlast a crashed holder's lease
        self._local = threading.RLock()
        self._depth = 0
        self._lease = None

    def __enter__(self):
        self._local.acquire()
        try:
            if self._depth == 0:
                self._lease = acquire(self.resource, self.uid, wait=self.wait,
                                      busy="Your account is busy with another change. Try again in a moment.")
                push(self._lease)
            self._depth += 1
        except BaseException:
            self._local.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self._depth -= 1
            if self._depth == 0:
                lease, self._lease = self._lease, None
                pop(lease)
                lease.release()
        finally:
            self._local.release()
        return False

    def acquire(self):
        self.__enter__()
        return True

    def release(self):
        self.__exit__(None, None, None)


_USER_LOCKS = {}
_USER_LOCKS_MU = threading.Lock()


def user_lock(uid):
    """The process-wide UserLock for this user (one object per user, so re-entrancy works across call sites)."""
    with _USER_LOCKS_MU:
        lk = _USER_LOCKS.get(uid)
        if lk is None:
            lk = _USER_LOCKS[uid] = UserLock(uid)
        return lk
