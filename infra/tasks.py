"""Durable task queue in MongoDB: the unit of work agents (and later other background work) run as.

One document in `tasks` per task, owned by a user (`uid`). Every state change also appends one row to
`task_events`. Shared by every process and worker; nothing about a task lives only in memory.

- **States.** queued → running → done | failed | cancelled | dead_letter. A running task whose worker stopped (its
  lease expired) goes back to queued (or to dead_letter when its attempts are used up) the next time anyone claims.
- **Claiming** is one atomic update judged by the database server's clock (`$$NOW`), as in infra/locks.py: exactly
  one worker gets a task, with a fresh random `lease_token` and a `fence` number that only grows. Every later write by
  that worker must match the token *and* an unexpired lease, so a worker that paused past its lease, or whose task was
  reclaimed, writes nothing (LeaseLost) — it can't finish, fail or retry a task someone else now owns.
- **Heartbeat.** While a handler runs, a thread renews the lease every third of its length.
- **Retries.** A handler that raises an ordinary exception is retried with exponential backoff (`RETRY_BASE_SECONDS`
  · 2^(attempt-1), capped at `RETRY_MAX_SECONDS`) until `max_attempts`, then dead-lettered. `Permanent` fails it at
  once. A lease lost mid-run counts as an attempt too, so a task that keeps crashing workers ends in dead_letter.
- **Idempotency.** enqueue() with an `idempotency_key` returns the existing task for that user and key instead of
  creating a second one (unique index), however many processes race.
- **Cancellation** is cooperative, like infra/operations.py: a queued task is cancelled at once; a running one gets
  `cancel_requested` and stops at its next checkpoint(). Nothing is killed mid-step.
- **Deadline.** A task not finished by its deadline isn't started or retried any more (failed, error class
  "deadline"); a running handler sees it at checkpoint().
- **Content** (input, result, error messages) is sealed like the rest of the user's data; only plain metadata (states, counts,
  timings, error classes) is stored in the clear.
- **Retention.** Tasks are kept until the account is deleted; a retention period is a pending product decision.

Handlers are registered per kind with register(kind, fn); fn(task) receives a Running task (task.input, task.uid,
task.checkpoint(), task.attempt) and returns the result (JSON-serialisable) or raises."""

import datetime as dt
import logging
import os
import re
import secrets
import socket
import threading
import time
import uuid

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from core.crypto import seal, unseal
from core.db import M
from core.validation import Invalid
from infra.redact import error_text

log = logging.getLogger("inkwell.tasks")

QUEUED, RUNNING, DONE, FAILED, CANCELLED, DEAD = "queued", "running", "done", "failed", "cancelled", "dead_letter"
FINAL = (DONE, FAILED, CANCELLED, DEAD)


def _seconds(name, default):
    try:
        return max(0.05, float(os.environ.get(name, default)))
    except ValueError:
        return default


LEASE_SECONDS = _seconds("TASK_LEASE_SECONDS", 30.0)
RETRY_BASE_SECONDS = _seconds("TASK_RETRY_BASE_SECONDS", 5.0)
RETRY_MAX_SECONDS = _seconds("TASK_RETRY_MAX_SECONDS", 600.0)
DEFAULT_ATTEMPTS = 3


def _int(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


# Delegation limits (proposed defaults from the architecture proposal; not product-approved numbers)
MAX_DEPTH = _int("TASK_MAX_DEPTH", 3)          # a root task is depth 0; its children 1, …
MAX_CHILDREN = _int("TASK_MAX_CHILDREN", 10)   # direct children per task
KIND_OK = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
KEY_OK = re.compile(r"^[\w.:@+-]{1,200}$")
ID_OK = re.compile(r"^[0-9a-f]{32}$")

HANDLERS = {}


class Cancelled(Exception):
    """Raised by checkpoint() when the task was cancelled; the worker records it as cancelled."""


class DeadlinePassed(Exception):
    """Raised by checkpoint() when the task's deadline has passed; recorded as failed (error class "deadline")."""


class Permanent(Exception):
    """Raise from a handler for a failure that retrying can't fix (bad input, refused permission)."""

    def __init__(self, message, error_class="permanent"):
        super().__init__(message)
        self.error_class = error_class


class LeaseLost(Exception):
    """This worker no longer owns the task (lease expired or reclaimed): stop without writing anything."""


class Stopped(Cancelled):
    """Raised by checkpoint() when a kill switch covers this task (infra/controls.py)."""


class DelegationRefused(Invalid):
    """A child task would break a delegation limit: too deep, too many children, or a cycle."""

    def __init__(self, message):
        super().__init__(message, status=409)


def register(kind, fn):
    if not KIND_OK.match(kind or ""):
        raise ValueError(f"Bad task kind: {kind!r}")
    HANDLERS[kind] = fn


def ensure_indexes():
    M.tasks.create_index([("status", ASCENDING), ("priority", DESCENDING), ("created_at", ASCENDING)])
    M.tasks.create_index([("status", ASCENDING), ("lease_until", ASCENDING)])
    M.tasks.create_index([("uid", ASCENDING), ("created_at", DESCENDING)])
    M.tasks.create_index([("uid", ASCENDING), ("idempotency_key", ASCENDING)], unique=True,
                         partialFilterExpression={"idempotency_key": {"$type": "string"}})
    M.tasks.create_index("parent_id", sparse=True)
    M.tasks.create_index([("agent_id", ASCENDING), ("status", ASCENDING)], sparse=True)
    M.task_events.create_index([("task_id", ASCENDING), ("at", ASCENDING)])
    M.task_events.create_index([("uid", ASCENDING), ("at", DESCENDING)])


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _event(task_id, uid, event, attempt=None, **detail):
    """Append one event (plain metadata only). A failure to record it is logged, never raised."""
    try:
        M.task_events.insert_one({"task_id": task_id, "uid": uid, "at": _now(), "event": event,
                                  **({"attempt": attempt} if attempt is not None else {}), **detail})
    except PyMongoError as e:
        log.warning("task event %s for %s not stored: %s", event, task_id, error_text(e, 200))


# ---- creating and reading ---------------------------------------------------------------------------------------

def enqueue(uid, kind, input=None, *, priority=0, max_attempts=DEFAULT_ATTEMPTS, idempotency_key=None,
            delay=0.0, deadline=None, parent_id=None, agent_id=None, trace_id=None):
    """Create a task for `uid`; returns its id. With an idempotency key, an existing task for the same user and key
    is returned instead of creating another one. `deadline` is an aware datetime or seconds from now.

    With `parent_id` it is a delegated child: same user as the parent (enforced), depth + 1, and refused
    (DelegationRefused) past MAX_DEPTH or MAX_CHILDREN, or when `agent_id` already appears in the parent's chain of
    agents (an agent can't delegate to itself or to an agent that delegated to it). A child never outlives its
    parent's deadline."""
    if not uid or not isinstance(uid, str):
        raise ValueError("A task needs the user it belongs to.")
    if kind not in HANDLERS:
        raise ValueError(f"No handler registered for task kind {kind!r}.")
    if idempotency_key is not None and not KEY_OK.match(str(idempotency_key)):
        raise ValueError("Bad idempotency key.")
    if not (1 <= int(max_attempts) <= 20):
        raise ValueError("max_attempts must be between 1 and 20.")
    now = _now()
    if isinstance(deadline, (int, float)):
        deadline = now + dt.timedelta(seconds=deadline)
    if agent_id is not None and not KIND_OK.match(agent_id):
        raise ValueError(f"Bad agent id: {agent_id!r}")
    depth, root_id, chain = 0, None, []
    if parent_id:
        parent = M.tasks.find_one({"_id": parent_id, "uid": uid}, {"depth": 1, "root_id": 1, "agent_chain": 1,
                                                                  "agent_id": 1, "deadline": 1, "trace_id": 1})
        if not parent:  # a child always belongs to its parent's user
            raise ValueError("Parent task not found for this user.")
        depth, root_id = parent.get("depth", 0) + 1, parent.get("root_id") or parent_id
        chain = [*parent.get("agent_chain", []), *([parent["agent_id"]] if parent.get("agent_id") else [])]
        if depth > MAX_DEPTH:
            raise DelegationRefused(f"Delegation is limited to {MAX_DEPTH} levels.")
        if agent_id is not None and agent_id in chain:
            raise DelegationRefused(f"{agent_id} is already working on this goal higher up (delegation cycle).")
        if M.tasks.count_documents({"parent_id": parent_id, "uid": uid}) >= MAX_CHILDREN:
            raise DelegationRefused(f"A task can delegate at most {MAX_CHILDREN} sub-tasks.")
        if parent.get("deadline") and (deadline is None or deadline > parent["deadline"]):
            deadline = parent["deadline"]
        trace_id = parent.get("trace_id") or trace_id  # a child always reports into its parent's trace
    tid = uuid.uuid4().hex
    doc = {"_id": tid, "uid": uid, "kind": kind, "input": seal(input if input is not None else {}),
           "status": QUEUED, "priority": int(priority), "attempts": 0, "max_attempts": int(max_attempts),
           "run_after": now + dt.timedelta(seconds=max(0.0, delay)), "deadline": deadline,
           "lease_token": None, "lease_until": None, "fence": 0, "worker": None, "cancel_requested": False,
           "parent_id": parent_id, "root_id": root_id, "depth": depth, "agent_id": agent_id, "agent_chain": chain,
           "trace_id": trace_id or tid,
           "created_at": now, "updated_at": now}
    if idempotency_key is not None:
        doc["idempotency_key"] = str(idempotency_key)
    try:
        M.tasks.insert_one(doc)
    except DuplicateKeyError:
        existing = M.tasks.find_one({"uid": uid, "idempotency_key": str(idempotency_key)}, {"_id": 1})
        if existing:
            return existing["_id"]
        raise
    _event(tid, uid, "enqueued", kind=kind)
    return tid


def view_of(doc, with_content=True):
    if not doc:
        return None
    out = {k: doc.get(k) for k in ("kind", "status", "priority", "attempts", "max_attempts", "run_after", "deadline",
                                   "cancel_requested", "parent_id", "root_id", "depth", "agent_id", "trace_id", "error_class", "created_at",
                                   "updated_at", "finished_at")}
    out["id"] = doc["_id"]
    if with_content:
        out["input"] = unseal(doc.get("input"), {})
        out["result"] = unseal(doc["result"], None) if doc.get("result") else None
        out["error"] = unseal(doc["error"], None) if doc.get("error") else None
    return out


def get(uid, task_id):
    """The user's own task (None for anyone else's: a task is never visible across users)."""
    if not ID_OK.match(str(task_id or "")):
        return None
    return view_of(M.tasks.find_one({"_id": task_id, "uid": uid}))


def events(uid, task_id):
    return list(M.task_events.find({"task_id": task_id, "uid": uid}, {"_id": 0}).sort("at", ASCENDING))


def cancel(uid, task_id):
    """Cancel the user's own task: queued → cancelled now; running → stops at its next checkpoint. Idempotent;
    finished tasks are left as they are. Returns the task view (None if it isn't this user's)."""
    if not ID_OK.match(str(task_id or "")):
        return None
    now = _now()
    r = M.tasks.update_one({"_id": task_id, "uid": uid, "status": QUEUED},
                           {"$set": {"status": CANCELLED, "cancel_requested": True, "finished_at": now, "updated_at": now}})
    if r.modified_count:
        _event(task_id, uid, "cancelled", reason="requested")
    elif M.tasks.update_one({"_id": task_id, "uid": uid, "status": RUNNING, "cancel_requested": False},
                            {"$set": {"cancel_requested": True, "updated_at": now}}).modified_count:
        _event(task_id, uid, "cancel_requested")
    return get(uid, task_id)


# ---- claiming -----------------------------------------------------------------------------------------------------

def _worker_name():
    return f"{socket.gethostname()}:{os.getpid()}:{threading.current_thread().name}"[:200]


def recover():
    """Settle tasks nobody can finish any more: expired leases go back to the queue (or dead_letter when their
    attempts are used up); queued tasks past their deadline fail. Safe to run from every process at any time."""
    now = _now()
    expired = {"status": RUNNING, "$expr": {"$lte": ["$lease_until", "$$NOW"]}}
    for doc in M.tasks.find(expired, {"uid": 1, "attempts": 1, "max_attempts": 1, "lease_token": 1, "cancel_requested": 1}):
        dead = doc.get("attempts", 0) >= doc.get("max_attempts", DEFAULT_ATTEMPTS)
        if doc.get("cancel_requested"):  # it was asked to stop: don't run it again
            sets, event = {"status": CANCELLED, "finished_at": now}, "cancelled"
        elif dead:
            sets, event = {"status": DEAD, "finished_at": now, "error_class": "lease_expired",
                           "error": seal("The worker running this task stopped too many times.")}, "dead_letter"
        else:
            sets, event = {"status": QUEUED, "run_after": now}, "lease_expired"
        r = M.tasks.update_one({"_id": doc["_id"], "status": RUNNING, "lease_token": doc.get("lease_token"),
                                "$expr": {"$lte": ["$lease_until", "$$NOW"]}},
                               {"$set": {**sets, "lease_token": None, "lease_until": None, "updated_at": now}})
        if r.modified_count:
            _event(doc["_id"], doc["uid"], event, attempt=doc.get("attempts"))
    late = {"status": QUEUED, "deadline": {"$ne": None}, "$expr": {"$lte": ["$deadline", "$$NOW"]}}
    for doc in M.tasks.find(late, {"uid": 1}):
        if M.tasks.update_one({"_id": doc["_id"], "status": QUEUED},
                              {"$set": {"status": FAILED, "error_class": "deadline", "error": seal("Its deadline passed."),
                                        "finished_at": now, "updated_at": now}}).modified_count:
            _event(doc["_id"], doc["uid"], "failed", error_class="deadline")


def claim(worker=None, kinds=None, lease=None):
    """Atomically take the next runnable task (highest priority, then oldest). Returns a Running task or None."""
    from infra import controls
    lease = lease or LEASE_SECONDS
    recover()
    switches = controls.active()
    if switches["global"]:
        return None  # everything is stopped: start nothing
    token = secrets.token_hex(16)
    q = {"status": QUEUED, "cancel_requested": False, "$expr": {"$lte": ["$run_after", "$$NOW"]}}
    if kinds is not None:
        q["kind"] = {"$in": list(kinds)}
    if switches["users"]:
        q["uid"] = {"$nin": sorted(switches["users"])}
    if switches["agents"]:
        q["agent_id"] = {"$nin": sorted(switches["agents"])}
    doc = M.tasks.find_one_and_update(
        q, [{"$set": {"status": RUNNING, "lease_token": token, "worker": worker or _worker_name(),
                      "lease_until": {"$add": ["$$NOW", int(lease * 1000)]}, "claimed_at": "$$NOW", "updated_at": "$$NOW",
                      "attempts": {"$add": ["$attempts", 1]},
                      "fence": {"$max": [{"$add": [{"$ifNull": ["$fence", 0]}, 1]}, {"$toLong": "$$NOW"}]}}}],
        sort=[("priority", DESCENDING), ("created_at", ASCENDING)], return_document=ReturnDocument.AFTER)
    if not doc:
        return None
    _event(doc["_id"], doc["uid"], "claimed", attempt=doc["attempts"], worker=doc["worker"])
    return Running(doc, lease)


class Running:
    """A task this worker holds. All writes are fenced by the lease token and an unexpired lease."""

    def __init__(self, doc, lease):
        self.id, self.uid, self.kind = doc["_id"], doc["uid"], doc["kind"]
        self.attempt, self.max_attempts, self.fence = doc["attempts"], doc["max_attempts"], doc["fence"]
        self.token, self.lease, self.deadline = doc["lease_token"], lease, doc.get("deadline")
        self.parent_id, self.root_id, self.depth = doc.get("parent_id"), doc.get("root_id"), doc.get("depth", 0)
        self.agent_id, self.trace_id = doc.get("agent_id"), doc.get("trace_id") or doc["_id"]
        self.input = unseal(doc.get("input"), {})
        self.state = RUNNING

    def __repr__(self):
        return f"<Task {self.kind} {self.id[:8]} attempt {self.attempt} {self.state}>"

    def _mine(self):
        return {"_id": self.id, "status": RUNNING, "lease_token": self.token, "$expr": {"$gt": ["$lease_until", "$$NOW"]}}

    def heartbeat(self):
        """Extend the lease. Raises LeaseLost if the task is no longer ours."""
        r = M.tasks.update_one(self._mine(), [{"$set": {"lease_until": {"$add": ["$$NOW", int(self.lease * 1000)]}}}])
        if not r.matched_count:
            raise LeaseLost()

    def checkpoint(self):
        """Call between units of work: raises Cancelled (Stopped when a kill switch covers it), DeadlinePassed or
        LeaseLost; otherwise renews the lease."""
        from infra import controls
        doc = M.tasks.find_one({"_id": self.id}, {"cancel_requested": 1, "status": 1, "lease_token": 1})
        if not doc or doc.get("status") != RUNNING or doc.get("lease_token") != self.token:
            raise LeaseLost()
        if doc.get("cancel_requested"):
            raise Cancelled()
        if controls.stopped(self.uid, self.agent_id):
            raise Stopped()
        if self.deadline and _now() >= self.deadline:
            raise DeadlinePassed()
        self.heartbeat()

    def _finish(self, status, sets, event, **detail):
        now = _now()
        r = M.tasks.update_one(self._mine(), {"$set": {"status": status, "lease_token": None, "lease_until": None,
                                                       "updated_at": now, **sets}})
        if not r.matched_count:
            raise LeaseLost()
        self.state = status
        _event(self.id, self.uid, event, attempt=self.attempt, **detail)

    def complete(self, result=None):
        self._finish(DONE, {"result": seal(result), "finished_at": _now(), "error": None, "error_class": None}, "done")

    def cancelled(self, reason="checkpoint"):
        self._finish(CANCELLED, {"finished_at": _now()}, "cancelled", reason=reason)

    def fail(self, error, error_class="error", retryable=True):
        """Record a failure: retried later with backoff while attempts remain (and the deadline allows),
        otherwise failed (not retryable) or dead_letter (attempts used up)."""
        error = error_text(error, 300) if isinstance(error, BaseException) else str(error)[:300]
        backoff = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** (self.attempt - 1))
        retry_at = _now() + dt.timedelta(seconds=backoff)
        if retryable and self.attempt < self.max_attempts and not (self.deadline and retry_at >= self.deadline):
            self._finish(QUEUED, {"run_after": retry_at, "error": seal(error), "error_class": error_class}, "retry_scheduled",
                         error_class=error_class, backoff_seconds=backoff)
        elif retryable and self.attempt >= self.max_attempts:
            self._finish(DEAD, {"finished_at": _now(), "error": seal(error), "error_class": error_class}, "dead_letter",
                         error_class=error_class)
        else:
            self._finish(FAILED, {"finished_at": _now(), "error": seal(error),
                                  "error_class": "deadline" if retryable else error_class}, "failed", error_class=error_class)


# ---- running -----------------------------------------------------------------------------------------------------

class _Heartbeat(threading.Thread):
    def __init__(self, task):
        super().__init__(daemon=True, name=f"task-heartbeat-{task.id[:6]}")
        self.task, self.stop, self.lost = task, threading.Event(), False

    def run(self):
        while not self.stop.wait(self.task.lease / 3):
            try:
                self.task.heartbeat()
            except LeaseLost:
                self.lost = True
                return
            except PyMongoError as e:
                log.warning("task %s heartbeat failed: %s", self.task.id, error_text(e, 200))


def execute(task):
    """Run a claimed task's handler and record the outcome. Returns the final (or re-queued) status, or None when
    the lease was lost (someone else owns the task now; nothing was written)."""
    from infra import telemetry
    beat = _Heartbeat(task)
    beat.start()
    try:
        with telemetry.attach(task.trace_id, task.uid, name=task.kind), \
                telemetry.span("step", task.kind, task_id=task.id, agent_id=task.agent_id, attempt=task.attempt) as sp:
            status = _execute(task, sp)
        if task.parent_id is None and status in FINAL:  # the root task's outcome closes its trace
            telemetry.end(task.trace_id, task.uid, status)
        return status
    finally:
        beat.stop.set()


def _execute(task, sp):
    try:
        try:
            fn = HANDLERS.get(task.kind)
            if fn is None:
                raise Permanent(f"No handler registered for task kind {task.kind!r}.", "no_handler")
            result = fn(task)
        except LeaseLost:
            return None
        except Stopped:
            task.cancelled("kill_switch")
        except Cancelled:
            task.cancelled()
        except DeadlinePassed:
            task.fail("Its deadline passed.", "deadline", retryable=False)
        except Permanent as e:
            task.fail(e, e.error_class, retryable=False)
        except Invalid as e:  # validation and permission errors from existing code: retrying won't help
            task.fail(e.message, "invalid", retryable=False)
        except Exception as e:
            log.warning("task %s (%s) attempt %s failed: %s", task.id, task.kind, task.attempt, error_text(e, 200))
            task.fail(e, getattr(e, "error_class", None) or type(e).__name__.lower()[:40])
        else:
            task.complete(result)
        if sp is not None and task.state not in (DONE, QUEUED):
            sp.fail(task.state)
        elif sp is not None and task.state == QUEUED:
            sp.fail("retry_scheduled")
        return task.state
    except LeaseLost:
        if sp is not None:
            sp.fail("lease_lost")
        return None


def run_once(worker=None, kinds=None, lease=None):
    """Claim and run one task. Returns (task_id, status), or None when nothing is runnable."""
    task = claim(worker, kinds, lease)
    if task is None:
        return None
    return task.id, execute(task)


def work(stop, worker=None, kinds=None, idle=1.0):
    """Worker loop: run tasks until `stop` (a threading.Event) is set; sleeps `idle` seconds when there's nothing."""
    while not stop.is_set():
        try:
            if run_once(worker, kinds) is None:
                stop.wait(idle)
        except PyMongoError as e:
            log.warning("task worker: database unavailable (%s)", error_text(e, 200))
            stop.wait(max(idle, 5.0))
