"""Long-running operations: shared progress and cooperative cancellation, stored in MongoDB.

One document in `operations` per run (a campaign, a mailbox sync, a batch of company checks…), visible from every
process:

    {_id, uid, kind, key, state, public, private (encrypted), version, lock_token, cancel_requested, error, timestamps}

- **One at a time.** start() takes the lease `op:<kind>[:<key>]` for that user (infra/locks.py) and holds it for the
  whole run, so a second start, from any process, is refused (409), just like the old per-process flags.
- **Progress.** `public` holds plain, non-personal figures (counts, stage labels, timings). Anything personal (names,
  log lines, results) goes in `private`, encrypted like the rest of the user's data. Readers get both merged.
- **States.** running → done | failed | cancelled | interrupted. A finished operation never changes again.
  "interrupted" means its process stopped without finishing (crash or restart): detected when the run's lease is gone,
  and recorded the first time someone looks.
- **Stale writers.** Every update must match the run's id, `state: running` and its lease token, and the lease must
  still be valid; a writer that lost its lease, or whose run was superseded, gets LeaseLost and nothing is written.
  Each update increments `version`.
- **External actions** (sending a message, submitting an application) call checkpoint() right before acting, so a
  run that lost its lease or was cancelled stops *before* the action. The record of an action that did happen is always
  written afterwards (under the account's own lock, which is fenced), never blocked by the run's lease: losing that
  record could make a later run repeat the action.
- **Cancellation** sets `cancel_requested` on the running document (from any process; idempotent; a finished run is
  left alone). The work calls checkpoint() / wait() between units of work, never in the middle of one, and stops with
  Cancelled there; `with op:` then records "cancelled" and releases the lease. Nothing is ever killed.
- **Retention.** Finished runs get `expire_at` (OPERATIONS_KEEP_DAYS, default 7) and a TTL index removes them later.
  TTL is only clean-up; correctness never depends on it.

If MongoDB is unavailable: start() fails (503); updates and checkpoints raise; a worker that can't confirm its lease
stops at its next checkpoint. Nothing falls back to in-process state.
"""

import datetime as dt
import os
import threading
import time
import uuid

from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from core.crypto import seal, unseal
from core.db import M
from core.validation import Invalid
from infra import locks
from infra.redact import error_text

RUNNING, DONE, FAILED, CANCELLED, INTERRUPTED = "running", "done", "failed", "cancelled", "interrupted"
FINAL = (DONE, FAILED, CANCELLED, INTERRUPTED)
# Every kind of run that uses this module (an allow-list: the cancel API only accepts these).
KINDS = ("send", "inbox", "apps_sync", "mail_sync", "nearby", "ai_evaluate", "ai_apply", "ai_match", "finder",
         "briefing", "research", "wa_link")
KEEP_DAYS = float(os.environ.get("OPERATIONS_KEEP_DAYS", "7") or 7)


class Cancelled(Exception):
    """Raised at a checkpoint when cancellation was requested. `with op:` records it and doesn't re-raise."""


def ensure_indexes():
    M.operations.create_index([("uid", 1), ("kind", 1), ("key", 1), ("fence", DESCENDING)])
    M.operations.create_index([("state", 1), ("updated_at", 1)])
    M.operations.create_index("expire_at", expireAfterSeconds=0)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _resource(kind, key):
    return f"op:{kind}" + (f":{key}" if key else "")


def _expiry():
    return _now() + dt.timedelta(days=KEEP_DAYS)


class Op:
    def __init__(self, doc, lease):
        self.id, self.uid, self.kind, self.key = doc["_id"], doc["uid"], doc["kind"], doc["key"]
        self.lease = lease
        self.state = RUNNING
        self._private = dict(doc.get("_private_plain") or {})

    def __repr__(self):
        return f"<Op {self.kind} {self.id[:8]} {self.state}>"

    def _write(self, sets=None, inc=None, final=None):
        self.lease.ensure()
        now = _now()
        upd = {"$set": {**(sets or {}), "updated_at": now}, "$inc": {"version": 1, **(inc or {})}}
        if final:
            upd["$set"].update(state=final, finished_at=now, expire_at=_expiry())
        r = M.operations.update_one({"_id": self.id, "state": RUNNING, "lock_token": self.lease.token}, upd)
        if not r.matched_count:
            raise locks.LeaseLost("This run is no longer active (it was stopped or replaced).")

    def update(self, public=None, private=None, inc=None):
        """Merge fields into the progress. `inc` adds to public counters atomically (safe from several threads)."""
        sets = {f"public.{k}": v for k, v in (public or {}).items()}
        if private:
            self._private.update(private)
            sets["private"] = seal(self._private)
        self._write(sets, {f"public.{k}": v for k, v in (inc or {}).items()})

    def view(self):
        return view_of(M.operations.find_one({"_id": self.id}))

    def checkpoint(self):
        """Call between units of work. Raises Cancelled if cancellation was requested, LeaseLost if we lost the lock."""
        self.lease.ensure()
        try:
            doc = M.operations.find_one({"_id": self.id}, {"cancel_requested": 1, "state": 1, "lock_token": 1})
        except PyMongoError:
            return  # can't see a cancel request right now; the lease check above still stops us before it runs out
        if not doc or doc.get("state") != RUNNING or doc.get("lock_token") != self.lease.token:
            raise locks.LeaseLost("This run is no longer active (it was stopped or replaced).")
        if doc.get("cancel_requested"):
            raise Cancelled()

    def wait(self, seconds, step=1.0):
        """Sleep, checking for cancellation about every `step` seconds."""
        end = time.monotonic() + seconds
        while True:
            self.checkpoint()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(step, left))

    def finish(self, state=DONE, public=None, private=None, error=""):
        """Record the outcome (once). A run that finished its work is "done" even if a cancel arrived meanwhile."""
        if self.state != RUNNING:
            return
        sets = {f"public.{k}": v for k, v in (public or {}).items()}
        if private:
            self._private.update(private)
            sets["private"] = seal(self._private)
        if error:
            sets["error"] = error[:300]
        try:
            self._write(sets, final=state)
            self.state = state
        finally:
            self.lease.release()

    def __enter__(self):
        return self

    def __exit__(self, et, e, tb):
        if self.state == RUNNING:
            if et is None:
                self.finish(DONE)
            elif issubclass(et, Cancelled):
                self.finish(CANCELLED)
            elif issubclass(et, locks.LeaseLost):
                self.state = INTERRUPTED  # not ours to write any more; readers will see it as interrupted
                self.lease.release()
            else:
                try:
                    self.finish(FAILED, error=error_text(e) if not isinstance(e, Invalid) else e.message)
                except (locks.LeaseLost, PyMongoError):
                    self.lease.release()
        return et is not None and issubclass(et, Cancelled)


def spawn(op, fn, *args, name=None):
    """Run fn(op, *args) in a daemon thread inside `with op:` (outcome recorded; errors logged, never raised)."""
    def go():
        try:
            with op:
                fn(op, *args)
        except Exception as e:
            print(f"[Inkwell] {op.kind}: {error_text(e, 200)}", flush=True)
    t = threading.Thread(target=go, daemon=True, name=name or f"{op.kind}-{op.uid[:6]}")
    t.start()
    return t


class CancelEvent:
    """threading.Event look-alike for code that takes a `stop` event: is_set() becomes a (throttled) checkpoint."""

    def __init__(self, op, every=1.0):
        self.op, self.every, self._seen, self._at = op, every, False, 0.0
        self._mu = threading.Lock()

    def is_set(self):
        with self._mu:
            if not self._seen and time.monotonic() - self._at >= self.every:
                self._at = time.monotonic()
                try:
                    self.op.checkpoint()
                except (Cancelled, locks.LeaseLost):
                    self._seen = True
            return self._seen

    def set(self):
        self._seen = True

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while not self.is_set() and time.monotonic() < end:
            time.sleep(min(self.every, max(0.0, end - time.monotonic())))
        return self._seen


def start(uid, kind, key="", public=None, private=None, busy="This is already running."):
    """Begin a run for this user, or raise LockTimeout (409) if one is running anywhere, LockUnavailable (503) if the
    database can't be reached. Returns the Op; use `with op:` in the worker."""
    if kind not in KINDS:
        raise ValueError(f"Unknown operation kind: {kind}")
    lease = locks.acquire(_resource(kind, key), uid, wait=0, busy=busy)
    try:
        now = _now()
        # Anything still marked running for this slot is dead: we hold its lease now.
        M.operations.update_many({"uid": uid, "kind": kind, "key": key, "state": RUNNING},
                                 {"$set": {"state": INTERRUPTED, "finished_at": now, "updated_at": now, "expire_at": _expiry(),
                                           "error": "Stopped unexpectedly (the server restarted). Start it again."}})
        doc = {"_id": uuid.uuid4().hex, "uid": uid, "kind": kind, "key": key, "state": RUNNING, "public": dict(public or {}),
               "private": seal(dict(private or {})), "version": 1, "lock_token": lease.token, "fence": lease.fence,
               "cancel_requested": False, "error": "", "started_at": now, "updated_at": now}
        M.operations.insert_one(doc)
    except PyMongoError as e:
        lease.release()
        raise locks.LockUnavailable() from e
    return Op({**doc, "_private_plain": private}, lease)


def _latest(uid, kind, key):
    # newest = highest fence: each run of a slot gets a larger fence from its lease (start times can tie)
    return M.operations.find_one({"uid": uid, "kind": kind, "key": key}, sort=[("fence", DESCENDING), ("started_at", DESCENDING)])


def _settle(doc):
    """A running document whose lease is gone belongs to a process that died: record it as interrupted."""
    if not doc or doc["state"] != RUNNING:
        return doc
    h = locks.holder(_resource(doc["kind"], doc["key"]), doc["uid"])
    if h and h.get("token") == doc["lock_token"]:
        return doc
    if h is None and time.time() - doc["started_at"].replace(tzinfo=dt.timezone.utc).timestamp() < 2:
        return doc  # just started: give the insert and the lease a moment to line up
    now = _now()
    M.operations.update_one({"_id": doc["_id"], "state": RUNNING, "lock_token": doc["lock_token"]},
                            {"$set": {"state": INTERRUPTED, "finished_at": now, "updated_at": now, "expire_at": _expiry(),
                                      "error": "Stopped unexpectedly (the server restarted). Start it again."}})
    return M.operations.find_one({"_id": doc["_id"]})


def view_of(doc):
    """What the owner sees: state, timings, error, and the progress fields (public and decrypted private)."""
    if not doc:
        return None
    ts = lambda d: d.replace(tzinfo=dt.timezone.utc).timestamp() if d else None  # noqa: E731
    return {**(doc.get("public") or {}), **(unseal(doc.get("private"), {}) or {}),
            "state": doc["state"], "started": ts(doc.get("started_at")), "finished": ts(doc.get("finished_at")),
            "cancel_requested": bool(doc.get("cancel_requested")), "error": doc.get("error", ""), "op_id": doc["_id"]}


def current(uid, kind, key=""):
    """The latest run of this kind for this user (settled), as view_of(); None if there never was one."""
    return view_of(_settle(_latest(uid, kind, key)))


def running(uid, kind, key=""):
    v = current(uid, kind, key)
    return bool(v and v["state"] == RUNNING)


def running_keys(uid, kind):
    """Keys of this user's runs of `kind` that are running right now."""
    out = []
    for doc in M.operations.find({"uid": uid, "kind": kind, "state": RUNNING}):
        doc = _settle(doc)
        if doc and doc["state"] == RUNNING:
            out.append(doc["key"])
    return out


def batch_view(uid, kind, key=""):
    """{"state", "total", "done", "current", ...} for the latest run, or {} if there was none: the shape the pages
    have always polled (they check state == "running")."""
    v = current(uid, kind, key)
    if not v:
        return {}
    return {**{k: x for k, x in v.items() if k not in ("op_id", "started", "finished", "cancel_requested", "error")},
            "current": v.get("current", "") if v["state"] == RUNNING else "", "stopping": v["cancel_requested"] and v["state"] == RUNNING}


def cancel(uid, kind, key=""):
    """Ask this user's running run of `kind` to stop at its next checkpoint. Idempotent. Returns the run's view
    (None if there's none); a finished run is returned unchanged. Raises LockUnavailable if the database is down."""
    try:
        M.operations.update_one({"uid": uid, "kind": kind, "key": key, "state": RUNNING},
                                {"$set": {"cancel_requested": True, "cancel_requested_at": _now()}, "$inc": {"version": 1}})
        return current(uid, kind, key)
    except PyMongoError as e:
        raise locks.LockUnavailable("Couldn't reach the database to stop it. Try again in a moment.") from e
