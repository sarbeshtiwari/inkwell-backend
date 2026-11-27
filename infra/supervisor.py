"""The Supervisor: an agent that watches every other agent, and acts within narrow limits. Above it: the Super Admin.

Every SUPERVISOR_EVERY_SECONDS (300) it runs as a task owned by the system account ("system"):

1. **Observe** (tool supervision.health): metadata only, across all users — per agent: runs, failures and their
   error classes, average duration; tasks stuck waiting on delegated work; dead letters; queue depth and the age of
   the oldest queued task; model errors and budget refusals; pending approvals; active kill switches. No user content
   (no inputs, results, prompts or names).
2. **Assess**: rules turn the figures into findings, each with the actions it allows (pause an agent that keeps
   failing; cancel a task stuck for hours; alert the Super Admin). With a model configured, the model reviews the
   findings and picks among those proposed actions; it can't invent others, and its answer is validated (a bad answer
   falls back to the rules' own choice).
3. **Act**, only in mode "act" (set by the Super Admin; the default is "observe"): pause an agent (an agent kill
   switch, audited with actor "agent:supervisor"), cancel a stuck task (cooperative), alert the Super Admins. It can
   never: pause itself, stop everything (global switch), stop a user, clear a switch, change models or budgets, or
   decide an approval. Only the Super Admin can undo what it did.
4. **Report**: every run stores a metadata-only report in `supervisor_reports` (latest 500 kept) and notifies the
   Super Admins when there is anything to look at.

The Super Admin controls it like any agent: the agent:supervisor kill switch stops it; mode observe/act chooses
whether it may act; thresholds are settings (environment variables below)."""

import datetime as dt
import os
import time
from typing import Annotated, TypedDict
import operator

from langgraph.graph import END, START, StateGraph

from core.db import M, ensure_index
from core.validation import Invalid
from infra import audit, controls, rbac, runtime, tasks, tools
from infra.tools import READ, WRITE_INTERNAL, tool

SYSTEM_UID = "system"
AGENT_ID = "supervisor"
MODES = ("observe", "act")


def _num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


EVERY_SECONDS = _num("SUPERVISOR_EVERY_SECONDS", 300)
WINDOW_MINUTES = _num("SUPERVISOR_WINDOW_MINUTES", 60)
FAIL_RATE = _num("SUPERVISOR_FAIL_RATE", 0.5)        # share of failed runs that makes an agent "failing"
MIN_RUNS = _num("SUPERVISOR_MIN_RUNS", 5)            # runs needed before a failure rate counts
STUCK_HOURS = _num("SUPERVISOR_STUCK_HOURS", 6)      # a task waiting on its children this long is stuck
QUEUE_AGE_MINUTES = _num("SUPERVISOR_QUEUE_AGE_MINUTES", 30)
KEEP_REPORTS = 500


def ensure_indexes():
    ensure_index(M.supervisor_reports, [("at", -1)])


def _now():
    return dt.datetime.now(dt.timezone.utc)


# ---- settings (Super Admin) -------------------------------------------------------------------------------------

def mode():
    d = M.supervisor_settings.find_one({"_id": "global"}) or {}
    return d.get("mode") if d.get("mode") in MODES else "observe"


def set_mode(actor_uid, new_mode, reason=""):
    rbac.check(actor_uid, "admin.control_agents", target="supervisor")
    if new_mode not in MODES:
        raise Invalid("Choose observe or act.", "mode")
    reason = str(reason or "").strip()[:300]
    if len(reason) < 5:
        raise Invalid("Give a short reason for the change.", "reason")
    audit.record("supervisor.mode", actor_uid, target="supervisor", details={"mode": new_mode, "reason": reason})
    M.supervisor_settings.update_one({"_id": "global"}, {"$set": {"mode": new_mode, "by": actor_uid, "at": time.time()}},
                                     upsert=True)
    return new_mode


def reports(limit=20):
    return [{**{k: v for k, v in r.items() if k != "_id"}, "id": r["_id"]}
            for r in M.supervisor_reports.find({}).sort("at", -1).limit(max(1, min(int(limit), 100)))]


# ---- tools (granted to the Supervisor only) ---------------------------------------------------------------------

def _group(coll, match, key, extra=None):
    pipe = [{"$match": match}, {"$group": {"_id": key, "count": {"$sum": 1}, **(extra or {})}}]
    return list(coll.aggregate(pipe))


@tool("supervision.health", "1", "Health of every agent across all users, as figures only (no user content): runs, "
      "failures and error classes, stuck and dead-letter tasks, queue age, model errors, budget refusals, pending "
      "approvals, active kill switches.",
      {"type": "object", "additionalProperties": False, "properties": {
          "window_minutes": {"type": "number", "minimum": 5, "maximum": 1440}}},
      {"type": "object", "required": ["agents", "stuck", "queue", "models", "switches"]}, READ)
def health(ctx, window_minutes=None):
    since = _now() - dt.timedelta(minutes=window_minutes or WINDOW_MINUTES)
    agents = {}
    for r in _group(M.agent_runs, {"started": {"$gte": since}, "agent_id": {"$ne": AGENT_ID}},
                    {"agent_id": "$agent_id", "outcome": "$outcome"}):
        a = agents.setdefault(r["_id"]["agent_id"], {"runs": 0, "failed": 0, "outcomes": {}})
        a["runs"] += r["count"]
        a["outcomes"][r["_id"]["outcome"]] = r["count"]
        if r["_id"]["outcome"] == "error":
            a["failed"] += r["count"]
    for r in _group(M.tasks, {"updated_at": {"$gte": since}, "status": {"$in": ["failed", "dead_letter"]},
                              "agent_id": {"$nin": [None, AGENT_ID]}},
                    {"agent_id": "$agent_id", "error_class": "$error_class"}):
        a = agents.setdefault(r["_id"]["agent_id"], {"runs": 0, "failed": 0, "outcomes": {}})
        a.setdefault("error_classes", {})[str(r["_id"]["error_class"])] = r["count"]
    stuck_before = _now() - dt.timedelta(hours=STUCK_HOURS)
    stuck = [{"task_id": d["_id"], "agent_id": d.get("agent_id"), "status": d["status"],
              "since": d["updated_at"].isoformat()} for d in M.tasks.find(
        {"status": "waiting_children", "updated_at": {"$lte": stuck_before}},
        {"agent_id": 1, "status": 1, "updated_at": 1}).limit(50)]
    oldest = M.tasks.find_one({"status": "queued", "run_after": {"$lte": _now()}}, {"created_at": 1}, sort=[("created_at", 1)])
    queue = {"queued": M.tasks.count_documents({"status": "queued"}),
             "dead_letter_recent": M.tasks.count_documents({"status": "dead_letter", "updated_at": {"$gte": since}}),
             "oldest_minutes": round((_now() - oldest["created_at"]).total_seconds() / 60, 1) if oldest else 0,
             "waiting_approval": M.tasks.count_documents({"status": "waiting_approval"})}
    models = {"calls": 0, "errors": {}, "refused_by_budget": 0}
    for r in _group(M.model_usage, {"at": {"$gte": since.timestamp()}}, {"status": "$status", "error_class": "$error_class"}):
        models["calls"] += r["count"]
        if r["_id"]["status"] == "refused":
            models["refused_by_budget"] += r["count"]
        elif r["_id"]["status"] == "error":
            models["errors"][str(r["_id"]["error_class"])] = r["count"]
    sw = controls.active()
    return {"agents": agents, "stuck": stuck, "queue": queue, "models": models,
            "switches": {"global": sw["global"], "agents": sorted(sw["agents"]), "users": len(sw["users"])}}


def _must_act():
    if mode() != "act":
        raise tools.ToolDenied("The Supervisor is in observe mode: it reports, but doesn't act.")


@tool("supervision.pause_agent", "1", "Pause one agent for every user (an agent kill switch). Only the Super Admin "
      "can resume it.",
      {"type": "object", "additionalProperties": False, "required": ["agent_id", "reason"], "properties": {
          "agent_id": {"type": "string", "pattern": "[a-z][a-z0-9_]{0,63}"},
          "reason": {"type": "string", "maxLength": 300}}},
      {"type": "object", "required": ["paused"]}, WRITE_INTERNAL)
def pause_agent(ctx, agent_id, reason):
    _must_act()
    if agent_id == AGENT_ID:
        raise tools.ToolDenied("The Supervisor can't pause itself.")
    if agent_id not in runtime.AGENTS:
        raise tools.InvalidArguments("Unknown agent.")
    if agent_id in controls.active()["agents"]:
        return {"paused": False, "detail": "already paused"}
    audit.record("kill_switch.set", f"agent:{AGENT_ID}", target=f"agent:{agent_id}",
                 details={"reason": reason[:300], "cancel_running": False, "by": "supervisor"})
    M.kill_switches.update_one({"_id": f"agent:{agent_id}"}, {"$set": {
        "active": True, "reason": f"Supervisor: {reason[:280]}", "by": f"agent:{AGENT_ID}", "at": time.time()}}, upsert=True)
    return {"paused": True}


@tool("supervision.cancel_task", "1", f"Cancel one agent task that has been stuck waiting for its sub-tasks for more "
      f"than {STUCK_HOURS:g} hours (cooperative: it stops at its next checkpoint).",
      {"type": "object", "additionalProperties": False, "required": ["task_id", "reason"], "properties": {
          "task_id": {"type": "string", "pattern": "[0-9a-f]{32}"}, "reason": {"type": "string", "maxLength": 300}}},
      {"type": "object", "required": ["cancelled"]}, WRITE_INTERNAL)
def cancel_task(ctx, task_id, reason):
    _must_act()
    d = M.tasks.find_one({"_id": task_id}, {"uid": 1, "status": 1, "updated_at": 1, "agent_id": 1})
    stuck = d and d["status"] == "waiting_children" and d["updated_at"] <= _now() - dt.timedelta(hours=STUCK_HOURS)
    if not stuck or d.get("agent_id") == AGENT_ID:
        raise tools.ToolDenied("The Supervisor only cancels tasks that are stuck.")
    audit.record("task.cancel", f"agent:{AGENT_ID}", target=f"task:{task_id}", details={"reason": reason[:300]})
    tasks.cancel(d["uid"], task_id)
    return {"cancelled": True}


@tool("supervision.alert", "1", "Tell the Super Admins about something that needs a human (in-app notification).",
      {"type": "object", "additionalProperties": False, "required": ["title", "body"], "properties": {
          "title": {"type": "string", "maxLength": 120}, "body": {"type": "string", "maxLength": 1000}}},
      {"type": "object", "required": ["notified"]}, WRITE_INTERNAL)
def alert(ctx, title, body):
    from features.notify import notify
    admins = [r["_id"] for r in M.roles.find({"role": rbac.SUPER_ADMIN}, {"_id": 1})]
    for uid in admins:
        notify(uid, title, body, "/app/agents", "info")
    return {"notified": len(admins)}


# ---- the agent --------------------------------------------------------------------------------------------------

class State(TypedDict, total=False):
    health: dict
    findings: list        # [{"kind", "subject", "detail", "actions": [proposed action dicts]}]
    chosen: list          # actions to take
    done: Annotated[list, operator.add]
    notes: Annotated[list, operator.add]


def observe(state, config):
    runtime.checkpoint(config)
    return {"health": tools.call(runtime.ctx(config), "supervision.health", {})}


def _rules(h):
    findings = []
    for agent_id, a in sorted(h["agents"].items()):
        if a["runs"] >= MIN_RUNS and a["failed"] / a["runs"] >= FAIL_RATE and agent_id not in h["switches"]["agents"]:
            detail = f"{a['failed']} of {a['runs']} runs failed in the last {WINDOW_MINUTES:g} minutes"
            findings.append({"kind": "failing_agent", "subject": agent_id, "detail": detail, "actions": [
                {"tool": "supervision.pause_agent", "args": {"agent_id": agent_id, "reason": detail}}]})
    for s in h["stuck"]:
        detail = f"{s['agent_id'] or 'task'} has waited on its sub-tasks since {s['since']}"
        findings.append({"kind": "stuck_task", "subject": s["task_id"], "detail": detail, "actions": [
            {"tool": "supervision.cancel_task", "args": {"task_id": s["task_id"], "reason": detail}}]})
    q = h["queue"]
    if q["oldest_minutes"] >= QUEUE_AGE_MINUTES:
        findings.append({"kind": "queue_backlog", "subject": "queue", "actions": [],
                         "detail": f"{q['queued']} tasks waiting; the oldest for {q['oldest_minutes']:g} minutes (is a worker running?)"})
    if q["dead_letter_recent"]:
        findings.append({"kind": "dead_letters", "subject": "queue", "actions": [],
                         "detail": f"{q['dead_letter_recent']} tasks gave up after repeated failures"})
    m = h["models"]
    if m["refused_by_budget"]:
        findings.append({"kind": "budget", "subject": "models", "actions": [],
                         "detail": f"{m['refused_by_budget']} model calls refused by budgets"})
    errs = sum(m["errors"].values())
    if m["calls"] >= MIN_RUNS and errs / m["calls"] >= FAIL_RATE:
        findings.append({"kind": "model_errors", "subject": "models", "actions": [],
                         "detail": f"{errs} of {m['calls']} model calls failed ({', '.join(sorted(m['errors']))})"})
    return findings


def assess(state, config):
    findings = _rules(state["health"])
    proposed = [a for f in findings for a in f["actions"]]
    chosen, note = proposed, "rules"
    if proposed:
        from features.ai_jobs import models
        from infra import model_config
        cfg = model_config.for_agents(SYSTEM_UID)  # only the Super Admin's model; without one the rules decide
        if cfg:
            try:
                out = models.chat(cfg, "You supervise AI agents. From the proposed actions, choose the ones that are "
                                  "warranted. Answer JSON: {\"take\": [indexes of actions to take], \"why\": \"one sentence\"}.",
                                  "Findings and proposed actions (numbered):\n" + "\n".join(
                                      f"{i}. {a['tool']} {a['args']}" for i, a in enumerate(proposed)), as_json=True,
                                  max_tokens=300, temperature=0)
                take = out.get("take") if isinstance(out, dict) else None
                if isinstance(take, list) and all(isinstance(i, int) and 0 <= i < len(proposed) for i in take):
                    chosen = [proposed[i] for i in sorted(set(take))]
                    note = f"model: {str(out.get('why', ''))[:200]}"
            except Exception:
                pass  # rules decide
    return {"findings": findings, "chosen": chosen, "notes": [f"{len(findings)} findings; actions chosen by {note}"]}


def act(state, config):
    c, done = runtime.ctx(config), []
    if mode() != "act":
        return {"done": [{**a, "result": "not taken (observe mode)"} for a in state.get("chosen", [])]}
    for a in state.get("chosen", []):
        runtime.checkpoint(config)
        try:
            done.append({**a, "result": tools.call(c, a["tool"], a["args"])})
        except tools.ToolError as e:
            done.append({**a, "result": f"refused: {e}"})
    return {"done": done}


def report(state, config):
    findings = state.get("findings", [])
    rid = runtime.ctx(config).task_id
    prev = M.supervisor_reports.find_one({"_id": {"$ne": rid}}, {"findings": 1}, sort=[("at", -1)]) or {}
    M.supervisor_reports.replace_one({"_id": rid}, {"_id": rid, "at": _now(), "mode": mode(), "findings": findings,
                                     "actions": state.get("done", []), "notes": state.get("notes", []),
                                     "health": state.get("health", {})}, upsert=True)
    old = [d["_id"] for d in M.supervisor_reports.find({}, {"_id": 1}).sort("at", -1).skip(KEEP_REPORTS)]
    if old:
        M.supervisor_reports.delete_many({"_id": {"$in": old}})
    seen = {(f["kind"], f["subject"]) for f in prev.get("findings", [])}
    new = [f for f in findings if (f["kind"], f["subject"]) not in seen]  # alert once per new problem, not every run
    if new:
        lines = [f"- {f['detail']}" for f in new[:6]]
        taken = [a for a in state.get("done", []) if isinstance(a.get("result"), dict)]
        try:
            tools.call(runtime.ctx(config), "supervision.alert", {"title": f"Supervisor: {len(new)} thing(s) to check",
                                                                  "body": "\n".join(lines + ([f"Done: {len(taken)} action(s)."] if taken else []))[:1000]})
        except tools.ToolError:
            pass
    return {"notes": [f"report {rid}"]}


def build():
    g = StateGraph(State)
    for name, fn in (("observe", observe), ("assess", assess), ("act", act), ("report", report)):
        g.add_node(name, fn)
    g.add_edge(START, "observe")
    g.add_edge("observe", "assess")
    g.add_edge("assess", "act")
    g.add_edge("act", "report")
    g.add_edge("report", END)
    return g


GRANTS = frozenset({"supervision.health", "supervision.pause_agent", "supervision.cancel_task", "supervision.alert"})


def register():
    runtime.register_agent(runtime.Agent(
        AGENT_ID, "1", "supervisor-1", GRANTS, build, max_steps=8,
        description="Watches every agent; pauses one that keeps failing, cancels stuck work, alerts the Super Admin.",
        instructions="observe metadata; rules propose; model may choose among proposals; act only in act mode"))


def tick():
    """Every EVERY_SECONDS (bridge.every): queue one Supervisor run for this slot, then run it here, so supervision
    works even without a separate worker. One run per slot across processes (idempotency key)."""
    if AGENT_ID not in runtime.AGENTS:
        return
    slot = int(time.time() // EVERY_SECONDS)
    runtime.start(SYSTEM_UID, AGENT_ID, {}, mode="on", idempotency_key=f"supervisor:{slot}", max_attempts=1)
    tasks.run_once("supervisor-loop", kinds=[f"agent.{AGENT_ID}"])


def run_now():
    """Run one check immediately (Super Admin's "check now"); returns its report, or None if it couldn't run
    (for example the Supervisor is stopped by a kill switch)."""
    import uuid
    tid = runtime.start(SYSTEM_UID, AGENT_ID, {}, mode="on", max_attempts=1, idempotency_key=f"supervisor:now:{uuid.uuid4().hex}")
    tasks.run_once("supervisor-now", kinds=[f"agent.{AGENT_ID}"])
    d = M.supervisor_reports.find_one({"_id": tid})
    return {**{k: v for k, v in d.items() if k != "_id"}, "id": tid} if d else None


register()
