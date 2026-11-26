"""Execution telemetry: traces and spans in MongoDB, correlated, redacted before they are written.

- **Trace** (`traces`): one per goal or task tree — `_id` = trace_id, `uid`, `name`, `status`, `started`, `ended`, and
  running totals (spans, errors, model calls, tokens, cost).
- **Span** (`spans`): one per event inside a trace — model call, tool call, delegation, approval, retry, error, final
  output, or a plain step. Fields: `trace_id`, `span_id`, `parent_span_id`, `uid`, `type`, `name`, `start`, `end`,
  `ms`, `status`, `error_class`, correlation ids (task, agent, versions, tool), token and cost figures, and `summary`.
- **What is written in the clear** is metadata only: ids, timings, counts, error classes, and a `summary` passed
  through redact.pii (no secrets; emails and phone numbers masked). Content, if a caller attaches it, is sealed in
  `payload` and readable only by the trace's owner (owner_payload). No chain-of-thought is ever recorded; callers
  pass outcomes, not reasoning.
- **Correlation.** `with trace(uid, name):` starts a trace in this thread; `with span(type, name):` nests spans under
  whatever span is open. Model calls made through infra/gateway.py inside a trace record a `model_call` span and carry
  the trace and span ids in their usage record. attach(trace_id, uid) continues an existing trace (e.g. in a worker).
- **Failure.** Telemetry never breaks the work it describes: if a span can't be stored, a redacted warning is logged
  and the work goes on.
- **Retention.** Kept until the account is deleted; TELEMETRY_KEEP_DAYS (unset by default: the retention period is a
  pending product decision) adds an expiry that a TTL index enforces."""

import datetime as dt
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from core.crypto import seal, unseal
from core.db import M
from infra import redact
from infra.redact import error_text

log = logging.getLogger("inkwell.telemetry")

SPAN_TYPES = ("step", "model_call", "tool_call", "delegation", "approval", "retry", "error", "final_output")
META_FIELDS = ("task_id", "run_id", "agent_id", "agent_version", "model_config_version", "tool_id", "provider",
               "model_id", "input_tokens", "output_tokens", "cost", "attempt")
SUMMARY_MAX = 500

_LOCAL = threading.local()


def ensure_indexes():
    M.traces.create_index([("uid", ASCENDING), ("started", DESCENDING)])
    M.traces.create_index([("status", ASCENDING), ("started", DESCENDING)])
    M.spans.create_index([("trace_id", ASCENDING), ("start", ASCENDING)])
    M.spans.create_index([("uid", ASCENDING), ("start", DESCENDING)])
    M.spans.create_index([("type", ASCENDING), ("start", DESCENDING)])
    for c in (M.traces, M.spans):
        c.create_index("expire_at", expireAfterSeconds=0)


def _expiry():
    days = os.environ.get("TELEMETRY_KEEP_DAYS", "").strip()
    try:
        return dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=float(days)) if days else None
    except ValueError:
        return None


def _stack():
    if not hasattr(_LOCAL, "stack"):
        _LOCAL.stack = []
    return _LOCAL.stack


def current():
    """(trace_id, span_id, uid) of the innermost open span in this thread, or None outside a trace."""
    s = _stack()
    return (s[-1]["trace_id"], s[-1]["span_id"], s[-1]["uid"]) if s else None


def _summary(text):
    if text in (None, ""):
        return ""
    return redact.pii(str(text))[:SUMMARY_MAX]


def _meta(fields):
    bad = set(fields) - set(META_FIELDS)
    if bad:
        raise ValueError(f"Unknown span field(s): {', '.join(sorted(bad))}")
    return {k: v for k, v in fields.items() if v is not None}


def _safe(fn, what):
    try:
        return fn()
    except PyMongoError as e:
        log.warning("telemetry %s not stored: %s", what, error_text(e, 200))


class Span:
    """An open span. Set .summary / .error_class / numeric fields with update() while it runs."""

    def __init__(self, trace_id, uid, type_, name, parent, fields):
        self.trace_id, self.uid, self.type, self.name = trace_id, uid, type_, name
        self.span_id, self.parent_span_id = uuid.uuid4().hex, parent
        self.fields, self.summary, self.payload = _meta(fields), "", None
        self.status, self.error_class = "ok", None
        self.start, self._t0 = time.time(), time.monotonic()

    def update(self, summary=None, payload=None, **fields):
        if summary is not None:
            self.summary = summary
        if payload is not None:
            self.payload = payload
        self.fields.update(_meta(fields))

    def fail(self, error_class, summary=None):
        self.status, self.error_class = "error", str(error_class)[:60]
        if summary is not None:
            self.summary = summary

    def _doc(self):
        end = time.time()
        doc = {"_id": self.span_id, "trace_id": self.trace_id, "span_id": self.span_id,
               "parent_span_id": self.parent_span_id, "uid": self.uid, "type": self.type, "name": str(self.name)[:120],
               "start": self.start, "end": end, "ms": round((time.monotonic() - self._t0) * 1000),
               "status": self.status, "error_class": self.error_class, "summary": _summary(self.summary), **self.fields}
        if self.payload is not None:
            doc["payload"] = seal(self.payload)
        exp = _expiry()
        if exp:
            doc["expire_at"] = exp
        return doc


def _totals(sp):
    inc = {"spans": 1, "errors": 1 if sp.status == "error" else 0}
    if sp.type == "model_call":
        inc.update(model_calls=1, input_tokens=sp.fields.get("input_tokens") or 0,
                   output_tokens=sp.fields.get("output_tokens") or 0, cost=sp.fields.get("cost") or 0)
    return {f"totals.{k}": v for k, v in inc.items()}


def _close(sp):
    doc = sp._doc()
    _safe(lambda: M.spans.insert_one(doc), f"span {sp.type}")
    _safe(lambda: M.traces.update_one({"_id": sp.trace_id}, {"$inc": _totals(sp)}), "trace totals")


@contextmanager
def span(type_, name, summary=None, **fields):
    """A span under the open trace (a no-op outside one). Exceptions mark it failed and propagate unchanged."""
    if type_ not in SPAN_TYPES:
        raise ValueError(f"Unknown span type: {type_!r}")
    s = _stack()
    if not s:
        yield None
        return
    top = s[-1]
    sp = Span(top["trace_id"], top["uid"], type_, name, top["span_id"], fields)
    if summary is not None:
        sp.summary = summary
    s.append({"trace_id": sp.trace_id, "span_id": sp.span_id, "uid": sp.uid})
    try:
        yield sp
    except BaseException as e:
        if sp.status == "ok":
            sp.fail(getattr(e, "error_class", None) or type(e).__name__.lower())
        raise
    finally:
        s.pop()
        _close(sp)


@contextmanager
def trace(uid, name, trace_id=None):
    """Start (or, with trace_id, continue) a trace owned by `uid` in this thread. Yields the trace id."""
    if not uid:
        raise ValueError("A trace belongs to a user.")
    tid = trace_id or uuid.uuid4().hex
    s = _stack()
    existing = _safe(lambda: M.traces.find_one({"_id": tid}, {"uid": 1}), "trace lookup") if trace_id else None
    if existing and existing.get("uid") != uid:
        raise PermissionError("That trace belongs to someone else.")
    if not existing:
        doc = {"_id": tid, "uid": uid, "name": str(name)[:120], "status": "running", "started": time.time(),
               "totals": {"spans": 0, "errors": 0, "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0}}
        exp = _expiry()
        if exp:
            doc["expire_at"] = exp
        _safe(lambda: M.traces.update_one({"_id": tid}, {"$setOnInsert": doc}, upsert=True), "trace")
    s.append({"trace_id": tid, "span_id": None, "uid": uid})
    status = "ok"
    try:
        yield tid
    except BaseException:
        status = "error"
        raise
    finally:
        s.pop()
        if not trace_id:  # only the code that started a trace ends it
            _safe(lambda: M.traces.update_one({"_id": tid}, {"$set": {"status": status, "ended": time.time()}}), "trace end")


def end(trace_id, uid, status):
    """Mark a trace finished (used when the root task of a task tree reaches its final state)."""
    _safe(lambda: M.traces.update_one({"_id": trace_id, "uid": uid}, {"$set": {"status": status, "ended": time.time()}}),
          "trace end")


def attach(trace_id, uid, name="continued"):
    """Continue an existing trace of `uid` (e.g. in the worker that runs one of its tasks)."""
    return trace(uid, name, trace_id=trace_id)


def record(type_, name, summary=None, status="ok", error_class=None, **fields):
    """Write one finished span (an event with no duration of interest, e.g. an approval request)."""
    with span(type_, name, summary, **fields) as sp:
        if sp is not None and status != "ok":
            sp.fail(error_class or "error")


# ---- reading ------------------------------------------------------------------------------------------------------

METADATA_PROJECTION = {"payload": 0, "summary": 0}


def owner_trace(uid, trace_id):
    """The trace and its spans for its owner (summaries and sealed payloads included), or None for anyone else."""
    t = M.traces.find_one({"_id": trace_id, "uid": uid})
    if not t:
        return None
    spans = list(M.spans.find({"trace_id": trace_id, "uid": uid}).sort("start", ASCENDING))
    for sp in spans:
        sp["payload"] = unseal(sp["payload"], None) if sp.get("payload") else None
        sp.pop("_id", None)
    t["id"] = t.pop("_id")
    return {"trace": t, "spans": spans}


def metadata_trace(trace_id):
    """The trace's metadata for a Super Admin view: no summaries, no payloads, no content of any kind."""
    t = M.traces.find_one({"_id": trace_id}, {"name": 0})
    if not t:
        return None
    t["id"] = t.pop("_id")
    spans = list(M.spans.find({"trace_id": trace_id}, {**METADATA_PROJECTION, "_id": 0, "name": 0}).sort("start", ASCENDING))
    return {"trace": t, "spans": spans}
