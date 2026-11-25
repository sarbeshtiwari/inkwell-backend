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
- **Stale holders can't write.** Protected writes (account data saved under `ws.lock`, see guarded_write()) run in a
  MongoDB transaction that first re-checks, on the lock record, that our token still holds an unexpired lease (server
  clock), then writes. A holder whose lease ran out, or was taken over, aborts without writing, however long it was
  paused and whether or not the new holder has written yet. The check also writes the lock record, so nobody can take
  the lock between our check and our commit (a competing acquire waits or, past ACQUIRE_ATTEMPT_MS, counts as busy).
  Transactions need a replica set: Atlas always is one; locally start.command runs a one-node set
  (tools/local_replset.py). On a plain server, protected writes are refused, never done unguarded.

If MongoDB is unavailable, acquiring fails with LockUnavailable (no silent fallback to a process-local lock), and a
held lease can't be renewed, so its holder stops at its next `ensure()` instead of carrying on unprotected.

Waiting for the account lock (`with ws.lock:`) is bounded: LOCK_WAIT_INTERACTIVE_SECONDS (18) inside a web request,
so the answer (409 + Retry-After) arrives before the proxy gives up, and LOCK_WAIT_BACKGROUND_SECONDS (45) in background
work, long enough to outlast a crashed holder's lease. The wait covers both the in-process queue and the shared lease,
and happens before the protected code runs: a request that times out has read and changed nothing.

Measurements (metrics(); slow waits, long holds and failures are also logged on `inkwell.locks`): wait time, hold time,
timeouts and database failures per lock kind, with the operation (endpoint or thread) and the request id. Never the
account id or anything from the account.

Settings (seconds): LOCK_LEASE_SECONDS (30), LOCK_RENEW_SECONDS (10), LOCK_SAFETY_SECONDS (5), LOCK_TXN_SECONDS (10),
LOCK_WAIT_INTERACTIVE_SECONDS (18), LOCK_WAIT_BACKGROUND_SECONDS (45), LOCK_SLOW_WAIT_SECONDS (1), LOCK_SLOW_HOLD_SECONDS (5).
"""

import datetime as dt
import logging
import os
import random
import re
import socket
import threading
import time
import uuid
import weakref
from collections import deque

import pymongo
from pymongo import ReadPreference, ReturnDocument, WriteConcern
from pymongo.errors import ExecutionTimeout, PyMongoError
from pymongo.read_concern import ReadConcern

from core.db import M
from core.validation import Invalid


def _seconds(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


LEASE_SECONDS = _seconds("LOCK_LEASE_SECONDS", 30.0)
TXN_SECONDS = _seconds("LOCK_TXN_SECONDS", 10.0)
WAIT_INTERACTIVE = _seconds("LOCK_WAIT_INTERACTIVE_SECONDS", 18.0)  # web requests: answer before the proxy times out (~26 s)
WAIT_BACKGROUND = _seconds("LOCK_WAIT_BACKGROUND_SECONDS", 45.0)    # background work: outlasts a crashed holder's lease
SLOW_WAIT = _seconds("LOCK_SLOW_WAIT_SECONDS", 1.0)
SLOW_HOLD = _seconds("LOCK_SLOW_HOLD_SECONDS", 5.0)
RETRY_AFTER = 2    # client-side limit for one protected write, retries included
ACQUIRE_ATTEMPT_MS = 500                            # one acquire attempt waits at most this long on a busy record
RENEW_SECONDS = _seconds("LOCK_RENEW_SECONDS", 10.0)
SAFETY_SECONDS = _seconds("LOCK_SAFETY_SECONDS", 5.0)
EPOCH = dt.datetime(1970, 1, 1)
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
UID_OK = re.compile(r"^[\w-]{1,64}$")


class LockTimeout(Invalid):
    """Someone else holds the lock and it didn't come free in time (409)."""

    def __init__(self, message="This is busy right now. Try again in a moment."):
        super().__init__(message, status=409)


class AccountBusy(LockTimeout):
    """The account lock (`ws.lock`) didn't come free in time. Retryable: answered as 409 with Retry-After."""

    retry_after = RETRY_AFTER

    def __init__(self, message="Your account is busy with another change. Try again in a moment."):
        super().__init__(message)


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
        self._held_since = time.monotonic()
        self._op = _operation()

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
            with pymongo.timeout(max(1.0, self.ttl / 3)):  # never wait long behind a transaction on the record
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
        _record_hold(self.resource, time.monotonic() - self._held_since, self._op)
        _RENEWER.discard(self)
        try:
            with pymongo.timeout(5):
                M.locks.update_one({"_id": self.id, "token": self.token},
                                   {"$set": {"token": None, "owner": None, "expires_at": EPOCH,
                                             "released_at": dt.datetime.now(dt.timezone.utc)}})
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
    """One acquisition attempt: the lease document after our update, or None if the lock is held (or its record is
    busy in another holder's transaction for longer than ACQUIRE_ATTEMPT_MS)."""
    update = [{"$set": {"uid": uid, "resource": resource, "token": token, "owner": _owner(),
                        # grows on every acquisition, and never restarts low even if the record is deleted:
                        # at least the server's clock in milliseconds
                        "fence": {"$max": [{"$add": [{"$ifNull": ["$fence", 0]}, 1]}, {"$toLong": "$$NOW"}]},
                        "acquired_at": "$$NOW", "renewed_at": "$$NOW",
                        "expires_at": {"$add": ["$$NOW", int(ttl * 1000)]}}}]
    free = {"_id": lid, "$expr": {"$lte": ["$expires_at", "$$NOW"]}}
    try:
        doc = M.locks.find_one_and_update(free, update, return_document=ReturnDocument.AFTER, maxTimeMS=ACQUIRE_ATTEMPT_MS)
        if doc is None and M.locks.find_one_and_update(
                {"_id": lid}, {"$setOnInsert": {"uid": uid, "resource": resource, "fence": 0, "token": None, "expires_at": EPOCH}},
                upsert=True, return_document=ReturnDocument.BEFORE, maxTimeMS=ACQUIRE_ATTEMPT_MS) is None:
            # the record didn't exist: it does now (free), so try once more
            doc = M.locks.find_one_and_update(free, update, return_document=ReturnDocument.AFTER, maxTimeMS=ACQUIRE_ATTEMPT_MS)
    except ExecutionTimeout:
        return None  # its holder is in the middle of a protected write: busy
    return doc


def acquire(resource, uid=None, *, wait=0.0, ttl=None, poll=0.1, busy="This is busy right now. Try again in a moment.",
            _queued=0.0):
    """Take the lock, waiting at most `wait` seconds. Returns a Lease (use it as a context manager).
    Raises LockTimeout if it stays taken, LockUnavailable if the database can't be reached."""
    lid, ttl = lock_id(resource, uid), ttl or LEASE_SECONDS
    start = time.monotonic()
    deadline = start + max(0.0, wait)
    while True:
        token, t0 = uuid.uuid4().hex, time.monotonic()
        try:
            doc = _take(lid, uid, resource, ttl, token)
        except PyMongoError as e:
            _record_wait(resource, time.monotonic() - start + _queued, "unavailable")
            raise LockUnavailable() from e
        if doc is not None and doc.get("token") == token:
            lease = Lease(doc, ttl, t0)
            _RENEWER.add(lease)
            _record_wait(resource, time.monotonic() - start + _queued, "acquired", quiet=wait == 0 and not _queued)
            return lease
        left = deadline - time.monotonic()
        if left <= 0:  # no new attempt starts after the deadline (one in flight takes ≤ ACQUIRE_ATTEMPT_MS + a round trip)
            _record_wait(resource, time.monotonic() - start + _queued, "timeout", quiet=wait == 0 and not _queued)
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


_TXN_SUPPORT = {}


def transactions_supported():
    """True when the server can run multi-document transactions (a replica set or sharded cluster)."""
    key = id(M.client)
    if key not in _TXN_SUPPORT:
        hello = M.client.admin.command("hello")
        _TXN_SUPPORT[key] = bool(hello.get("setName") or hello.get("msg") == "isdbgrid")
    return _TXN_SUPPORT[key]


def guarded_write(uid, fn):
    """Run fn(session) as a write protected by the locks this thread holds for `uid`, and return its result.

    Without such locks, fn(None) runs as a plain write (unchanged behaviour). With them, one transaction (1) re-checks
    each lease on its lock record (our token, unexpired by the server clock) and touches that record, then (2) runs
    fn(session). If any lease is gone the transaction aborts and LeaseLost is raised: nothing is written. Transient
    conflicts are retried within TXN_SECONDS. A database that can't run transactions, or a failure, raises
    LockUnavailable; there is no unguarded fallback."""
    held = current(uid)
    if not held:
        return fn(None)
    for lease in held:
        lease.ensure()

    def body(session):
        for lease in held:
            r = M.locks.update_one({"_id": lease.id, "token": lease.token, "$expr": {"$gt": ["$expires_at", "$$NOW"]}},
                                   [{"$set": {"last_write_at": "$$NOW"}}], session=session)
            if not r.matched_count:
                raise LeaseLost()
        return fn(session)

    try:
        if not transactions_supported():
            raise LockUnavailable("The database must run as a replica set to save this safely. Nothing was changed.")
        with pymongo.timeout(TXN_SECONDS), M.client.start_session() as session:
            return session.with_transaction(body, read_concern=ReadConcern("snapshot"),
                                            write_concern=WriteConcern("majority", j=True),
                                            read_preference=ReadPreference.PRIMARY)
    except (LeaseLost, LockUnavailable):
        raise
    except PyMongoError as e:
        raise LockUnavailable("Couldn't confirm the change was saved (database problem). Try again.") from e


# ---- measurements ---------------------------------------------------------------------------------------------

LOG = logging.getLogger("inkwell.locks")
_METRICS, _METRICS_MU = {}, threading.Lock()


def _kind(resource):
    """Low-cardinality name for a lock: "workspace", "op:send", "loop:send-queue" (never ids or keys)."""
    parts = resource.split(":")
    return ":".join(parts[:2]) if parts[0] in ("op", "loop") else parts[0]


def _operation():
    """What is taking the lock: the Flask endpoint in a request, else the thread's name. Plus the request id."""
    try:
        from flask import has_request_context, request

        from infra.audit import request_id
        if has_request_context():
            return (request.endpoint or request.path.split("?")[0])[:80], request_id()
    except Exception:  # measurement never breaks locking
        pass
    return threading.current_thread().name[:80], ""


def _bucket(resource):
    k = _kind(resource)
    with _METRICS_MU:
        m = _METRICS.get(k)
        if m is None:
            m = _METRICS[k] = {"wait": deque(maxlen=2000), "hold": deque(maxlen=2000), "acquired": 0, "timeouts": 0,
                               "unavailable": 0}
        return k, m


def _record_wait(resource, seconds, outcome, quiet=False):
    k, m = _bucket(resource)
    with _METRICS_MU:
        m["wait"].append(seconds)
        m["acquired" if outcome == "acquired" else "timeouts" if outcome == "timeout" else "unavailable"] += 1
    if quiet:
        return  # try_acquire: "busy" is a normal answer, not a failure
    op, rid = _operation()
    if outcome != "acquired":
        LOG.warning("lock %s %s after %.0f ms (op=%s request=%s)", k, outcome, seconds * 1000, op, rid or "-")
    elif seconds >= SLOW_WAIT:
        LOG.warning("lock %s slow wait %.0f ms (op=%s request=%s)", k, seconds * 1000, op, rid or "-")


def _record_hold(resource, seconds, op):
    k, m = _bucket(resource)
    with _METRICS_MU:
        m["hold"].append(seconds)
    if seconds >= SLOW_HOLD and not k.startswith(("op", "loop")):  # tasks and loops hold their lease by design
        LOG.warning("lock %s held %.0f ms (op=%s request=%s)", k, seconds * 1000, op[0], op[1] or "-")


def metrics():
    """Per lock kind: acquired / timeouts / unavailable counts and wait/hold median, p95, p99 in ms (last 2000)."""
    def pct(xs):
        xs = sorted(xs)
        if not xs:
            return None
        at = lambda p: round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] * 1000, 1)  # noqa: E731
        return {"median": at(0.5), "p95": at(0.95), "p99": at(0.99), "n": len(xs)}
    with _METRICS_MU:
        snap = {k: {**{c: m[c] for c in ("acquired", "timeouts", "unavailable")}, "wait": list(m["wait"]),
                    "hold": list(m["hold"])} for k, m in _METRICS.items()}
    return {k: {**{c: v[c] for c in ("acquired", "timeouts", "unavailable")}, "wait_ms": pct(v["wait"]),
                "hold_ms": pct(v["hold"])} for k, v in snap.items()}


def reset_metrics():
    with _METRICS_MU:
        _METRICS.clear()


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

    BUSY = "Your account is busy with another change. Try again in a moment."

    def __init__(self, uid, resource="workspace", wait=None):
        self.uid, self.resource = uid, resource
        self.wait = wait  # None: chosen per entry (interactive or background); a number overrides both
        self._local = threading.RLock()
        self._depth = 0
        self._lease = None

    def wait_limit(self):
        if self.wait is not None:
            return self.wait
        from flask import has_request_context
        return WAIT_INTERACTIVE if has_request_context() else WAIT_BACKGROUND

    def __enter__(self):
        """Bounded wait (in-process queue + shared lease) *before* anything protected runs: on timeout, LockTimeout is
        raised to the caller and nothing has been read or written."""
        from core.workspace import (
            forbid_in_update,  # (late: workspace imports this module)
        )
        forbid_in_update("Taking the account lock")
        start, limit = time.monotonic(), self.wait_limit()
        if not self._local.acquire(timeout=limit):
            _record_wait(self.resource, time.monotonic() - start, "timeout")
            raise AccountBusy(self.BUSY)
        try:
            if self._depth == 0:
                queued = time.monotonic() - start  # time spent behind other threads of this process
                try:
                    self._lease = acquire(self.resource, self.uid, wait=max(0.0, limit - queued), busy=self.BUSY,
                                          _queued=queued or 1e-9)  # measured and reported as one wait, never "quiet"
                except LockTimeout:
                    raise AccountBusy(self.BUSY) from None
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
