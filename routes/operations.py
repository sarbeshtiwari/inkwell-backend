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
    """Super Admin: set a scope's limits ({agent_id} or {uid}, or neither for the whole service). Audited first."""
    from infra import budgets
    p = body()
    out = budgets.set_budget(ws.uid, agent_id=p.get("agent_id") or None, uid=p.get("uid") or None,
                             limits=p.get("limits") if isinstance(p.get("limits"), dict) else None,
                             applies_to=p.get("applies_to") or ["platform"], reason=p.get("reason"))
    return jsonify(ok=True, budget=out)
