# Adapted from Morrow (MIT, (c) 2026 Morrow contributors): common/learning_memory.py (stamp, record). See ../LICENSE-morrow.
"""The in-memory part of Morrow's learning record. Reachout stores the record per user in its workspace
(features/learning/progress.py) instead of a learning.json file, so the file locking is not needed here."""
import datetime as dt


def empty():
    return {"version": 1, "course": {}, "documents": {}, "plan": {"done": [], "marked": {}}, "history": [],
            "dsa": {"days": {}}, "reviews": {}, "review_log": []}


def stamp():
    return dt.datetime.now().astimezone().isoformat(timespec="microseconds")


def record(data, kind, identifier, title, done, *, at=None, day=None):
    at = at or stamp()
    bucket = data["course" if kind == "course" else "documents"]
    previous = bucket.get(identifier, {})
    if bool(previous.get("done")) == done:
        return
    bucket[identifier] = {"done": done, "title": title, "updated_at": at,
                          "completed_at": at if done else None}
    data["history"].append({"at": at, "kind": kind, "id": identifier, "title": title,
                            "action": "completed" if done else "reopened"})
    if day is not None:
        plan = data["plan"]
        days = set(plan.get("done", []))
        days.add(day) if done else days.discard(day)
        plan["done"] = sorted(days)
        plan.setdefault("start", at[:10])
        marked = plan.setdefault("marked", {})
        if done:
            marked[at[:10]] = day
        else:
            plan["marked"] = {date: n for date, n in marked.items() if n != day}
