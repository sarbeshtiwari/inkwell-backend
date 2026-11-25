"""Tamper-evident audit log: every administrative action and every role change is written here.

Each event stores actor, action, target, outcome, request/trace id and time, plus `seq`, `prev_hash`, `hash` and
`key_id`. `hash` is an HMAC over the event's canonical JSON and the previous event's hash.

Writing (safe across processes):
- The next `seq` is taken by inserting under a unique index: exactly one writer wins each position, the others retry
  on the new tail, so `seq` has no gaps or duplicates and the chain can't fork (works without transactions, on Atlas
  and on a single local mongod). Then the `audit_meta` checkpoint is moved forward with a conditional update that
  never goes backwards. If a process dies between the two steps, `repair_head()` (run at startup) rolls the
  checkpoint forward over events that verify. Exact mechanism and crash cases: docs/audit-anchoring.md.
- Writes use majority write concern (durable). If an event still can't be written after the retry budget,
  `AuditError` is raised: callers doing security-sensitive work let it fail the request (fail closed), and an alert
  line is printed. Events are never silently dropped.

Verifying:
- `verify()` recomputes every hash in order and also checks the stored checkpoint, so deleting or truncating the
  newest events is detected (not just edits, deletions and reordering in the middle).

Keys (rotation without re-signing):
- Signing keys are versioned. `AUDIT_KEYS` (env) holds `id:secret` pairs, comma-separated; `AUDIT_KEY_ID` picks the
  one used for new events. Without them, key id "k0" is derived from SECRET_KEY (the behaviour before rotation was
  possible). Each event records its `key_id`; old events are verified with their own key and are never re-signed.
- Losing a key means events signed with it can no longer be verified (they are still readable). Keep every key that
  ever signed events, offline, separately from database backups.

Limitation: anyone holding a signing key *and* write access to the database could rebuild a valid-looking chain.
This log is tamper-evident against everyone else, not immutable. External anchoring (see docs/audit-anchoring.md)
is the planned defence and is not implemented yet."""

import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid

from flask import g, has_request_context, request
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

from core.crypto import _derive
from core.db import M
from infra import redact

FIELDS = ("seq", "at", "actor", "action", "target", "outcome", "request_id", "trace_id", "details", "key_id")
LEGACY_FIELDS = FIELDS[:-1]
META_ID = "head"
RETRY_SECONDS = 10.0


class AuditError(RuntimeError):
    """The audit event could not be stored. Security-sensitive operations must not continue."""


def _keys():
    """{key_id: key bytes} plus the active id. Legacy key k0 (from SECRET_KEY) is always available for verification."""
    keys = {"k0": _derive("inkwell-audit-v1")}
    for pair in filter(None, (p.strip() for p in os.environ.get("AUDIT_KEYS", "").split(","))):
        kid, _, secret = pair.partition(":")
        if kid and len(secret) >= 32:
            keys[kid.strip()] = hashlib.sha256(f"inkwell-audit:{secret.strip()}".encode()).digest()
    active = os.environ.get("AUDIT_KEY_ID", "").strip() or "k0"
    if active not in keys:
        raise AuditError(f"AUDIT_KEY_ID={active!r} has no matching entry in AUDIT_KEYS.")
    return keys, active


def request_id():
    """The current request's id (from X-Request-Id if it's sane, else generated once per request)."""
    if not has_request_context():
        return ""
    if not getattr(g, "request_id", None):
        rid = request.headers.get("X-Request-Id", "")
        g.request_id = rid if (rid and len(rid) <= 64 and all(c.isalnum() or c in "-_." for c in rid)) else uuid.uuid4().hex
    return g.request_id


def canonical(doc):
    """Stable bytes for hashing: sorted keys, no whitespace, UTF-8, floats as repr (exact round-trip).
    Events written before key rotation existed have no `key_id` and were signed without it."""
    fields = FIELDS if "key_id" in doc else LEGACY_FIELDS
    return json.dumps({k: doc.get(k) for k in fields}, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def _digest(doc, prev, key):
    return hmac.new(key, prev.encode() + b"|" + canonical(doc), hashlib.sha256).hexdigest()


def ensure_indexes():
    M.audit_log.create_index("seq", unique=True)
    M.audit_log.create_index([("actor", 1), ("at", -1)])
    M.audit_log.create_index([("action", 1), ("at", -1)])
    M.audit_log.create_index("details.outbox_id", sparse=True)
    if not M.audit_meta.find_one({"_id": META_ID}):
        last = M.audit_log.find_one(sort=[("seq", -1)], projection={"seq": 1, "hash": 1})
        try:  # adopt events written before the checkpoint existed
            M.audit_meta.insert_one({"_id": META_ID, "seq": last["seq"] if last else 0, "hash": last["hash"] if last else ""})
        except PyMongoError:
            pass
    repair_head()


def _write(doc_base):
    """Insert the next event. Atomic without transactions (works on Atlas and on a single local mongod):
    the unique `seq` index lets exactly one writer take each position; losers retry on the new tail."""
    keys, active = _keys()
    wc = WriteConcern(w="majority", j=True)
    log = M.audit_log.with_options(write_concern=wc)
    last = M.audit_log.find_one(sort=[("seq", -1)], projection={"seq": 1, "hash": 1})
    doc = {**doc_base, "seq": (last["seq"] + 1) if last else 1, "key_id": active, "prev_hash": last["hash"] if last else ""}
    doc["hash"] = _digest(doc, doc["prev_hash"], keys[active])
    log.insert_one(doc)  # DuplicateKeyError if another writer took this seq: caller retries
    _advance_head(doc["seq"], doc["hash"], doc["at"])
    return doc


def _advance_head(seq, h, at):
    """Move the checkpoint forward to (seq, hash); never backwards, so concurrent writers can't regress it."""
    wc = WriteConcern(w="majority", j=True)
    M.audit_meta.with_options(write_concern=wc).update_one(
        {"_id": META_ID, "seq": {"$lt": seq}}, {"$set": {"seq": seq, "hash": h, "at": at}})


def repair_head():
    """After a crash between an event's insert and its checkpoint update, the checkpoint lags. Roll it forward over
    events whose hashes verify; it never moves backwards and never accepts an event that fails verification."""
    keys, _ = _keys()
    head = M.audit_meta.find_one({"_id": META_ID}) or {"seq": 0, "hash": ""}
    prev, seq = head.get("hash", ""), head.get("seq", 0)
    for doc in M.audit_log.find({"seq": {"$gt": seq}}, sort=[("seq", 1)]):
        key = keys.get(doc.get("key_id", "k0"))
        if key is None or doc["seq"] != seq + 1 or doc.get("prev_hash") != prev or doc.get("hash") != _digest(doc, prev, key):
            break
        _advance_head(doc["seq"], doc["hash"], doc["at"])
        prev, seq = doc["hash"], doc["seq"]
    return seq


def alert(message):
    """Operational alert: a CRITICAL log line (Render log stream) and, when Sentry is configured, a Sentry event."""
    text = redact.secrets(f"[Inkwell] ALERT: {message}")
    logging.getLogger("inkwell.alert").critical(text)
    print(text, flush=True)
    try:
        import sentry_sdk
        if sentry_sdk.get_client().is_active():
            sentry_sdk.capture_message(text, level="fatal")
    except Exception as e:  # noqa: BLE001 - an alert must never fail the caller; the log line above already went out
        print(f"[Inkwell] Sentry alert not sent ({type(e).__name__})", flush=True)


def record(action, actor, target="", outcome="ok", details=None, trace_id=""):
    """Append one event; returns it. Raises AuditError if it can't be stored (never drops events silently)."""
    base = {"at": time.time(), "actor": str(actor or "anonymous"), "action": str(action), "target": str(target or ""),
            "outcome": str(outcome), "request_id": request_id(), "trace_id": str(trace_id or ""),
            "details": redact.pii(details or {})}
    deadline, delay, err = time.monotonic() + RETRY_SECONDS, 0.005, None
    while time.monotonic() < deadline:
        try:
            return _write(base)
        except AuditError:
            raise
        except DuplicateKeyError as e:  # another writer took this seq: retry at once on the new tail
            err = e
            time.sleep(random.uniform(0, delay))
            delay = min(delay * 1.5, 0.05)
        except PyMongoError as e:  # transient: failover, network blip
            err = e
            time.sleep(random.uniform(0.05, 0.3))
    alert(f"audit event {action!r} by {actor!r} could not be stored: {err!r}")
    raise AuditError("The audit log is unavailable, so this action was not carried out.") from err


def verify():
    """(ok, first_bad_seq or None, events_checked). Recomputes every hash and checks the head checkpoint."""
    try:
        keys, _ = _keys()
    except AuditError:
        return False, None, 0
    prev, expected, n = "", 1, 0
    for doc in M.audit_log.find({}, sort=[("seq", 1)]):
        n += 1
        key = keys.get(doc.get("key_id", "k0"))
        if (key is None or doc["seq"] != expected or doc.get("prev_hash", "") != prev
                or doc.get("hash") != _digest(doc, prev, key)):
            return False, doc.get("seq"), n
        prev, expected = doc["hash"], expected + 1
    head = M.audit_meta.find_one({"_id": META_ID}) or {"seq": 0, "hash": ""}
    if head.get("seq", 0) != expected - 1 or head.get("hash", "") != prev:
        return False, expected, n  # newest events missing (truncated) or checkpoint altered
    return True, None, n


def checkpoint():
    """The current head {seq, hash, at}: what external anchoring exports."""
    head = M.audit_meta.find_one({"_id": META_ID}, {"_id": 0}) or {"seq": 0, "hash": ""}
    return head
