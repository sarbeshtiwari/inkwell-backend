"""The Supervisor agent: watches every agent (metadata only), acts within narrow limits, under the Super Admin."""

import datetime as dt
import json
import uuid

import pytest
from conftest import H, client_for, make_user

import app as A
from infra import controls, gateway, model_config, rbac, runtime, supervisor, tasks
from infra.gateway import Reply


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.agent_runs, A.M.kill_switches, A.M.supervisor_reports,
              A.M.supervisor_settings, A.M.model_configs, A.M.checkpoints, A.M.checkpoint_writes, A.M.notifications):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    supervisor.register()
    from features.ai_jobs import agents
    agents.register()
    yield


@pytest.fixture
def admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    return {"uid": uid, "client": client_for(uid)}


def failing_runs(agent_id, n=6, failed=5, secret="PRIVATE-GOAL"):
    uid, _ = make_user()
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(n):
        A.M.agent_runs.insert_one({"_id": uuid.uuid4().hex, "uid": uid, "task_id": uuid.uuid4().hex, "agent_id": agent_id,
                                   "agent_version": "1", "started": now, "outcome": "error" if i < failed else "done",
                                   "goal": secret})
    return uid


def run_check():
    return supervisor.run_now()


def test_observe_mode_reports_and_alerts_but_never_acts(admin):
    failing_runs("job_scout")
    r = run_check()
    assert r["mode"] == "observe"
    assert [(f["kind"], f["subject"]) for f in r["findings"]] == [("failing_agent", "job_scout")]
    assert r["actions"][0]["result"] == "not taken (observe mode)"
    assert "job_scout" not in controls.active()["agents"]
    assert A.M.notifications.count_documents({"uid": admin["uid"]}) == 1
    assert "PRIVATE-GOAL" not in json.dumps(r, default=str)          # metadata only


def test_act_mode_pauses_a_failing_agent_only_the_admin_can_resume(admin):
    supervisor.set_mode(admin["uid"], "act", "let it act")
    failing_runs("job_scout")
    failing_runs("writer", failed=1)                                     # healthy enough: left alone
    r = run_check()
    assert controls.active()["agents"] == {"job_scout"}
    assert r["actions"][0]["result"] == {"paused": True}
    sw = A.M.kill_switches.find_one({"_id": "agent:job_scout"})
    assert sw["by"] == "agent:supervisor" and sw["reason"].startswith("Supervisor:")
    assert A.M.audit_log.find_one({"action": "kill_switch.set", "actor": "agent:supervisor", "target": "agent:job_scout"})
    run_check()                                                          # already paused: no second alert, no new finding
    assert A.M.notifications.count_documents({"uid": admin["uid"]}) == 1
    controls.clear_switch(admin["uid"], agent_id="job_scout", reason="fixed")
    assert "job_scout" not in controls.active()["agents"]


def test_it_cannot_pause_itself_stop_everything_or_cancel_healthy_work(admin):
    supervisor.set_mode(admin["uid"], "act", "let it act")
    from infra import tools
    ctx = tools.ToolContext(uid=supervisor.SYSTEM_UID, grants=supervisor.GRANTS, mode="on", agent_id="supervisor", task_id="t")
    with pytest.raises(tools.ToolDenied):
        tools.call(ctx, "supervision.pause_agent", {"agent_id": "supervisor", "reason": "x"})
    with pytest.raises(tools.ToolError):
        tools.call(ctx, "supervision.pause_agent", {"agent_id": "global", "reason": "x"})
    uid, _ = make_user()
    tasks.register("x", lambda t: None)
    tid = tasks.enqueue(uid, "x")
    with pytest.raises(tools.ToolDenied, match="stuck"):
        tools.call(ctx, "supervision.cancel_task", {"task_id": tid, "reason": "x"})
    other = tools.ToolContext(uid=uid, grants=frozenset(), mode="on", agent_id="job_scout", task_id="t")
    with pytest.raises(tools.ToolDenied):                                # no other agent is granted these tools
        tools.call(other, "supervision.pause_agent", {"agent_id": "writer", "reason": "x"})
    assert not controls.active()["global"] and controls.active()["agents"] == set()


def test_cancels_a_task_stuck_on_its_sub_tasks(admin):
    supervisor.set_mode(admin["uid"], "act", "let it act")
    uid, _ = make_user()
    tasks.register("x", lambda t: None)
    tid = tasks.enqueue(uid, "x", agent_id="orchestrator")
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=supervisor.STUCK_HOURS + 1)
    A.M.tasks.update_one({"_id": tid}, {"$set": {"status": "waiting_children", "updated_at": old}})
    r = run_check()
    assert ("stuck_task", tid) in [(f["kind"], f["subject"]) for f in r["findings"]]
    assert A.M.tasks.find_one({"_id": tid})["status"] == "cancelled"


def test_the_model_chooses_only_among_proposed_actions(admin, monkeypatch):
    supervisor.set_mode(admin["uid"], "act", "let it act")
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", "sk-ant-test-key-00000000000000000000")
    model_config.set_model(admin["uid"], "anthropic", "claude-sonnet-5-5", "supervisor model")
    answers = iter([{"take": [], "why": "a deploy just happened; wait"}, {"take": [7, "rm -rf"], "why": "bad"}])
    monkeypatch.setitem(gateway.ADAPTERS, "anthropic", lambda req: Reply(json.dumps(next(answers)), model_id="m"))
    failing_runs("job_scout")
    r = run_check()
    assert "job_scout" not in controls.active()["agents"] and "model: a deploy just happened" in r["notes"][0]
    r = run_check()                                                      # invalid choice: the rules decide
    assert "job_scout" in controls.active()["agents"] and "rules" in r["notes"][0]


def test_super_admin_controls_it(admin):
    u, _ = make_user()
    uc, c = client_for(u), admin["client"]
    assert uc.get("/api/admin/supervisor").status_code == 403
    assert uc.put("/api/admin/supervisor", json={"mode": "act", "reason": "me me"}, headers=H).status_code == 403
    assert uc.post("/api/admin/supervisor/run", headers=H).status_code == 403
    assert c.put("/api/admin/supervisor", json={"mode": "loud", "reason": "xxxxx"}, headers=H).status_code == 400
    assert c.put("/api/admin/supervisor", json={"mode": "act", "reason": "go ahead"}, headers=H).get_json()["mode"] == "act"
    failing_runs("writer")
    rep = c.post("/api/admin/supervisor/run", headers=H).get_json()["report"]
    assert rep["findings"][0]["subject"] == "writer"
    got = c.get("/api/admin/supervisor").get_json()
    assert got["mode"] == "act" and got["reports"][0]["id"] == rep["id"]
    controls.set_switch(admin["uid"], agent_id="supervisor", reason="stop the supervisor")
    A.M.agent_runs.delete_many({})
    failing_runs("job_scout")
    assert run_check() is None                                           # kill switch: it doesn't run
    assert "job_scout" not in controls.active()["agents"]


def test_one_run_per_slot_across_ticks():
    supervisor.tick()
    supervisor.tick()
    assert A.M.tasks.count_documents({"kind": "agent.supervisor"}) == 1 and len(supervisor.reports(10)) == 1


def test_users_cannot_start_or_see_the_supervisor_or_use_its_tools():
    from infra import tools
    uid, _ = make_user()
    c = client_for(uid)
    assert "supervisor" not in [a["id"] for a in c.get("/api/agents").get_json()["agents"]]
    assert c.post("/api/agents/tasks", json={"agent_id": "supervisor", "input": {}}, headers=H).status_code == 404
    ctx = tools.ToolContext(uid=uid, grants=supervisor.GRANTS, mode="on", agent_id="supervisor", task_id="t")
    with pytest.raises(tools.ToolDenied, match="Only the Supervisor"):
        tools.call(ctx, "supervision.health", {})
