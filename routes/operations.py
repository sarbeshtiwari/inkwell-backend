"""Long-running operations API: a user's own runs (status, cancel), and Super Admin cancellation (metadata only)."""

import re

from flask import jsonify

from core.http import app
from core.security import body, login_required
from core.validation import Invalid
from infra import audit, operations, rbac

KEY_OK = re.compile(r"^[\w.:-]{0,120}$")
UID_OK = re.compile(r"^[\w-]{1,64}$")


def _kind_key(kind, key):
    if kind not in operations.KINDS:
        raise Invalid("Unknown kind of task.", status=404)
    if not KEY_OK.match(key or ""):
        raise Invalid("Unknown task.", status=404)
    return kind, key or ""


@app.get("/api/operations/<kind>")
@login_required
def operation_status(ws, kind):
    """The signed-in user's latest run of this kind (only ever their own)."""
    from flask import request
    kind, key = _kind_key(kind, request.args.get("key", ""))
    return jsonify(operation=operations.current(ws.uid, kind, key))


@app.post("/api/operations/<kind>/cancel")
@login_required
def operation_cancel(ws, kind):
    """Ask the signed-in user's running task of this kind to stop at its next safe point. Idempotent; a finished task
    is left as it is."""
    kind, key = _kind_key(kind, str(body().get("key") or ""))
    view = operations.cancel(ws.uid, kind, key)
    return jsonify(ok=True, state=view["state"] if view else None, cancel_requested=bool(view and view["cancel_requested"]))


@app.post("/api/admin/operations/<uid>/<kind>/cancel")
@login_required
@rbac.require_permission("admin.control_agents", message="Only a Super Admin can stop other people's tasks.")
def admin_operation_cancel(ws, uid, kind):
    """Super Admin: stop a user's running task. Audited before it happens; the answer carries metadata only."""
    if not UID_OK.match(uid):
        raise Invalid("Unknown user.", status=404)
    kind, key = _kind_key(kind, str(body().get("key") or ""))
    reason = str(body().get("reason") or "").strip()[:300]
    if len(reason) < 5:
        raise Invalid("Give a short reason for stopping it.", "reason")
    audit.record("operation.cancel", ws.uid, target=f"user:{uid}", details={"kind": kind, "key": key, "reason": reason})
    view = operations.cancel(uid, kind, key)
    return jsonify(ok=True, state=view["state"] if view else None, cancel_requested=bool(view and view["cancel_requested"]))


@app.get("/api/admin/kill-switches")
@login_required
@rbac.require_permission("admin.control_agents", message="Only a Super Admin can see kill switches.")
def admin_kill_switches(ws):
    """Super Admin: every kill switch (scope, state, reason, who, when). Metadata only."""
    from infra import controls
    return jsonify(switches=controls.listing())


@app.post("/api/admin/kill-switches")
@login_required
def admin_kill_switch_set(ws):
    """Super Admin: stop new executions globally, for one agent ({agent_id}) or one user ({uid}).
    {cancel_running: true} also asks covered running tasks to stop at their next checkpoint. Audited first."""
    from infra import controls
    p = body()
    stopped = controls.set_switch(ws.uid, agent_id=p.get("agent_id") or None, uid=p.get("uid") or None,
                                  reason=p.get("reason"), cancel_running=bool(p.get("cancel_running")))
    return jsonify(ok=True, cancel_requested=stopped, switches=controls.listing())


@app.post("/api/admin/kill-switches/clear")
@login_required
def admin_kill_switch_clear(ws):
    """Super Admin: clear a kill switch (queued tasks it held back start running again). Audited first."""
    from infra import controls
    p = body()
    controls.clear_switch(ws.uid, agent_id=p.get("agent_id") or None, uid=p.get("uid") or None, reason=p.get("reason"))
    return jsonify(ok=True, switches=controls.listing())


@app.get("/api/usage")
@login_required
def my_usage(ws):
    """The signed-in user's own model usage: today's and this month's calls, tokens and cost (UTC periods)."""
    import time as _t
    from infra import gateway
    day = _t.strftime("%Y-%m-%d", _t.gmtime())
    month = day[:8] + "01"
    return jsonify(today=gateway.totals(ws.uid, day, day), month=gateway.totals(ws.uid, month, day[:8] + "31"))


@app.get("/api/admin/budgets")
@login_required
@rbac.require_permission("admin.manage_budgets", message="Only a Super Admin can see budgets.")
def admin_budgets(ws):
    from infra import budgets
    return jsonify(budgets=budgets.listing())


@app.post("/api/admin/budgets")
@login_required
def admin_budget_set(ws):
    """Super Admin: set a scope's limits: {per_user: true} for every user's own daily/monthly limit, {agent_id},
    {uid}, or none of these for the whole service together. Audited first."""
    from infra import budgets
    p = body()
    out = budgets.set_budget(ws.uid, agent_id=p.get("agent_id") or None, uid=p.get("uid") or None,
                             per_user=bool(p.get("per_user")),
                             limits=p.get("limits") if isinstance(p.get("limits"), dict) else None,
                             applies_to=p.get("applies_to") or ["platform"], reason=p.get("reason"))
    return jsonify(ok=True, budget=out)


# ---- Agent Observatory (metadata only) --------------------------------------------------------------------------

@app.get("/api/admin/agents/overview")
@login_required
@rbac.require_permission("admin.read_metadata", message="Only a Super Admin can see agent activity.")
def admin_agents_overview(ws):
    """Counts and totals across all users: tasks by status, runs by agent and outcome, model use by model, errors by
    class. Numbers only: no user content, no task inputs or results."""
    from core.db import M

    def group(coll, match, key):
        return [{**r["_id"], "count": r["count"]} for r in coll.aggregate([
            {"$match": match}, {"$group": {"_id": key, "count": {"$sum": 1}}}, {"$sort": {"count": -1}}, {"$limit": 100}])]
    return jsonify(
        tasks=group(M.tasks, {"kind": {"$regex": "^agent\\."}}, {"agent_id": "$agent_id", "status": "$status"}),
        runs=group(M.agent_runs, {}, {"agent_id": "$agent_id", "agent_version": "$agent_version", "outcome": "$outcome"}),
        task_errors=group(M.tasks, {"error_class": {"$ne": None}, "kind": {"$regex": "^agent\\."}}, {"error_class": "$error_class"}),
        models=[{**r["_id"], **{k: r[k] for k in ("calls", "errors", "input_tokens", "output_tokens", "cost", "latency_ms")}}
                for r in M.model_usage.aggregate([{"$group": {
                    "_id": {"provider": "$provider", "model_id": "$model_id", "billing": "$billing"},
                    "calls": {"$sum": 1}, "errors": {"$sum": {"$cond": [{"$eq": ["$status", "ok"]}, 0, 1]}},
                    "input_tokens": {"$sum": {"$ifNull": ["$input_tokens", 0]}},
                    "output_tokens": {"$sum": {"$ifNull": ["$output_tokens", 0]}},
                    "cost": {"$sum": {"$ifNull": ["$cost", 0]}}, "latency_ms": {"$avg": "$latency_ms"}}},
                    {"$sort": {"calls": -1}}, {"$limit": 100}])])


@app.get("/api/admin/agents/tasks")
@login_required
@rbac.require_permission("admin.read_metadata", message="Only a Super Admin can see agent activity.")
def admin_agent_tasks(ws):
    """Recent agent tasks across users (`?status=`, `?agent_id=`): metadata only."""
    from flask import request

    from core.db import M
    from infra import tasks
    q = {"kind": {"$regex": "^agent\\."}}
    if request.args.get("status"):
        q["status"] = str(request.args["status"])[:40]
    if request.args.get("agent_id"):
        q["agent_id"] = str(request.args["agent_id"])[:64]
    rows = M.tasks.find(q).sort("created_at", -1).limit(100)
    return jsonify(tasks=[{**tasks.view_of(d, with_content=False), "uid": d["uid"]} for d in rows])


@app.get("/api/admin/agents/traces/<trace_id>")
@login_required
@rbac.require_permission("admin.read_metadata", message="Only a Super Admin can see agent activity.")
def admin_agent_trace(ws, trace_id):
    """One trace's metadata: span types, timings, statuses, error classes, models and tokens. No names, summaries
    or payloads (content reveal stays disabled)."""
    from infra import telemetry
    tr = telemetry.metadata_trace(str(trace_id)[:64])
    if not tr:
        raise Invalid("Unknown trace.", status=404)
    return jsonify(trace=tr)


@app.get("/api/admin/model")
@login_required
@rbac.require_permission("admin.manage_models", message="Only a Super Admin can see the agents' model.")
def admin_model(ws):
    """The service-wide model for agents, and which platform providers have a key on the server (never the keys)."""
    from infra import model_config
    return jsonify(model_config.status())


@app.put("/api/admin/model")
@login_required
def admin_model_set(ws):
    """Super Admin: {provider: "anthropic"|"openai"|null, model, reason}. null goes back to each user's own model.
    Audited first."""
    from infra import model_config
    p = body()
    return jsonify(config=model_config.set_model(ws.uid, p.get("provider") or None, p.get("model"), p.get("reason")))


# ---- Supervisor (the agent that watches the agents) -------------------------------------------------------------

@app.get("/api/admin/supervisor")
@login_required
@rbac.require_permission("admin.read_metadata", message="Only a Super Admin can see the Supervisor.")
def admin_supervisor(ws):
    """The Supervisor's mode and its latest reports (metadata only)."""
    from flask import request

    from infra import supervisor
    return jsonify(mode=supervisor.mode(), reports=supervisor.reports(request.args.get("limit", 20)))


@app.put("/api/admin/supervisor")
@login_required
def admin_supervisor_mode(ws):
    """Super Admin: {mode: observe | act, reason}. observe reports only; act lets it pause failing agents and cancel
    stuck tasks (only you can undo those). Audited first."""
    from infra import supervisor
    p = body()
    return jsonify(mode=supervisor.set_mode(ws.uid, p.get("mode"), p.get("reason")))


@app.post("/api/admin/supervisor/run")
@login_required
@rbac.require_permission("admin.control_agents", message="Only a Super Admin can run the Supervisor.", audit_success=True)
def admin_supervisor_run(ws):
    """Super Admin: run a check now (in this request) and return its report."""
    from infra import supervisor
    return jsonify(report=supervisor.run_now())
