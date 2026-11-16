"""One-off migration of the old SQLite store into MongoDB."""

import sqlite3
from datetime import datetime

from core.config import SQLITE_PATH
from core.crypto import fernet, unseal
from core.db import M
from core.storage import put_file
from core.whatsapp_store import store_wa_blob


def migrate_sqlite():
    """Copy the previous SQLite store into MongoDB. Encrypted values are copied as-is (same key)."""
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    counts = {}
    try:
        users = src.execute("SELECT * FROM users").fetchall() if "users" in tables else []
        for u in users:
            M.users.insert_one({"_id": u["id"], "email_hash": u["email_hash"], "data": u["data"], "created": u["created"],
                                "last_login": u["last_login"]})
        counts["accounts"] = len(users)
        if "kv" in tables:
            rows = src.execute("SELECT * FROM kv").fetchall()
            if rows:
                M.kv.insert_many([{"_id": f"{r['uid']}:{r['key']}", "uid": r["uid"], "key": r["key"], "data": r["data"]}
                                  for r in rows])
        if "send_log" in tables:
            cols = {r[1] for r in src.execute("PRAGMA table_info(send_log)")}
            rows = src.execute("SELECT * FROM send_log ORDER BY id").fetchall()
            if rows:
                def when(r):
                    stamp = (unseal(r["data"], {}) or {}).get("timestamp") or r["day"]
                    try:
                        return datetime.fromisoformat(stamp).timestamp()
                    except ValueError:
                        return 0
                M.send_log.insert_many([{"uid": r["uid"], "rid": r["rid"] if "rid" in cols else None, "day": r["day"],
                                         "ts": when(r), "data": r["data"]} for r in rows])
            counts["messages"] = len(rows)
        if "documents" in tables:
            rows = src.execute("SELECT * FROM documents").fetchall()
            for r in rows:
                data = fernet.decrypt(r["data"])
                M.documents.insert_one({"uid": r["uid"], "name_hash": r["name_hash"], "size": r["size"], "meta": r["meta"],
                                        "file_id": put_file(data, r["uid"], "document"), "created": r["created"]})
            counts["files"] = len(rows)
        if "wa_sessions" in tables:
            for r in src.execute("SELECT * FROM wa_sessions").fetchall():
                store_wa_blob(r["uid"], fernet.decrypt(r["data"]))
        if "leads" in tables:
            rows = src.execute("SELECT * FROM leads").fetchall()
            if rows:
                M.leads.insert_many([{"_id": r["id"], "created": r["created"], "status": r["status"], "data": r["data"]}
                                     for r in rows])
            counts["enquiries"] = len(rows)
    except Exception:
        # Leave MongoDB empty so the copy runs again cleanly next start; the SQLite file is untouched.
        for name in ("users", "kv", "send_log", "documents", "wa_sessions", "leads", "files.files", "files.chunks"):
            M.db[name].delete_many({})
        raise
    finally:
        src.close()
    backup = SQLITE_PATH.with_name("app.db.migrated-to-mongodb")
    SQLITE_PATH.rename(backup)
    for extra in ("-wal", "-shm"):
        side = SQLITE_PATH.with_name("app.db" + extra)
        if side.exists():
            side.rename(backup.with_name(backup.name + extra))
    print("Copied into MongoDB: " + ", ".join(f"{v} {k}" for k, v in counts.items())
          + f". The old file was kept as {backup.name}.", flush=True)
