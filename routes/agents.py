"""Agent APIs for the signed-in user: their own approvals (decide each external action an agent wants to take)."""

from flask import jsonify, request

from core.http import app
from core.security import body, login_required
from core.validation import Invalid
from infra import approvals

STATUSES = (approvals.PENDING, approvals.APPROVED, approvals.REJECTED, approvals.EXECUTING, approvals.EXECUTED,
            approvals.FAILED, approvals.WITHDRAWN)


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
