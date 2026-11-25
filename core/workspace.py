"""Per-user workspace: each account's encrypted key-value data, documents and history."""

import copy
import random
import sys
import threading
import time
import uuid
from datetime import date, datetime

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from core.config import DEFAULT_SETTINGS, STARTER_TEMPLATE
from core.crypto import lookup_hash, seal, unseal
from core.db import M
from core.storage import drop_file, get_file, put_file
from core.tables import phone_key
from core.validation import Invalid
from infra import locks


def now_iso():
    """When a contact was added (local time, to the second); used by the "Added" filter."""
    return datetime.now().isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:10]


KV_MAX_BYTES = 12 * 1024 * 1024


DELETED = {}  # uid -> time: accounts deleted in this process (background writers check it)

# Records written only through the version protocol (Workspace.update / create / replace_versioned). Plain save() refuses
# them, and tests/test_stage2a_versioned.py fails if any code writes them another way.
VERSIONED_KEYS = frozenset({"templates"})
UPDATE_ATTEMPTS = 5


class VersionConflict(Invalid):
    """Another change to the same record kept landing first: nothing was written. Retryable (409 + Retry-After)."""

    retry_after = 2

    def __init__(self):
        super().__init__("This was changed somewhere else at the same moment. Try again.", status=409)


class VersionCorrupt(Invalid):
    """The stored version isn't a non-negative integer: refuse to guess (nothing is written)."""

    def __init__(self):
        super().__init__("This record can't be saved safely right now. Nothing was changed.", status=500)


_PURE = threading.local()


def forbid_in_update(what):
    """Called by side-effecting helpers (network, mail, notifications, other writes). Inside a Workspace.update
    callback, which may run several times, they are refused: the callback must only compute the new value."""
    if getattr(_PURE, "depth", 0):
        raise RuntimeError(f"{what} isn't allowed inside Workspace.update (the change may be computed again)")


def _version_of(row):
    """The stored version: 0 only when the field is genuinely missing (records written before versioning)."""
    if "version" not in row:
        return 0
    v = row["version"]
    if type(v) is not int or v < 0:  # bool, None, float, string... are corruption, never "0"
        raise VersionCorrupt()
    return v


class Workspace:
    def __init__(self, uid):
        self.uid = uid
        self.lock = locks.user_lock(uid)  # shared by every process (MongoDB lease), re-entrant within this one

    def load(self, key, default):
        row = M.kv.find_one({"_id": f"{self.uid}:{key}"})
        return unseal(row["data"], default, strict=True) if row else default

    def save(self, key, value):
        if key in VERSIONED_KEYS:
            raise RuntimeError(f"'{key}' is versioned: write it with Workspace.update / create / replace_versioned")
        forbid_in_update("Workspace.save")
        blob = seal(value)
        if len(blob) > KV_MAX_BYTES:  # MongoDB refuses documents over 16 MB; fail clearly, before that point
            raise Invalid("This is more data than one account can store here. Remove some items (old contacts, "
                          "columns or history) and try again.", status=413)
        if DELETED.get(self.uid) or not M.users.find_one({"_id": self.uid}, {"_id": 1}):
            return  # the account was deleted while a background job was still running: don't recreate its data
        doc = {"uid": self.uid, "key": key, "data": blob}
        # Under this account's lock, the write is a transaction that first re-checks the lease: a holder whose lease
        # ran out (paused, partitioned) can't overwrite anything. Without the lock it's a plain write, as before.
        locks.guarded_write(self.uid, lambda s: M.kv.replace_one({"_id": f"{self.uid}:{key}"}, doc, upsert=True, session=s))

    # ---- versioned records (optimistic concurrency, no lock) --------------------------------------------------

    def _check_size_and_owner(self, blob):
        if len(blob) > KV_MAX_BYTES:
            raise Invalid("This is more data than one account can store here. Remove some items (old contacts, "
                          "columns or history) and try again.", status=413)
        return not (DELETED.get(self.uid) or not M.users.find_one({"_id": self.uid}, {"_id": 1}))

    def load_versioned(self, key, default=None):
        """(value, version) as stored now; version 0 for a record written before versioning, None if missing."""
        row = M.kv.find_one({"_id": f"{self.uid}:{key}"})
        if row is None:
            return copy.deepcopy(default), None
        return unseal(row["data"], default, strict=True), _version_of(row)

    def update(self, key, fn, default=None, expected_version=None):
        """Change one record with optimistic concurrency and return the new value.

        Reads the record and its version, computes `fn(current)` (a deep copy, so fn may modify it), then writes only if
        the version is still the one read, incrementing it in the same atomic operation. If another writer got there
        first, it reads again and computes again from the fresh value: a stale result is never written. Up to
        UPDATE_ATTEMPTS attempts, each waiting a random part of the previous attempt's duration times the attempt number
        (so retries spread out at any network latency), then VersionConflict (409). A record that doesn't exist yet is created
        with an insert (a concurrent creation is detected by its duplicate key and retried as an update).

        `fn` must only compute: no network, mail, notifications or other writes (they are refused while it runs).
        Errors it raises (e.g. Invalid) propagate and nothing is written. `expected_version`: refuse with
        VersionConflict if the record has moved past the version a caller (e.g. a form) was based on."""
        forbid_in_update("A nested Workspace.update")
        _id = f"{self.uid}:{key}"
        last = 0.0
        for attempt in range(UPDATE_ATTEMPTS):
            if attempt:  # back off in proportion to how long an attempt takes (network latency), with jitter
                time.sleep(random.uniform(0.1, 1.0) * max(last, 0.01) * attempt)
            started = time.monotonic()
            row = M.kv.find_one({"_id": _id})
            version = None if row is None else _version_of(row)
            if expected_version is not None and version != expected_version:
                raise VersionConflict()
            current = copy.deepcopy(default) if row is None else unseal(row["data"], default, strict=True)
            _PURE.depth = getattr(_PURE, "depth", 0) + 1
            try:
                new = fn(copy.deepcopy(current))
            finally:
                _PURE.depth -= 1
            blob = seal(new)
            if not self._check_size_and_owner(blob):
                return new  # the account was deleted meanwhile: don't recreate its data (same as save)
            fields = {"uid": self.uid, "key": key, "data": blob}
            if row is None:
                try:
                    M.kv.insert_one({"_id": _id, **fields, "version": 1})
                    return new
                except DuplicateKeyError:
                    last = time.monotonic() - started
                    continue  # created concurrently: compute again from that record
            match = {"_id": _id, "version": version} if "version" in row else {"_id": _id, "version": {"$exists": False}}
            if M.kv.update_one(match, {"$set": fields, "$inc": {"version": 1}}).matched_count:
                return new
            last = time.monotonic() - started
        raise VersionConflict()

    def create(self, key, value):
        """Write a record only if it doesn't exist yet (version 1). True if created, False if it already existed."""
        blob = seal(value)
        if not self._check_size_and_owner(blob):
            return False
        try:
            M.kv.insert_one({"_id": f"{self.uid}:{key}", "uid": self.uid, "key": key, "data": blob, "version": 1})
            return True
        except DuplicateKeyError:
            return False

    def replace_versioned(self, key, value, expected_version=None):
        """Replace a versioned record's whole value (versioned write; with expected_version, refuses a stale form)."""
        return self.update(key, lambda _current: value, expected_version=expected_version)

    def settings(self):
        return {**DEFAULT_SETTINGS, **self.load("settings", {})}

    def update_settings(self, **changes):
        with self.lock:
            self.save("settings", {**self.settings(), **changes})

    def profile(self):
        return self.load("profile", {})

    # history
    def log(self, channel, to, name, status, detail="", rid=None, preview="", track=None, message_id=None,
            when=None, source=None):
        forbid_in_update("Workspace.log")
        when = when or datetime.now()
        entry = {"timestamp": when.isoformat(timespec="seconds"), "channel": channel, "to": to,
                 "name": name, "status": status, "detail": detail, "preview": preview[:280]}
        if source:
            entry["source"] = source
        doc = {"uid": self.uid, "rid": rid, "day": when.date().isoformat(), "ts": when.timestamp(), "data": seal(entry)}
        if track:
            doc.update(track=track, opens=0, first_open=None, last_open=None)
        if message_id:
            doc["mid"] = lookup_hash("mid:" + message_id.strip("<> ").lower())
            doc["to_hash"] = lookup_hash("to:" + to.lower())
        M.send_log.insert_one(doc)

    @staticmethod
    def _with_opens(row):
        e = unseal(row["data"])
        if e:
            e["rid"] = row.get("rid")
            e["replied"] = bool(row.get("replied_at"))
        if e and "track" in row:
            e.update(tracked=True, opens=row.get("opens", 0), first_open=row.get("first_open"),
                     last_open=row.get("last_open"))
        return e

    def contact_log(self, contact):
        """Messages sent to one contact (older entries without an id are matched by phone/email)."""
        keys = {k for k in ("+" + phone_key(contact, self.settings()["country_code"]) if contact.get("phone") else "",
                            contact.get("email", "").lower()) if k}
        rows = M.send_log.find({"uid": self.uid, "rid": {"$in": [contact["id"], None]}}).sort("ts", DESCENDING).limit(500)
        out = []
        for r in rows:
            e = self._with_opens(r)
            if e and (r.get("rid") == contact["id"] or str(e.get("to", "")).lower() in keys):
                out.append(e)
        return out

    def events(self, rid):
        return self.load(f"contact:{rid}", [])

    def add_event(self, rid, kind, text):
        with self.lock:
            events = self.events(rid)
            event = {"id": new_id(), "type": kind, "text": text, "at": datetime.now().isoformat(timespec="seconds")}
            events.insert(0, event)
            self.save(f"contact:{rid}", events[:500])
        return event

    def drop_events(self, rids):
        M.kv.delete_many({"_id": {"$in": [f"{self.uid}:contact:{r}" for r in rids]}})

    def history(self, limit=1000):
        rows = M.send_log.find({"uid": self.uid}).sort("ts", DESCENDING).limit(limit)
        return [e for e in (self._with_opens(r) for r in rows) if e]

    def sent_today(self):
        rows = M.send_log.find({"uid": self.uid, "day": date.today().isoformat()}, {"data": 1})
        return sum(1 for r in rows if (unseal(r["data"], {}) or {}).get("status") == "sent")

    def mark_opened(self, rid, when):
        with self.lock:
            rows = self.load("recipients", [])
            for r in rows:
                if r["id"] == rid and not r.get("email_opened"):
                    r["email_opened"] = when
                    self.save("recipients", rows)
                    return True
        return False

    def set_status(self, rid, channel, status):
        prefix = "wa" if channel == "whatsapp" else "email"
        with self.lock:
            rows = self.load("recipients", [])
            for r in rows:
                if r["id"] == rid:
                    if r.get(f"{prefix}_status") != "sent" or status in ("sent", "bounced"):
                        r[f"{prefix}_status"] = status
                    r[f"{prefix}_last"] = datetime.now().isoformat(timespec="seconds")
                    if status == "sent" and r.get("stage", "new") == "new":
                        r["stage"] = "contacted"
            self.save("recipients", rows)

    # documents (bytes in GridFS, encrypted; file name encrypted in the metadata record)
    def documents(self):
        rows = M.documents.find({"uid": self.uid}).sort("created", ASCENDING)
        return [{"name": unseal(r["meta"], {}).get("name", "file"), "size": r["size"]} for r in rows]

    def _doc_key(self, name):
        return lookup_hash(f"{self.uid}:{name}")

    def doc_bytes(self, name):
        row = M.documents.find_one({"uid": self.uid, "name_hash": self._doc_key(name)})
        return get_file(row["file_id"]) if row else None

    def save_doc(self, name, data):
        file_id = put_file(data, self.uid, "document")
        old = M.documents.find_one_and_update(
            {"uid": self.uid, "name_hash": self._doc_key(name)},
            {"$set": {"size": len(data), "meta": seal({"name": name}), "file_id": file_id, "created": time.time()}},
            upsert=True)
        if old:
            drop_file(old["file_id"])

    def delete_doc(self, name):
        row = M.documents.find_one_and_delete({"uid": self.uid, "name_hash": self._doc_key(name)})
        if row:
            drop_file(row["file_id"])


def seed_workspace(ws, name, email):
    ws.save("recipients", [])
    ws.create("templates", [{"id": new_id(), "name": "Introduction", "subject": "Quick introduction – {sender_name}",
                             "body": STARTER_TEMPLATE}])
    ws.save("profile", {"name": name, "phone": "", "email": email, "smtp_host": "smtp.gmail.com",
                        "smtp_port": 465, "smtp_user": email, "smtp_password": ""})
    ws.save("settings", {})


def public_profile(p):
    out = {k: v for k, v in p.items() if k != "smtp_password"}
    out["has_password"] = bool(p.get("smtp_password"))
    return out


def bridge_mod():
    return sys.modules.get("bridge")
