"""Kill switches (task PH1-05) and delegation limits (task PH1-10) on the task queue."""

import threading
import time

import pytest
from conftest import H, client_for, make_user

import app as A
from infra import audit, controls, rbac, tasks


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.kill_switches):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    yield
    for c in (A.M.tasks, A.M.task_events, A.M.kill_switches):
        c.delete_many({})


@pytest.fixture
def admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    return {"uid": uid, "client": client_for(uid)}


def statuses(*ids):
    return [A.M.tasks.find_one({"_id": i})["status"] for i in ids]


def drain():
    while tasks.run_once("w", lease=5):
        pass


# ---- kill switches ----------------------------------------------------------------------------------------------

def test_global_switch_blocks_all_new_executions_until_cleared(admin):
    u, _ = make_user()
    tasks.register("x", lambda t: "ran")
    tid = tasks.enqueue(u, "x")
    controls.set_switch(admin["uid"], reason="provider outage")
    assert tasks.run_once() is None and statuses(tid) == ["queued"]
    controls.clear_switch(admin["uid"], reason="resolved")
    drain()
    assert statuses(tid) == ["done"]


def test_user_switch_blocks_only_that_user(admin):
    a, _ = make_user()
    b, _ = make_user()
    tasks.register("x", lambda t: None)
    ta, tb = tasks.enqueue(a, "x"), tasks.enqueue(b, "x")
    controls.set_switch(admin["uid"], uid=a, reason="abuse report")
    drain()
    assert statuses(ta, tb) == ["queued", "done"]


def test_agent_switch_blocks_only_that_agent(admin):
    u, _ = make_user()
    tasks.register("x", lambda t: None)
    scout, writer, plain = tasks.enqueue(u, "x", agent_id="job_scout"), tasks.enqueue(u, "x", agent_id="writer"), tasks.enqueue(u, "x")
    controls.set_switch(admin["uid"], agent_id="writer", reason="bad outputs")
    drain()
    assert statuses(scout, writer, plain) == ["done", "queued", "done"]


def test_running_task_stops_at_its_next_checkpoint_when_a_switch_covers_it(admin):
    u, _ = make_user()
    started, steps = threading.Event(), []

    def long(t):
        for i in range(200):
            started.set()
            t.checkpoint()
            steps.append(i)
            time.sleep(0.01)
    tasks.register("long", long)
    tid = tasks.enqueue(u, "long", agent_id="job_analyst")
    th = threading.Thread(target=lambda: tasks.run_once("w", lease=5))
    th.start()
    started.wait(2)
    controls.set_switch(admin["uid"], agent_id="job_analyst", reason="stop it")
    th.join(5)
    assert statuses(tid) == ["cancelled"] and len(steps) < 200
    assert [e.get("reason") for e in tasks.events(u, tid) if e["event"] == "cancelled"] == ["kill_switch"]


def test_cancel_running_asks_covered_running_tasks_to_stop(admin):
    a, _ = make_user()
    b, _ = make_user()
    tasks.register("x", lambda t: None)
    tasks.enqueue(a, "x")
    tasks.enqueue(b, "x")
    ra, rb = tasks.claim("w1"), tasks.claim("w2")
    n = controls.set_switch(admin["uid"], uid=a, reason="stop a now", cancel_running=True)
    assert n == 1
    flags = {d["uid"]: d["cancel_requested"] for d in A.M.tasks.find({}, {"uid": 1, "cancel_requested": 1})}
    assert flags == {a: True, b: False}
    assert ra and rb


def test_switches_need_permission_a_reason_and_are_audited(admin):
    u, _ = make_user()
    with pytest.raises(A.Invalid) as e:
        controls.set_switch(u, reason="I want to stop everyone")
    assert e.value.status == 403 and not controls.active()["global"]
    with pytest.raises(A.Invalid):
        controls.set_switch(admin["uid"], reason="")
    with pytest.raises(ValueError):
        controls.set_switch(admin["uid"], agent_id="writer", uid=u, reason="both at once")
    controls.set_switch(admin["uid"], uid=u, reason="checking audit")
    ev = A.M.audit_log.find_one({"action": "kill_switch.set", "target": f"user:{u}"})
    assert ev and ev["actor"] == admin["uid"] and ev["details"]["reason"] == "checking audit"
    assert audit.verify()[0]


def test_no_switch_changes_without_an_audit_record(admin, monkeypatch):
    def down(*a, **k):
        raise audit.AuditError("audit unavailable")
    monkeypatch.setattr(audit, "record", down)
    with pytest.raises(audit.AuditError):
        controls.set_switch(admin["uid"], reason="must not apply")
    assert not controls.active()["global"]


def test_admin_api_is_super_admin_only_and_returns_metadata(admin):
    u, _ = make_user()
    uc = client_for(u)
    assert uc.get("/api/admin/kill-switches").status_code == 403
    assert uc.post("/api/admin/kill-switches", json={"reason": "nope nope"}, headers=H).status_code == 403
    c = admin["client"]
    r = c.post("/api/admin/kill-switches", json={"agent_id": "applicant", "reason": "pause submissions"}, headers=H)
    assert r.status_code == 200
    rows = c.get("/api/admin/kill-switches").get_json()["switches"]
    assert rows == [{"scope": "agent:applicant", "active": True, "reason": "pause submissions", "by": admin["uid"],
                     "at": rows[0]["at"], "cleared_at": None}]
    assert c.post("/api/admin/kill-switches/clear", json={"agent_id": "applicant", "reason": "ok"}, headers=H).status_code == 200
    assert not controls.active()["agents"]
    assert c.post("/api/admin/kill-switches", json={"agent_id": "Bad Agent!", "reason": "xxxxx"}, headers=H).status_code == 400


# ---- delegation limits ------------------------------------------------------------------------------------------

def test_depth_limit(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_DEPTH", 2)
    u, _ = make_user()
    tasks.register("x", lambda t: None)
    t0 = tasks.enqueue(u, "x", agent_id="orchestrator")
    t1 = tasks.enqueue(u, "x", parent_id=t0, agent_id="job_analyst")
    t2 = tasks.enqueue(u, "x", parent_id=t1, agent_id="researcher")
    with pytest.raises(tasks.DelegationRefused, match="levels"):
        tasks.enqueue(u, "x", parent_id=t2, agent_id="writer")
    assert tasks.get(u, t2)["depth"] == 2


def test_children_limit(monkeypatch):
    monkeypatch.setattr(tasks, "MAX_CHILDREN", 3)
    u, _ = make_user()
    tasks.register("x", lambda t: None)
    root = tasks.enqueue(u, "x", agent_id="orchestrator")
    for _ in range(3):
        tasks.enqueue(u, "x", parent_id=root, agent_id="job_scout")
    with pytest.raises(tasks.DelegationRefused, match="at most 3"):
        tasks.enqueue(u, "x", parent_id=root, agent_id="job_scout")


def test_cycles_are_refused():
    u, _ = make_user()
    tasks.register("x", lambda t: None)
    o = tasks.enqueue(u, "x", agent_id="orchestrator")
    a = tasks.enqueue(u, "x", parent_id=o, agent_id="job_analyst")
    with pytest.raises(tasks.DelegationRefused, match="cycle"):
        tasks.enqueue(u, "x", parent_id=a, agent_id="orchestrator")  # back to an ancestor
    r = tasks.enqueue(u, "x", parent_id=a, agent_id="researcher")
    with pytest.raises(tasks.DelegationRefused, match="cycle"):
        tasks.enqueue(u, "x", parent_id=r, agent_id="job_analyst")
    assert A.M.tasks.find_one({"_id": r})["agent_chain"] == ["orchestrator", "job_analyst"]


def test_a_child_never_outlives_its_parents_deadline():
    u, _ = make_user()
    tasks.register("x", lambda t: None)
    parent = tasks.enqueue(u, "x", deadline=60)
    child = tasks.enqueue(u, "x", parent_id=parent, deadline=3600)
    unbounded = tasks.enqueue(u, "x", parent_id=parent)
    pd = A.M.tasks.find_one({"_id": parent})["deadline"]
    assert A.M.tasks.find_one({"_id": child})["deadline"] == pd == A.M.tasks.find_one({"_id": unbounded})["deadline"]


def test_bad_agent_ids_are_refused():
    u, _ = make_user()
    tasks.register("x", lambda t: None)
    with pytest.raises(ValueError):
        tasks.enqueue(u, "x", agent_id="$ne")
