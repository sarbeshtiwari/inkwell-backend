"""Agent APIs for users (task PH1-13) and the metadata-only Observatory endpoints for Super Admins (task PH1-12)."""

from typing import TypedDict

import pytest
from conftest import H, client_for, make_user
from langgraph.graph import END, START, StateGraph

import app as A
from features.ai_jobs import models
from infra import gateway, rbac, runtime, tasks
from infra.gateway import FakeProvider

SECRET = "PRIVATE-RESUME-AND-GOAL-TEXT"


class S(TypedDict, total=False):
    goal: str
    verdict: str


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.agent_runs, A.M.agent_versions,
              A.M.model_usage, A.M.traces, A.M.spans):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    monkeypatch.setitem(gateway.ADAPTERS, "fake", FakeProvider(*[f"verdict about {SECRET}"] * 20))

    def node(state, config):
        uid = runtime.ctx(config).uid
        text = models.chat({"provider": "fake", "model": "m", "uid": uid, "label": "x"}, "judge", state["goal"])
        return {"verdict": text}

    def build():
        g = StateGraph(S)
        g.add_node("judge", node)
        g.add_edge(START, "judge")
        g.add_edge("judge", END)
        return g
    runtime.register_agent(runtime.Agent("judge", "1", "i1", frozenset({"rules.read"}), build, description="test agent"))


@pytest.fixture
def admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    return client_for(uid)


def run_all():
    while tasks.run_once("w", lease=5) is not None:
        pass


def test_user_starts_follows_and_reads_their_own_agent_task():
    uid, _ = make_user()
    c = client_for(uid)
    assert [a["id"] for a in c.get("/api/agents").get_json()["agents"]] == ["judge"]
    r = c.post("/api/agents/tasks", json={"agent_id": "judge", "input": {"goal": SECRET}}, headers=H)
    assert r.status_code == 202
    tid = r.get_json()["task"]["id"]
    run_all()
    t = c.get(f"/api/agents/tasks/{tid}").get_json()
    assert t["task"]["status"] == "done" and SECRET in t["task"]["result"]["verdict"]
    assert t["runs"][0]["agent_version"] == "1" and t["task"]["input"]["mode"] == "shadow"
    ev = c.get(f"/api/agents/tasks/{tid}/events").get_json()["events"]
    assert [e["event"] for e in ev] == ["enqueued", "claimed", "done"]
    later = c.get(f"/api/agents/tasks/{tid}/events?after={ev[1]['at']}").get_json()["events"]
    assert [e["event"] for e in later] == ["done"]
    assert c.get(f"/api/agents/tasks/{tid}/events?after=yesterday").status_code == 400
    trace = c.get(f"/api/agents/tasks/{tid}/trace").get_json()["trace"]
    assert {s["type"] for s in trace["spans"]} >= {"step", "model_call"}
    assert [x["id"] for x in c.get("/api/agents/tasks").get_json()["tasks"]] == [tid]


def test_only_shadow_mode_unknown_agents_and_bad_input_are_refused():
    uid, _ = make_user()
    c = client_for(uid)
    assert c.post("/api/agents/tasks", json={"agent_id": "judge", "mode": "on", "input": {}}, headers=H).status_code == 403
    assert c.post("/api/agents/tasks", json={"agent_id": "nope"}, headers=H).status_code == 404
    assert c.post("/api/agents/tasks", json={"agent_id": "judge", "input": "x"}, headers=H).status_code == 400
    assert c.post("/api/agents/tasks", json={"agent_id": "judge", "idempotency_key": "has space"}, headers=H).status_code == 400
    k = {"agent_id": "judge", "input": {"goal": "g"}, "idempotency_key": "weekly-run-48"}
    a = c.post("/api/agents/tasks", json=k, headers=H).get_json()["task"]["id"]
    assert c.post("/api/agents/tasks", json=k, headers=H).get_json()["task"]["id"] == a


def test_other_users_cannot_see_or_cancel_a_task():
    uid, _ = make_user()
    other, _ = make_user()
    tid = client_for(uid).post("/api/agents/tasks", json={"agent_id": "judge", "input": {"goal": "g"}}, headers=H).get_json()["task"]["id"]
    oc = client_for(other)
    for path in (f"/api/agents/tasks/{tid}", f"/api/agents/tasks/{tid}/events", f"/api/agents/tasks/{tid}/trace"):
        assert oc.get(path).status_code == 404
    assert oc.post(f"/api/agents/tasks/{tid}/cancel", headers=H).status_code == 404
    assert oc.get("/api/agents/tasks").get_json()["tasks"] == []
    assert client_for(uid).post(f"/api/agents/tasks/{tid}/cancel", headers=H).get_json()["task"]["status"] == "cancelled"


def test_observatory_is_super_admin_only(admin):
    uid, _ = make_user()
    c = client_for(uid)
    for path in ("/api/admin/agents/overview", "/api/admin/agents/tasks", "/api/admin/agents/traces/x"):
        assert c.get(path).status_code == 403


def test_observatory_shows_metadata_and_never_content(admin):
    uid, _ = make_user()
    tid = client_for(uid).post("/api/agents/tasks", json={"agent_id": "judge", "input": {"goal": SECRET}}, headers=H).get_json()["task"]["id"]
    run_all()
    ov = admin.get("/api/admin/agents/overview").get_json()
    assert {"agent_id": "judge", "status": "done", "count": 1} in ov["tasks"]
    assert ov["runs"][0]["agent_id"] == "judge" and ov["models"][0]["calls"] == 1
    rows = admin.get("/api/admin/agents/tasks?agent_id=judge").get_json()["tasks"]
    assert rows[0]["id"] == tid and rows[0]["uid"] == uid and "input" not in rows[0] and "result" not in rows[0]
    trace_id = rows[0]["trace_id"]
    tr = admin.get(f"/api/admin/agents/traces/{trace_id}").get_json()["trace"]
    assert {s["type"] for s in tr["spans"]} >= {"step", "model_call"}
    everything = repr(ov) + repr(rows) + repr(tr)
    assert SECRET not in everything and "summary" not in everything and "payload" not in everything
    assert admin.get("/api/admin/agents/traces/nope").status_code == 404
