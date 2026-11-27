"""Agent APIs for the signed-in user: their own approvals (decide each external action an agent wants to take)."""

from flask import jsonify, request

from core.http import app
from core.security import body, login_required
from core.validation import Invalid
from infra import approvals

STATUSES = (approvals.PENDING, approvals.APPROVED, approvals.REJECTED, approvals.EXECUTING, approvals.EXECUTED,
            approvals.FAILED, approvals.WITHDRAWN, approvals.EXPIRED)


@app.get("/api/agents/approvals")
@login_required
def list_approvals(ws):
    """The user's own approvals, newest first (`?status=pending` for the ones waiting for them)."""
    status = request.args.get("status") or None
    if status and status not in STATUSES:
        raise Invalid("Unknown status.", "status")
    return jsonify(approvals=approvals.list_for(ws.uid, status=status))


@app.get("/api/agents/approvals/<aid>")
@login_required
def get_approval(ws, aid):
    a = approvals.get(ws.uid, aid)
    if not a:
        raise Invalid("That approval doesn't exist.", status=404)
    return jsonify(approval=a)


@app.post("/api/agents/approvals/<aid>")
@login_required
def decide_approval(ws, aid):
    """{decision: "approve" | "reject", args?: {...edited details}}. Only the owner can decide; audited."""
    p = body()
    args = p.get("args")
    if args is not None and not isinstance(args, dict):
        raise Invalid("Edited details must be an object.", "args")
    return jsonify(approval=approvals.decide(ws.uid, aid, p.get("decision"), args))


# ---- agent tasks (the user's own) -------------------------------------------------------------------------------

@app.get("/api/agents")
@login_required
def list_agents(ws):
    """Agents the user can start, with their version and the tools they may use."""
    from infra import runtime
    return jsonify(agents=[{"id": a.id, "version": a.version, "description": a.description, "tools": sorted(a.grants)}
                           for a in sorted(runtime.AGENTS.values(), key=lambda a: a.id) if a.id not in SYSTEM_AGENTS])


AGENT_MODES = ("off", "shadow", "on")
SYSTEM_AGENTS = {"supervisor"}  # run by the platform for everyone; users can't start or see them


def agent_mode(ws):
    """The user's own rollout switch (product decision D-12, 2026-11-27: each user decides for their account).
    off: agents don't run. shadow (default): agents run but only read, change nothing and contact no one.
    on: agents can act, and every external action still waits for the user's approval."""
    m = (ws.load("agent_mode", {}) or {}).get("mode")
    return m if m in AGENT_MODES else "shadow"


@app.get("/api/agents/mode")
@login_required
def get_agent_mode(ws):
    return jsonify(mode=agent_mode(ws), modes=list(AGENT_MODES))


@app.put("/api/agents/mode")
@login_required
def set_agent_mode(ws):
    """{mode: off | shadow | on} for the signed-in user's own account. Recorded in the audit log."""
    from infra import audit
    mode = body().get("mode")
    if mode not in AGENT_MODES:
        raise Invalid("Choose off, shadow or on.", "mode")
    before = agent_mode(ws)
    if mode != before:
        audit.record("agent_mode.set", ws.uid, target=f"user:{ws.uid}", details={"from": before, "to": mode})
        with ws.lock:
            ws.save("agent_mode", {"mode": mode})
    return jsonify(mode=mode)


@app.post("/api/agents/tasks")
@login_required
def start_agent_task(ws):
    """{agent_id, input, idempotency_key?}: queue a run for the signed-in user, in the mode they chose for their
    account (off refuses; a run can ask for shadow even when the account is on, never the other way round)."""
    from infra import runtime, tasks
    p = body()
    agent_id = str(p.get("agent_id") or "")
    if agent_id not in runtime.AGENTS or agent_id in SYSTEM_AGENTS:
        raise Invalid("Unknown agent.", "agent_id", status=404)
    account = agent_mode(ws)
    if account == "off":
        raise Invalid("Agents are switched off for your account. Turn them on in Approvals.", "mode", status=403)
    wanted = p.get("mode") or account
    if wanted not in ("shadow", "on"):
        raise Invalid("Choose shadow or on.", "mode")
    if wanted == "on" and account != "on":
        raise Invalid("Your account is in shadow mode, so agents only look and report. Switch it on first.", "mode",
                      status=403)
    inp = p.get("input") or {}
    if not isinstance(inp, dict):
        raise Invalid("input must be an object.", "input")
    key = p.get("idempotency_key")
    try:
        tid = runtime.start(ws.uid, agent_id, inp, mode=wanted, idempotency_key=str(key) if key else None)
    except ValueError as e:
        raise Invalid(str(e), "idempotency_key")
    return jsonify(task=tasks.get(ws.uid, tid)), 202


def _own_task(ws, tid):
    from infra import tasks
    t = tasks.get(ws.uid, tid)
    if not t:
        raise Invalid("That task doesn't exist.", status=404)
    return t


@app.get("/api/agents/tasks")
@login_required
def list_agent_tasks(ws):
    from core.db import M
    from infra import tasks
    rows = M.tasks.find({"uid": ws.uid, "kind": {"$regex": "^agent\\."}}).sort("created_at", -1).limit(50)
    return jsonify(tasks=[tasks.view_of(d, with_content=False) for d in rows])


@app.get("/api/agents/tasks/<tid>")
@login_required
def get_agent_task(ws, tid):
    from infra import runtime
    return jsonify(task=_own_task(ws, tid), runs=runtime.runs_for(ws.uid, tid))


@app.get("/api/agents/tasks/<tid>/events")
@login_required
def agent_task_events(ws, tid):
    """The task's state changes, oldest first (poll with ?after=<ISO time>)."""
    from infra import tasks
    _own_task(ws, tid)
    rows = tasks.events(ws.uid, tid)
    after = request.args.get("after")
    if after:
        import datetime as dt
        try:
            cut = dt.datetime.fromisoformat(after.replace(" ", "+"))
        except ValueError:
            raise Invalid("after must be an ISO date-time.", "after")
        if cut.tzinfo is None:
            cut = cut.replace(tzinfo=dt.timezone.utc)
        rows = [e for e in rows if e["at"] > cut]
    return jsonify(events=[{**e, "at": e["at"].isoformat()} for e in rows])


@app.get("/api/agents/tasks/<tid>/trace")
@login_required
def agent_task_trace(ws, tid):
    """The task's execution trace for its owner (spans, with redacted summaries)."""
    from infra import telemetry
    t = _own_task(ws, tid)
    tr = telemetry.owner_trace(ws.uid, t["trace_id"])
    return jsonify(trace=tr)


@app.post("/api/agents/tasks/<tid>/cancel")
@login_required
def cancel_agent_task(ws, tid):
    from infra import tasks
    _own_task(ws, tid)
    return jsonify(task=tasks.cancel(ws.uid, tid))
