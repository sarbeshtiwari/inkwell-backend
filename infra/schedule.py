"""Shared schedule: "is this due?" decided once for every process, in MongoDB (`schedules`).

A schedule is one document `{_id: key, next_at, last_at, uid?}`. `claim(key, every)` atomically takes the current
slot: it succeeds only when `next_at` has passed (server clock) and moves `next_at` forward, so however many
processes ask at the same moment, exactly one gets each slot. It is also where "last run" times live, so they survive
restarts and are shared (instead of each process keeping its own).

Keys: `g:<name>` for app-wide jobs (the bridge.every loops), `u:<uid>:<name>` for per-account work (reply checks,
bounce checks, job-alert scans, daily digests). A missing document is due.
"""

import datetime as dt
import re

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from core.db import M

EPOCH = dt.datetime(1970, 1, 1)
NAME_OK = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")


def key(name, uid=None):
    if not NAME_OK.match(name or ""):
        raise ValueError(f"Bad schedule name: {name!r}")
    return f"g:{name}" if uid is None else f"u:{uid}:{name}"


def ensure_indexes():
    M.schedules.create_index("uid", sparse=True)
    M.schedules.create_index("expire_at", expireAfterSeconds=0)  # clean-up of once() markers only


def claim(name, every, uid=None):
    """Take the slot if it's due: True for exactly one caller per slot. The next slot opens `every` seconds later."""
    k, ms = key(name, uid), int(every * 1000)
    update = [{"$set": {"next_at": {"$add": ["$$NOW", ms]}, "last_at": "$$NOW", **({"uid": uid} if uid else {})}}]
    due = {"_id": k, "$expr": {"$lte": ["$next_at", "$$NOW"]}}
    if M.schedules.find_one_and_update(due, update, return_document=ReturnDocument.AFTER):
        return True
    try:  # never scheduled before: create it due, then compete for it like everyone else
        M.schedules.insert_one({"_id": k, "next_at": EPOCH, **({"uid": uid} if uid else {})})
    except DuplicateKeyError:
        return False
    return bool(M.schedules.find_one_and_update(due, update, return_document=ReturnDocument.AFTER))


def soon(name, seconds, uid=None):
    """Make the next slot open within `seconds` (never later than already planned)."""
    at = {"$add": ["$$NOW", int(seconds * 1000)]}
    M.schedules.update_one({"_id": key(name, uid)},
                           [{"$set": {"next_at": {"$min": [{"$ifNull": ["$next_at", at]}, at]}, **({"uid": uid} if uid else {})}}],
                           upsert=True)


def postpone(name, seconds, uid=None):
    """Push the next slot to at least `seconds` from now (e.g. a manual run just happened), and record it as run."""
    M.schedules.update_one({"_id": key(name, uid)},
                           [{"$set": {"next_at": {"$max": [{"$ifNull": ["$next_at", EPOCH]}, {"$add": ["$$NOW", int(seconds * 1000)]}]},
                                      "last_at": "$$NOW", **({"uid": uid} if uid else {})}}], upsert=True)


def once(name, uid=None, keep_days=40):
    """True the first time this exact key is claimed, False after that (e.g. "weekly digest for week 48"). Use it
    *before* an external action: at most once, even across processes and crashes. Removed after keep_days."""
    now = dt.datetime.now(dt.timezone.utc)
    try:
        M.schedules.insert_one({"_id": key(name, uid), "next_at": dt.datetime.max, "last_at": now,
                                "expire_at": now + dt.timedelta(days=keep_days), **({"uid": uid} if uid else {})})
        return True
    except DuplicateKeyError:
        return False


def last_at(name, uid=None):
    doc = M.schedules.find_one({"_id": key(name, uid)}, {"last_at": 1})
    return (doc or {}).get("last_at")
