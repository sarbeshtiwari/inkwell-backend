"""Tamper-evident audit log: every administrative action and every role change is written here.

Each event stores actor, action, target, outcome, request/trace id and time, plus `seq`, `prev_hash` and `hash`.
`hash` is an HMAC over the event's fields and the previous event's hash, keyed with a key derived from SECRET_KEY,
so editing, deleting or reordering any stored event breaks the chain from that point on (`verify()` reports where).
Events are only ever inserted; there is no update or delete path. Details are passed through redaction first."""

import hashlib
import hmac
import json
import threading
import time
import uuid

from flask import g, has_request_context, request

from core.crypto import _derive
from core.db import M
from infra import redact

KEY = _derive("inkwell-audit-v1")
_LOCK = threading.Lock()  # within one process; across processes the unique `seq` index decides


def request_id():
    """The current request's id (from X-Request-Id if it's sane, else generated once per request)."""
    if not has_request_context():
        return ""
    if not getattr(g, "request_id", None):
        rid = request.headers.get("X-Request-Id", "")
        g.request_id = rid if (rid and len(rid) <= 64 and all(c.isalnum() or c in "-_." for c in rid)) else uuid.uuid4().hex
    return g.request_id


def _digest(doc, prev):
    body = json.dumps({k: doc[k] for k in ("seq", "at", "actor", "action", "target", "outcome", "request_id", "trace_id",
                                           "details")}, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(KEY, (prev + "|" + body).encode(), hashlib.sha256).hexdigest()


def ensure_indexes():
    M.audit_log.create_index("seq", unique=True)
    M.audit_log.create_index([("actor", 1), ("at", -1)])
    M.audit_log.create_index([("action", 1), ("at", -1)])


def record(action, actor, target="", outcome="ok", details=None, trace_id=""):
    """Append one event. `actor` is a uid (or "system"); `details` is redacted (secrets and personal data)."""
    for _ in range(8):  # a concurrent writer may take the same seq; retry with the next one
        with _LOCK:
            last = M.audit_log.find_one(sort=[("seq", -1)], projection={"seq": 1, "hash": 1})
            doc = {"seq": (last["seq"] + 1) if last else 1, "at": time.time(), "actor": str(actor or "anonymous"),
                   "action": str(action), "target": str(target or ""), "outcome": str(outcome),
                   "request_id": request_id(), "trace_id": str(trace_id or ""), "details": redact.pii(details or {})}
            doc["prev_hash"] = last["hash"] if last else ""
            doc["hash"] = _digest(doc, doc["prev_hash"])
            try:
                M.audit_log.insert_one(doc)
                return doc
            except Exception as e:  # DuplicateKeyError on seq: someone else wrote first
                if "duplicate key" not in str(e).lower():
                    raise
    raise RuntimeError("Couldn't write the audit event.")


def verify():
    """(ok, first_bad_seq or None, events_checked). Recomputes every hash in order."""
    prev, expected_seq, n = "", 1, 0
    for doc in M.audit_log.find({}, sort=[("seq", 1)]):
        n += 1
        if doc["seq"] != expected_seq or doc.get("prev_hash", "") != prev or doc.get("hash") != _digest(doc, prev):
            return False, doc.get("seq"), n
        prev, expected_seq = doc["hash"], expected_seq + 1
    return True, None, n
