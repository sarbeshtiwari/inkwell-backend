"""Per-user workspace: each account's encrypted key-value data, documents and history."""

import sys
import threading
import time
import uuid
from datetime import date, datetime

from pymongo import ASCENDING, DESCENDING

from core.config import DEFAULT_SETTINGS, STARTER_TEMPLATE
from core.crypto import lookup_hash, seal, unseal
from core.db import M
from core.storage import drop_file, get_file, put_file
from core.tables import phone_key
from core.validation import Invalid

LOCKS = {}


def now_iso():
    """When a contact was added (local time, to the second); used by the "Added" filter."""
    return datetime.now().isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:10]


KV_MAX_BYTES = 12 * 1024 * 1024


DELETED = {}  # uid -> time: accounts deleted in this process (background writers check it)


class Workspace:
    def __init__(self, uid):
        self.uid = uid
        self.lock = LOCKS.setdefault(uid, threading.RLock())

    def load(self, key, default):
        row = M.kv.find_one({"_id": f"{self.uid}:{key}"})
        return unseal(row["data"], default, strict=True) if row else default

    def save(self, key, value):
        blob = seal(value)
        if len(blob) > KV_MAX_BYTES:  # MongoDB refuses documents over 16 MB; fail clearly, before that point
            raise Invalid("This is more data than one account can store here. Remove some items (old contacts, "
                          "columns or history) and try again.", status=413)
        if DELETED.get(self.uid) or not M.users.find_one({"_id": self.uid}, {"_id": 1}):
            return  # the account was deleted while a background job was still running: don't recreate its data
        M.kv.replace_one({"_id": f"{self.uid}:{key}"}, {"uid": self.uid, "key": key, "data": blob}, upsert=True)

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
    ws.save("templates", [{"id": new_id(), "name": "Introduction", "subject": "Quick introduction – {sender_name}",
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
