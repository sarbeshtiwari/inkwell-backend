"""Accounts and sessions: users, login sessions, account deletion, startup checks and indexes."""

import base64
import hashlib
import hmac
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timezone

from flask import g, session
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from core.config import (
    ADMIN_EMAILS,
    DATA,
    MONGODB_URI,
    PRODUCTION,
    SECURE_COOKIES,
    SITE_URL,
    SQLITE_PATH,
)
from core.crypto import lookup_hash, seal, unseal
from core.db import M
from core.storage import CLOUD, drop_file
from core.workspace import DELETED


def check_config():
    """Refuse to run a public server with settings that would weaken it."""
    if not PRODUCTION:
        return
    problems = []
    if not SITE_URL.startswith("https://"):
        problems.append("SITE_URL must be your public https:// address (it's used for links, the sitemap and share cards; "
                        "without it the address comes from request headers, which visitors control).")
    if not ADMIN_EMAILS:
        print("[Inkwell] Note: ADMIN_EMAILS is not set, so nobody can see Inkwell's own site enquiries.", flush=True)
    if os.environ.get("SECRET_KEY") and len(os.environ["SECRET_KEY"]) < 32:
        problems.append("SECRET_KEY is too short. Use at least 32 random characters (python -c 'import secrets; print(secrets.token_urlsafe(48))').")
    if not SECURE_COOKIES:
        print("[Inkwell] WARNING: COOKIE_SECURE=0 on a public server: login cookies can travel over plain http.", flush=True)
    if problems:
        sys.exit("Inkwell won't start with these production settings:\n- " + "\n- ".join(problems))


def init_storage():
    check_config()
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        M.connect()
    except PyMongoError as e:
        sys.exit(f"Can't connect to MongoDB at {MONGODB_URI.split('@')[-1]}: {e}\n"
                 "Start MongoDB (brew services start mongodb-community) or set MONGODB_URI.")
    M.users.create_index("email_hash", unique=True)
    M.users.create_index("created")
    M.otps.create_index("expire_at", expireAfterSeconds=0)  # MongoDB removes expired codes itself
    M.kv.create_index("uid")
    M.send_log.create_index([("uid", ASCENDING), ("day", ASCENDING)])
    M.send_log.create_index([("uid", ASCENDING), ("rid", ASCENDING), ("ts", DESCENDING)])
    M.send_log.create_index([("uid", ASCENDING), ("ts", DESCENDING)])
    M.send_log.create_index("track", sparse=True)
    M.documents.create_index([("uid", ASCENDING), ("name_hash", ASCENDING)], unique=True)
    M.leads.create_index([("created", DESCENDING)])
    M.leads.create_index([("uid", ASCENDING), ("created", DESCENDING)], sparse=True)
    M.sessions.create_index("expire_at", expireAfterSeconds=0)
    M.site_images.create_index("uid")
    M.site_stats.create_index("uid")
    M.sessions.create_index("uid")
    M.ratelimits.create_index("expire_at", expireAfterSeconds=0)
    M.queue.create_index("uid")
    M.contact_shares.create_index([("to_uid", ASCENDING), ("created", DESCENDING)])
    M.contact_shares.create_index([("from_uid", ASCENDING), ("created", DESCENDING)])
    M.contact_shares.create_index("expire_at", expireAfterSeconds=0)  # unanswered shares lapse on their own
    from infra import audit, gateway, locks, operations, rbac, runtime, schedule, tasks, telemetry
    runtime.ensure_indexes()
    from infra import approvals
    approvals.ensure_indexes()
    telemetry.ensure_indexes()
    from core.db import ensure_index
    ensure_index(M.kill_switches, "active")
    locks.ensure_indexes()
    gateway.ensure_indexes()
    tasks.ensure_indexes()
    operations.ensure_indexes()
    schedule.ensure_indexes()
    audit.ensure_indexes()
    rbac.ensure_indexes()
    rbac.bootstrap()
    if SQLITE_PATH.exists() and M.users.estimated_document_count() == 0:
        from migrations import (
            migrate_sqlite,  # (late: migrations uses the layers above)
        )
        migrate_sqlite()


def new_user(email, name, uid=None, terms=None):
    uid = uid or uuid.uuid4().hex
    M.users.insert_one({"_id": uid, "email_hash": lookup_hash(email), "data": seal({"email": email, "name": name}),
                        "created": time.time(), "last_login": None,
                        "terms_accepted": {"version": terms, "at": time.time()} if terms else None})
    return uid


# Passwords: scrypt (Python's built-in), a random salt per user. Only the hash is stored; never the password.
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, maxmem=64 * 1024 * 1024, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def password_ok(stored, password):
    """True when `password` matches the stored hash. A missing hash still does the work, so timing doesn't reveal it."""
    try:
        _, n, r, p, salt, h = (stored or DUMMY_HASH).split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(h)), maxmem=64 * 1024 * 1024)
        return bool(stored) and hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


DUMMY_HASH = "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$" + base64.b64encode(bytes(32)).decode()


def set_password(uid, password):
    M.users.update_one({"_id": uid}, {"$set": {"pw": hash_password(password), "pw_set": time.time()}})


def password_of(uid):
    row = M.users.find_one({"_id": uid}, {"pw": 1})
    return (row or {}).get("pw")


def find_user(email=None, uid=None):
    row = M.users.find_one({"_id": uid} if uid else {"email_hash": lookup_hash(email)})
    if not row:
        return None
    return {"id": row["_id"], **unseal(row["data"], {})}


SESSION_DAYS = 30


SESSION_IDLE_DAYS = 7  # a login not used for a week expires


def start_session(uid):
    """Log in: a random token goes in the (signed, HttpOnly) cookie; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    M.sessions.insert_one({"_id": lookup_hash("session:" + token), "uid": uid, "created": now,
                           "expire_at": datetime.fromtimestamp(now + SESSION_DAYS * 86400, tz=timezone.utc)})
    session.clear()
    session.permanent = True
    session["sid"] = token


def current_uid():
    """The logged-in user for this request, or None. Cached per request."""
    if "uid" not in g:
        token = session.get("sid")
        row = M.sessions.find_one({"_id": lookup_hash("session:" + token)}) if isinstance(token, str) else None
        now = time.time()
        if row and now - row.get("seen", row["created"]) > SESSION_IDLE_DAYS * 86400:
            M.sessions.delete_one({"_id": row["_id"]})  # unused for too long: sign in again
            row = None
        elif row and now - row.get("seen", 0) > 3600:
            M.sessions.update_one({"_id": row["_id"]}, {"$set": {"seen": now}})
        g.uid = row["uid"] if row and find_user(uid=row["uid"]) else None
    return g.uid


def end_session():
    token = session.get("sid")
    if isinstance(token, str):
        M.sessions.delete_one({"_id": lookup_hash("session:" + token)})
    session.clear()


USER_COLLECTIONS = ("kv", "send_log", "documents", "notifications", "push_subs", "mail_index", "replies",
                    "site_images", "site_stats", "leads", "queue", "search", "operations", "locks", "schedules", "model_usage",
                    "tasks", "task_events", "traces", "spans", "checkpoints", "checkpoint_writes", "approvals",
                    "agent_runs")


def delete_user_data(uid):
    """MongoDB has no cascades: remove everything that belongs to one account."""
    DELETED[uid] = time.time()  # running jobs (campaigns, mail sync) stop writing for this account
    M.queue.update_many({"uid": uid, "status": {"$in": ["queued", "sending"]}}, {"$set": {"status": "cancelled"}})
    for f in M.files.find({"metadata.uid": uid}):
        drop_file(f._id)
    for row in [*M.documents.find({"uid": uid}), *M.wa_sessions.find({"_id": uid})]:
        if isinstance(row.get("file_id"), dict):
            drop_file(row["file_id"])
    if CLOUD:
        for row in M.site_images.find({"uid": uid, "public_id": {"$exists": True}}):
            try:
                CLOUD.destroy(row["public_id"], kind="image", delivery="upload")
            except Exception as e:
                print(f"[Inkwell] Couldn't remove image {row['public_id']}: {e}", flush=True)
    for name in USER_COLLECTIONS:
        getattr(M, name).delete_many({"uid": uid})
    M.wa_sessions.delete_one({"_id": uid})
    M.site.delete_many({"_id": {"$regex": "^portfolio-slug:"}, "uid": uid})
    M.sessions.delete_many({"uid": uid})
    M.contact_shares.delete_many({"$or": [{"from_uid": uid}, {"to_uid": uid}]})
    M.users.delete_one({"_id": uid})


def is_admin(uid):
    """Kept for existing callers: true for the Super Admin role (see infra/rbac.py)."""
    from infra import rbac
    return rbac.role_of(uid) == rbac.SUPER_ADMIN
