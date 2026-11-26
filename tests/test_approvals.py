"""Human approval for external actions (task PH1-08): pause, decide (approve / edit / reject), resume, run once."""

from typing import TypedDict

import pytest
from conftest import H, client_for, make_user
from langgraph.graph import END, START, StateGraph

import app as A
from infra import approvals, rbac, runtime, tasks, tools
from infra.tools import EXTERNAL_ACTION, Tool


class S(TypedDict, total=False):
    to: str
    sent: dict
    outcome: str


SENT = []
INPUT = {"type": "object", "additionalProperties": False, "required": ["to", "body"],
         "properties": {"to": {"type": "string", "maxLength": 200}, "body": {"type": "string", "maxLength": 2000}}}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.approvals):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    SENT.clear()
    tools.register(Tool("test.send_mail", "1", "Send one email (test double).", INPUT,
                        {"type": "object", "required": ["id"]}, EXTERNAL_ACTION,
                        lambda ctx, to, body: SENT.append((ctx.uid, to, body)) or {"id": f"msg-{len(SENT)}"}))
    yield
    tools.REGISTRY.pop("test.send_mail", None)


def mailer(crash_after_send=None, fail=False):
    before = []

    def draft(state, config):
        before.append("draft")
        return {"to": state["to"]}

    def send(state, config):
        try:
            out = tools.call(runtime.ctx(config), "test.send_mail", {"to": state["to"], "body": "Hello from Inkwell"})
            if crash_after_send is not None and not crash_after_send:
                crash_after_send.append(1)
                raise SystemExit("worker died")  # after the action, before the step finished
            return {"sent": out, "outcome": "sent"}
        except tools.ApprovalRejected:
            return {"outcome": "skipped: you declined"}

    def build():
        g = StateGraph(S)
        g.add_node("draft", draft)
        g.add_node("send", send)
        g.add_edge(START, "draft")
        g.add_edge("draft", "send")
        g.add_edge("send", END)
        return g
    runtime.register_agent(runtime.Agent("mailer", "1", "i1", frozenset({"test.send_mail"}), build))
    return before


def run_all():
    while tasks.run_once("w", lease=5) is not None:
        pass


def start(uid, mode="on", to="hr@acme.example"):
    return runtime.start(uid, "mailer", {"to": to}, mode=mode)


def pending(uid):
    [a] = approvals.list_for(uid, status="pending")
    return a


def test_external_action_pauses_until_approved_then_runs_once():
    uid, _ = make_user()
    drafted = mailer()
    tid = start(uid)
    run_all()
    assert SENT == [] and tasks.get(uid, tid)["status"] == "waiting_approval"
    a = pending(uid)
    assert a["tool_id"] == "test.send_mail" and a["proposed"] == {"to": "hr@acme.example", "body": "Hello from Inkwell"}
    approvals.decide(uid, a["id"], "approve")
    run_all()
    v = tasks.get(uid, tid)
    assert v["status"] == "done" and v["result"]["outcome"] == "sent" and SENT == [(uid, "hr@acme.example", "Hello from Inkwell")]
    assert drafted == ["draft"]  # resuming re-ran only the paused step
    assert approvals.get(uid, a["id"])["status"] == "executed" and v["attempts"] == 1


def test_edited_approval_runs_with_the_users_version():
    uid, _ = make_user()
    mailer()
    start(uid)
    run_all()
    a = pending(uid)
    approvals.decide(uid, a["id"], "approve", {"to": "jobs@acme.example", "body": "My own words"})
    run_all()
    assert SENT == [(uid, "jobs@acme.example", "My own words")]
    assert approvals.get(uid, a["id"])["edited"] is True


def test_invalid_edits_are_refused():
    uid, _ = make_user()
    mailer()
    start(uid)
    run_all()
    a = pending(uid)
    for bad in ({"to": "x"}, {"to": "x", "body": "y", "cc": "z"}, {"to": 5, "body": "y"}):
        with pytest.raises(A.Invalid):
            approvals.decide(uid, a["id"], "approve", bad)
    assert pending(uid)["status"] == "pending"


def test_rejection_means_the_action_never_happens():
    uid, _ = make_user()
    mailer()
    tid = start(uid)
    run_all()
    approvals.decide(uid, pending(uid)["id"], "reject")
    run_all()
    assert SENT == [] and tasks.get(uid, tid)["result"]["outcome"] == "skipped: you declined"
    with pytest.raises(A.Invalid) as e:
        approvals.decide(uid, approvals.list_for(uid)[0]["id"], "approve")
    assert e.value.status == 409  # a decision is final


def test_shadow_mode_never_creates_approvals_or_acts():
    uid, _ = make_user()
    mailer()
    tid = start(uid, mode="shadow")
    run_all()
    v = tasks.get(uid, tid)
    assert v["status"] == "failed" and v["error_class"] == "shadow_refused" and v["attempts"] == 1
    assert SENT == [] and approvals.list_for(uid) == []


def test_only_the_owner_can_decide_and_admins_cannot(monkeypatch):
    uid, _ = make_user()
    other, _ = make_user()
    admin, admin_email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {admin_email})
    rbac.reconcile()
    mailer()
    start(uid)
    run_all()
    aid = pending(uid)["id"]
    for who in (other, admin):
        with pytest.raises(A.Invalid) as e:
            approvals.decide(who, aid, "approve")
        assert e.value.status == 404
        assert approvals.get(who, aid) is None and approvals.list_for(who) == []
    assert client_for(admin).post(f"/api/agents/approvals/{aid}", json={"decision": "approve"}, headers=H).status_code == 404
    assert SENT == []


def test_approval_decisions_are_audited():
    uid, _ = make_user()
    mailer()
    start(uid)
    run_all()
    aid = pending(uid)["id"]
    approvals.decide(uid, aid, "approve")
    ev = A.M.audit_log.find_one({"action": "approval.approve", "target": f"approval:{aid}"})
    assert ev and ev["actor"] == uid and ev["details"]["tool_id"] == "test.send_mail"


def test_crash_after_the_action_leaves_outcome_unknown_and_never_repeats_it():
    uid, _ = make_user()
    mailer(crash_after_send=[])
    tid = start(uid)
    run_all()
    approvals.decide(uid, pending(uid)["id"], "approve")
    a_id = approvals.list_for(uid)[0]["id"]
    # simulate the worker dying after sending but before recording: put the approval back in "executing"
    task = tasks.claim("w", lease=0.2)
    with pytest.raises(SystemExit):
        tasks.execute(task)
    assert len(SENT) == 1
    A.M.approvals.update_one({"_id": a_id}, {"$set": {"status": "executing"}, "$unset": {"result": ""}})
    import time
    time.sleep(0.3)
    run_all()
    assert len(SENT) == 1  # never sent twice
    v = tasks.get(uid, tid)
    assert v["status"] == "failed" and v["error_class"] == "outcome_unknown"


def test_a_retried_step_after_success_returns_the_recorded_result():
    uid, _ = make_user()
    mailer(crash_after_send=[])
    tid = start(uid)
    run_all()
    approvals.decide(uid, pending(uid)["id"], "approve")
    task = tasks.claim("w", lease=0.2)
    with pytest.raises(SystemExit):
        tasks.execute(task)  # sent and recorded, then the worker died before the step finished
    import time
    time.sleep(0.3)
    run_all()
    assert len(SENT) == 1 and tasks.get(uid, tid)["result"]["sent"] == {"id": "msg-1"}


def test_cancelling_a_waiting_task_withdraws_its_approval():
    uid, _ = make_user()
    mailer()
    tid = start(uid)
    run_all()
    aid = pending(uid)["id"]
    assert tasks.cancel(uid, tid)["status"] == "cancelled"
    assert approvals.get(uid, aid)["status"] == "withdrawn"
    with pytest.raises(A.Invalid):
        approvals.decide(uid, aid, "approve")
    run_all()
    assert SENT == []


def test_external_tools_outside_an_agent_task_are_refused():
    uid, _ = make_user()
    ctx = tools.ToolContext(uid=uid, grants=frozenset({"test.send_mail"}), mode="on")
    with pytest.raises(tools.ApprovalRequired):
        tools.call(ctx, "test.send_mail", {"to": "a", "body": "b"})
    assert SENT == []


def test_approvals_api():
    uid, _ = make_user()
    c = client_for(uid)
    mailer()
    start(uid)
    run_all()
    rows = c.get("/api/agents/approvals?status=pending").get_json()["approvals"]
    assert len(rows) == 1 and rows[0]["proposed"]["to"] == "hr@acme.example"
    aid = rows[0]["id"]
    assert c.get(f"/api/agents/approvals/{aid}").get_json()["approval"]["status"] == "pending"
    assert c.post(f"/api/agents/approvals/{aid}", json={"decision": "maybe"}, headers=H).status_code == 400
    assert c.post(f"/api/agents/approvals/{aid}", json={"decision": "approve", "args": "x"}, headers=H).status_code == 400
    assert c.post(f"/api/agents/approvals/{aid}", json={"decision": "approve"}, headers=H).get_json()["approval"]["status"] == "approved"
    assert c.get("/api/agents/approvals?status=bogus").status_code == 400
    run_all()
    assert len(SENT) == 1


def test_account_deletion_removes_approvals():
    uid, _ = make_user()
    mailer()
    start(uid)
    run_all()
    A.delete_user_data(uid)
    assert A.M.approvals.count_documents({"uid": uid}) == 0
