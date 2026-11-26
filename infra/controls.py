"""Kill switches: stop new agent/task executions globally, for one agent, or for one user.

One document in `kill_switches` per scope: `_id` is "global", "agent:<agent_id>" or "user:<uid>", with `active`,
`reason`, `by` and `at`. Setting or clearing one needs `admin.control_agents` and is written to the audit log
*before* it takes effect (if the audit can't be stored, nothing changes).

What a switch does (and doesn't):
- **New executions:** infra/tasks.py won't claim a task while a switch covers it (global, its agent, or its user).
  Covered tasks stay queued and run once the switch is cleared.
- **Running tasks:** with `cancel_running=True`, every running task the switch covers gets `cancel_requested` and
  stops at its next checkpoint (cooperative, as all cancellation here). A running handler also sees the switch at its
  next checkpoint() and stops then, so a switch never waits for a long task to end on its own.
- It does **not** kill a process, and it can't stop a step that is already in the middle of an external call; that
  step finishes (or fails) and the task stops at the next checkpoint after it.

If MongoDB can't be read, claim() fails (no task starts without the check)."""

import re
import time

from core.db import M
from core.validation import Invalid
from infra import audit, rbac

GLOBAL = "global"
AGENT_OK = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
UID_OK = re.compile(r"^[\w-]{1,64}$")


def scope_id(agent_id=None, uid=None):
    if agent_id and uid:
        raise ValueError("A switch covers everything, one agent, or one user: not both.")
    if agent_id:
        if not AGENT_OK.match(agent_id):
            raise Invalid("Unknown agent.", "agent_id")
        return f"agent:{agent_id}"
    if uid:
        if not UID_OK.match(uid):
            raise Invalid("Unknown user.", "uid")
        return f"user:{uid}"
    return GLOBAL


def active():
    """{'global': bool, 'agents': set, 'users': set} of the switches in force right now."""
    out = {"global": False, "agents": set(), "users": set()}
    for d in M.kill_switches.find({"active": True}, {"_id": 1}):
        sid = d["_id"]
        if sid == GLOBAL:
            out["global"] = True
        elif sid.startswith("agent:"):
            out["agents"].add(sid[6:])
        elif sid.startswith("user:"):
            out["users"].add(sid[5:])
    return out


def blocks(switches, uid, agent_id):
    return switches["global"] or uid in switches["users"] or (agent_id is not None and agent_id in switches["agents"])


def stopped(uid, agent_id=None):
    """True when a switch in force covers this user's (or this agent's) executions."""
    return blocks(active(), uid, agent_id)


def set_switch(actor_uid, *, agent_id=None, uid=None, reason="", cancel_running=False):
    """Turn a switch on (needs admin.control_agents; audited first). Returns how many running tasks were asked to stop."""
    sid = scope_id(agent_id, uid)
    rbac.check(actor_uid, "admin.control_agents", target=f"kill_switch:{sid}")
    reason = str(reason or "").strip()[:300]
    if len(reason) < 5:
        raise Invalid("Give a short reason for the stop.", "reason")
    audit.record("kill_switch.set", actor_uid, target=sid, details={"reason": reason, "cancel_running": bool(cancel_running)})
    M.kill_switches.update_one({"_id": sid}, {"$set": {"active": True, "reason": reason, "by": actor_uid, "at": time.time()}},
                               upsert=True)
    if not cancel_running:
        return 0
    from infra import tasks
    q = {"status": tasks.RUNNING, "cancel_requested": False}
    if agent_id:
        q["agent_id"] = agent_id
    elif uid:
        q["uid"] = uid
    return M.tasks.update_many(q, {"$set": {"cancel_requested": True, "updated_at": tasks._now()}}).modified_count


def clear_switch(actor_uid, *, agent_id=None, uid=None, reason=""):
    sid = scope_id(agent_id, uid)
    rbac.check(actor_uid, "admin.control_agents", target=f"kill_switch:{sid}")
    audit.record("kill_switch.clear", actor_uid, target=sid, details={"reason": str(reason or "").strip()[:300]})
    M.kill_switches.update_one({"_id": sid}, {"$set": {"active": False, "cleared_by": actor_uid, "cleared_at": time.time()}})


def listing():
    """Metadata of every switch (for the admin view): scope, state, reason, who and when."""
    return [{"scope": d["_id"], "active": bool(d.get("active")), "reason": d.get("reason", ""), "by": d.get("by"),
             "at": d.get("at"), "cleared_at": d.get("cleared_at")} for d in M.kill_switches.find({}).sort("_id", 1)]
