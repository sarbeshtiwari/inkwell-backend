"""Agent versioning (task PH1-09): every run records exact versions and the models that answered; versions are
immutable."""

from typing import TypedDict

import pytest
from conftest import make_user
from langgraph.graph import END, START, StateGraph

import app as A
from features.ai_jobs import models
from infra import gateway, runtime, tasks
from infra.gateway import FakeProvider


class S(TypedDict, total=False):
    out: str


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.agent_runs, A.M.agent_versions,
              A.M.model_usage):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    monkeypatch.setitem(gateway.ADAPTERS, "fake", FakeProvider(*["answer"] * 10, model_id="fake-2026-11-01"))


def agent(version="1", instructions="Judge fit honestly.", grants=("rules.read",), calls=1):
    def node(state, config):
        for _ in range(calls):
            models.chat({"provider": "fake", "model": "fake-latest", "uid": runtime.ctx(config).uid, "label": "x"},
                        instructions, "the posting and resume")
        return {"out": "ok"}

    def build():
        g = StateGraph(S)
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)
        return g
    return runtime.register_agent(runtime.Agent("analyst", version, "ins-3", frozenset(grants), build,
                                                instructions=instructions))


def run_all():
    while tasks.run_once("w", lease=5) is not None:
        pass


def test_every_run_records_exact_versions_and_the_models_that_answered():
    uid, _ = make_user()
    agent(calls=2)
    tid = runtime.start(uid, "analyst", {}, mode="on")
    run_all()
    [r] = runtime.runs_for(uid, tid)
    assert (r["agent_id"], r["agent_version"], r["instructions_version"], r["mode"], r["outcome"]) == ("analyst", "1", "ins-3", "on", "done")
    assert r["grants"] == {"rules.read": "1"} and len(r["fingerprint"]) == 64
    assert r["models"] == [{"provider": "fake", "model_id": "fake-2026-11-01", "requested": "fake-latest", "calls": 2}]
    assert r["model_calls"] == 2 and r["input_tokens"] > 0 and r["ended"] >= r["started"]
    usage = list(A.M.model_usage.find({"task_id": tid}))
    assert {u["agent_version"] for u in usage} == {"1"} and {u["agent_id"] for u in usage} == {"analyst"}


def test_a_version_cannot_change_without_a_new_number():
    uid, _ = make_user()
    agent(instructions="Judge fit honestly.")
    runtime.start(uid, "analyst", {})
    run_all()
    agent(instructions="Judge fit generously.")  # same version number, different instructions
    tid = runtime.start(uid, "analyst", {})
    run_all()
    v = tasks.get(uid, tid)
    assert v["status"] == "failed" and v["error_class"] == "version_changed" and v["attempts"] == 1
    agent(version="2", instructions="Judge fit generously.")
    tid2 = runtime.start(uid, "analyst", {})
    run_all()
    assert tasks.get(uid, tid2)["status"] == "done"
    assert sorted(d["_id"] for d in A.M.agent_versions.find()) == ["analyst@1", "analyst@2"]


def test_changing_grants_or_a_tool_version_changes_the_fingerprint():
    a = agent(grants=("rules.read",))
    b = agent(grants=("rules.read", "jobs.rank"))
    assert a.fingerprint() != b.fingerprint()
    assert agent().fingerprint() == a.fingerprint()


def test_each_attempt_of_a_resumed_run_has_its_own_record():
    uid, _ = make_user()
    agent()
    tid = runtime.start(uid, "analyst", {})
    task = tasks.claim("w")
    A.M.agent_runs.insert_one({"_id": "earlier", "uid": uid, "task_id": tid, "attempt": 0, "agent_id": "analyst",
                               "agent_version": "1", "started": A.M.tasks.find_one({"_id": tid})["created_at"],
                               "outcome": "error"})
    tasks.execute(task)
    runs = runtime.runs_for(uid, tid)
    assert [r["outcome"] for r in runs] == ["done", "error"]


def test_runs_are_visible_only_to_their_owner_and_deleted_with_the_account():
    uid, _ = make_user()
    other, _ = make_user()
    agent()
    runtime.start(uid, "analyst", {})
    run_all()
    assert runtime.runs_for(other) == [] and len(runtime.runs_for(uid)) == 1
    A.delete_user_data(uid)
    assert A.M.agent_runs.count_documents({"uid": uid}) == 0
