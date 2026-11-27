"""Human approval for external actions (ADR-0005): an agent's run pauses until the user decides each action.

Sending email or WhatsApp messages and submitting applications must always be approved by the user, one action at a
time. Standing consent (e.g. the auto-apply setting) never counts as approval here.

How a call to an `external_action` tool goes (infra/tools.py → gate()):

1. **No decision yet** → a pending approval is created (once per task, tool and exact arguments: `call_key`) holding
   the proposed arguments, sealed; the run pauses with a LangGraph interrupt and its task waits (`waiting_approval`).
2. **The user decides** (only the owner; never an agent or an admin): approve — optionally with edited arguments —
   or reject. The decision is written to the audit log first, then the task is queued again and resumes from its
   checkpoint, re-running only the paused step.
3. **Approved** → the approval is claimed atomically (approved → executing) so the action runs at most once, then
   the tool runs with the *approved* arguments, and the outcome is recorded (executed, with its result, or failed).
   A repeated call (a retried step) returns the recorded result instead of acting again.
4. **Rejected** → the call raises ApprovalRejected; the agent continues without that action.
5. **A crash between starting the action and recording it** leaves the approval in `executing`; a later call raises
   OutcomeUnknown and never repeats the action automatically — the user has to check and decide.

Approvals have no expiry yet (the period is a pending product decision); a pending approval can always be rejected,
and cancelling the task withdraws its pending approvals."""

import datetime as dt
import hashlib
import json
import uuid

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from core.crypto import seal, unseal
from core.db import M, ensure_index
from core.validation import Invalid
from infra import audit, telemetry

PENDING, APPROVED, REJECTED, EXECUTING, EXECUTED, FAILED, WITHDRAWN = (
    "pending", "approved", "rejected", "executing", "executed", "failed", "withdrawn")
OPEN = (PENDING,)


class ApprovalPending(Exception):
    """Raised (as a LangGraph interrupt inside a run) while the user hasn't decided."""


def ensure_indexes():
    ensure_index(M.approvals, [("uid", ASCENDING), ("task_id", ASCENDING), ("call_key", ASCENDING)], unique=True)
    ensure_index(M.approvals, [("uid", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])


def _now():
    return dt.datetime.now(dt.timezone.utc)


def call_key(tool_id, args):
    canon = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(f"{tool_id}\n{canon}".encode()).hexdigest()


def view(doc, with_content=True):
    if not doc:
        return None
    out = {"id": doc["_id"], "task_id": doc["task_id"], "trace_id": doc.get("trace_id"), "agent_id": doc.get("agent_id"),
           "tool_id": doc["tool_id"], "tool_version": doc.get("tool_version"), "status": doc["status"],
           "created_at": doc["created_at"], "decided_at": doc.get("decided_at"), "edited": bool(doc.get("edited"))}
    if with_content:
        out["proposed"] = unseal(doc.get("proposed"), {})
        out["approved_args"] = unseal(doc["final"], None) if doc.get("final") else None
        out["result"] = unseal(doc["result"], None) if doc.get("result") else None
        out["error"] = unseal(doc["error"], None) if doc.get("error") else None
    return out


def list_for(uid, status=None, task_id=None):
    q = {"uid": uid}
    if status:
        q["status"] = status
    if task_id:
        q["task_id"] = task_id
    return [view(d) for d in M.approvals.find(q).sort("created_at", DESCENDING).limit(200)]


def get(uid, approval_id):
    return view(M.approvals.find_one({"_id": str(approval_id), "uid": uid}))


# ---- used by the tool layer ---------------------------------------------------------------------------------------

def ensure(ctx, tool, args):
    """Make sure an approval exists for this exact call and return its status, without pausing. Lets an agent ask
    for several approvals at once (then pause once), instead of one pause per action."""
    key = call_key(tool.id, args)
    doc = M.approvals.find_one({"uid": ctx.uid, "task_id": ctx.task_id, "call_key": key}) or _create(ctx, tool, args, key)
    return doc["status"]


def gate(ctx, tool, args, run):
    """Run an external_action tool only with an approval for this exact call. `run(final_args)` performs it."""
    from infra import tools
    if not ctx.task_id:
        raise tools.ApprovalRequired(f"{tool.id} needs your approval, and approvals belong to an agent task.")
    key = call_key(tool.id, args)
    doc = M.approvals.find_one({"uid": ctx.uid, "task_id": ctx.task_id, "call_key": key})
    if doc is None:
        doc = _create(ctx, tool, args, key)
    status = doc["status"]
    if status == PENDING:
        _pause(doc)
    if status in (REJECTED, WITHDRAWN):
        raise tools.ApprovalRejected(f"You declined {tool.id}, so it wasn't done.")
    if status == EXECUTED:
        return unseal(doc.get("result"), None)  # already done: never act twice
    if status == EXECUTING:
        raise tools.OutcomeUnknown(f"{tool.id} may or may not have happened (the run stopped while doing it). "
                                   "Check before trying again; it won't be repeated automatically.")
    if status == FAILED:
        raise tools.ToolError(unseal(doc.get("error"), "It failed."))
    claimed = M.approvals.find_one_and_update({"_id": doc["_id"], "uid": ctx.uid, "status": APPROVED},
                                              {"$set": {"status": EXECUTING, "started_at": _now()}},
                                              return_document=ReturnDocument.AFTER)
    if not claimed:  # someone else took it between our read and now: look again
        return gate(ctx, tool, args, run)
    final = unseal(claimed.get("final"), args)
    telemetry.record("approval", tool.id, summary="approved; running", tool_id=tool.id, agent_id=ctx.agent_id,
                     task_id=ctx.task_id)
    try:
        result = run(final)
    except Exception as e:  # the action itself failed (and said so): recorded, not unknown
        M.approvals.update_one({"_id": doc["_id"], "status": EXECUTING},
                               {"$set": {"status": FAILED, "outcome_unknown": False, "finished_at": _now(),
                                         "error": seal(str(e)[:300])}})
        telemetry.record("approval", tool.id, summary="approved action failed", status="error",
                         error_class=getattr(e, "error_class", None) or type(e).__name__.lower(), tool_id=tool.id,
                         task_id=ctx.task_id)
        raise
    M.approvals.update_one({"_id": doc["_id"], "status": EXECUTING},
                           {"$set": {"status": EXECUTED, "finished_at": _now(), "result": seal(result)}})
    return result


def _create(ctx, tool, args, key):
    doc = {"_id": uuid.uuid4().hex, "uid": ctx.uid, "task_id": ctx.task_id, "trace_id": ctx.trace_id,
           "agent_id": ctx.agent_id, "agent_version": ctx.agent_version, "tool_id": tool.id, "tool_version": tool.version,
           "call_key": key, "proposed": seal(args), "status": PENDING, "created_at": _now()}
    try:
        M.approvals.insert_one(doc)
    except DuplicateKeyError:
        return M.approvals.find_one({"uid": ctx.uid, "task_id": ctx.task_id, "call_key": key})
    telemetry.record("approval", tool.id, summary="approval requested", tool_id=tool.id, agent_id=ctx.agent_id,
                     task_id=ctx.task_id)
    from features.notify import notify
    try:
        notify(ctx.uid, "Approval needed", f"An agent wants to run {tool.id}. Review it before anything is done.",
               "/app/ai-jobs", "info")
    except Exception:  # a notification must never stop the approval from being recorded
        pass
    return doc


def _pause(doc):
    from langgraph.types import interrupt
    interrupt({"approval_id": doc["_id"], "tool_id": doc["tool_id"]})
    raise ApprovalPending(doc["_id"])  # outside a graph (interrupt() needs one) the call simply can't proceed


# ---- the user's decision ------------------------------------------------------------------------------------------

def decide(uid, approval_id, decision, args=None):
    """The owner approves (optionally with edited arguments) or rejects a pending approval. Audited first; the task
    resumes afterwards. Returns the updated approval."""
    from infra import tasks, tools
    doc = M.approvals.find_one({"_id": str(approval_id), "uid": uid})
    if not doc:
        raise Invalid("That approval doesn't exist.", status=404)
    if doc["status"] != PENDING:
        raise Invalid("That has already been decided.", status=409)
    if decision not in ("approve", "reject"):
        raise Invalid("Choose approve or reject.", "decision")
    sets = {"decided_at": _now(), "decided_by": uid}
    edited = False
    if decision == "approve":
        final = unseal(doc.get("proposed"), {})
        if args is not None:
            t = tools.REGISTRY.get(doc["tool_id"])
            if t is None:
                raise Invalid("That action is no longer available.", status=409)
            try:
                tools.validate(args, t.input_schema)
            except ValueError as e:
                raise Invalid(f"The edited details aren't valid: {e}", "args")
            edited, final = args != final, args
        sets.update(status=APPROVED, final=seal(final), edited=edited)
    else:
        sets["status"] = REJECTED
    audit.record(f"approval.{decision}", uid, target=f"approval:{doc['_id']}",
                 details={"tool_id": doc["tool_id"], "task_id": doc["task_id"], "edited": edited},
                 trace_id=doc.get("trace_id") or "")
    r = M.approvals.update_one({"_id": doc["_id"], "uid": uid, "status": PENDING}, {"$set": sets})
    if not r.modified_count:
        raise Invalid("That has already been decided.", status=409)
    tasks.resume(uid, doc["task_id"])
    return get(uid, doc["_id"])


def withdraw_for_task(uid, task_id):
    """Cancelling a task withdraws its undecided approvals."""
    return M.approvals.update_many({"uid": uid, "task_id": task_id, "status": PENDING},
                                   {"$set": {"status": WITHDRAWN, "decided_at": _now()}}).modified_count
