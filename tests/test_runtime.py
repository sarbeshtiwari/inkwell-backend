"""Agent runtime (task PH1-07): LangGraph on the task queue with a sealed, per-user MongoDB checkpointer."""

import operator
import time
from typing import Annotated, TypedDict

import pytest
from conftest import make_user
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph

import app as A
from infra import runtime, tasks, tools
from infra.tools import READ, Tool


class State(TypedDict, total=False):
    goal: str
    log: Annotated[list, operator.add]
    rules: dict


class WorkerDied(BaseException):
    """Simulates the worker process dying mid-run (not an ordinary error: nothing gets recorded)."""


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    yield
    tools.REGISTRY.pop("test.echo_rules", None)


CALLS = []


def two_step_agent(crash_once=None, steps=25, agent_id="test_agent"):
    def first(state, config):
        CALLS.append("first")
        runtime.checkpoint(config)
        rules = tools.call(runtime.ctx(config), "testing.notes")
        return {"log": [f"first saw {state['goal']}"], "rules": rules}

    def second(state, config):
        CALLS.append("second")
        if crash_once is not None and not crash_once:
            crash_once.append(1)
            raise WorkerDied()
        return {"log": ["second"]}

    def build():
        g = StateGraph(State)
        g.add_node("first", first)
        g.add_node("second", second)
        g.add_edge(START, "first")
        g.add_edge("first", "second")
        g.add_edge("second", END)
        return g
    return runtime.register_agent(runtime.Agent(agent_id, "1", "i1", frozenset({"testing.notes"}), build, max_steps=steps))


def run_all(lease=5):
    out = []
    while (r := tasks.run_once("w", lease=lease)) is not None:
        out.append(r)
    return out


def test_agent_runs_as_a_task_with_tools_scoped_to_its_owner():
    CALLS.clear()
    uid, _ = make_user()
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("test_notes", {"blacklist": ["only-this-user"]})
    two_step_agent()
    tid = runtime.start(uid, "test_agent", {"goal": "find roles", "log": []})
    assert run_all() == [(tid, "done")]
    res = tasks.get(uid, tid)["result"]
    assert res["log"] == ["first saw find roles", "second"] and res["rules"]["blacklist"] == ["only-this-user"]
    assert CALLS == ["first", "second"]
    t = A.M.tasks.find_one({"_id": tid})
    assert t["kind"] == "agent.test_agent" and t["agent_id"] == "test_agent"


def test_a_crashed_run_resumes_from_its_checkpoint_without_repeating_finished_steps():
    CALLS.clear()
    uid, _ = make_user()
    two_step_agent(crash_once=[])
    tid = runtime.start(uid, "test_agent", {"goal": "g", "log": []})
    task = tasks.claim("doomed", lease=0.3)
    with pytest.raises(WorkerDied):
        tasks.execute(task)  # the worker dies inside "second"
    assert A.M.tasks.find_one({"_id": tid})["status"] == "running"  # nothing recorded by the dead worker
    time.sleep(0.4)  # its lease runs out
    assert run_all() == [(tid, "done")]
    assert CALLS == ["first", "second", "second"]  # "first" ran once: the resumed run started after it
    assert tasks.get(uid, tid)["result"]["log"] == ["first saw g", "second"]
    assert tasks.get(uid, tid)["attempts"] == 2


def test_checkpoints_are_sealed_and_scoped_to_their_user():
    uid, _ = make_user()
    other, _ = make_user()
    two_step_agent()
    tid = runtime.start(uid, "test_agent", {"goal": "PRIVATE-GOAL-TEXT", "log": []})
    run_all()
    docs = list(A.M.checkpoints.find({"thread_id": tid})) + list(A.M.checkpoint_writes.find({"thread_id": tid}))
    assert docs and all(d["uid"] == uid for d in docs)
    assert "PRIVATE-GOAL-TEXT" not in repr(docs)
    mine = runtime.MongoCheckpointer(uid).get_tuple({"configurable": {"thread_id": tid}})
    assert mine and mine.checkpoint["channel_values"]["goal"] == "PRIVATE-GOAL-TEXT"
    theirs = runtime.MongoCheckpointer(other)
    assert theirs.get_tuple({"configurable": {"thread_id": tid}}) is None
    assert list(theirs.list({"configurable": {"thread_id": tid}})) == [] and list(theirs.list(None)) == []
    theirs.delete_thread(tid)  # can't delete someone else's either
    assert A.M.checkpoints.count_documents({"thread_id": tid}) == len([d for d in docs if "checkpoint" in d])
    with pytest.raises(ValueError):
        runtime.MongoCheckpointer("")


def test_checkpointer_contract_put_get_list_writes():
    uid, _ = make_user()
    s = runtime.MongoCheckpointer(uid)
    base = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
    c1 = empty_checkpoint()
    cfg1 = s.put(base, c1, {"step": 0, "source": "input"}, {})
    c2 = {**empty_checkpoint(), "id": c1["id"][:-1] + "z"}
    cfg2 = s.put(cfg1, c2, {"step": 1, "source": "loop"}, {})
    s.put_writes(cfg2, [("log", ["a"]), ("goal", "x")], "task-1")
    s.put_writes(cfg2, [("log", ["a"])], "task-1")  # a retried step doesn't duplicate its writes
    latest = s.get_tuple(base)
    assert latest.config["configurable"]["checkpoint_id"] == c2["id"]
    assert latest.parent_config["configurable"]["checkpoint_id"] == c1["id"]
    assert latest.pending_writes == [("task-1", "log", ["a"]), ("task-1", "goal", "x")]
    assert s.get_tuple(cfg1).config["configurable"]["checkpoint_id"] == c1["id"]
    assert [t.metadata["step"] for t in s.list(base)] == [1, 0]
    assert [t.metadata["step"] for t in s.list(base, filter={"source": "loop"})] == [1]
    assert [t.metadata["step"] for t in s.list(base, before=cfg2)] == [0]
    assert len(list(s.list(base, limit=1))) == 1
    s.delete_thread("t1")
    assert s.get_tuple(base) is None


def test_step_limit_fails_the_task_without_retry():
    uid, _ = make_user()

    def loop(state, config):
        return {"log": ["again"]}

    def build():
        g = StateGraph(State)
        g.add_node("loop", loop)
        g.add_edge(START, "loop")
        g.add_conditional_edges("loop", lambda s: "loop")
        return g
    runtime.register_agent(runtime.Agent("looper", "1", "i1", frozenset(), build, max_steps=5))
    tid = runtime.start(uid, "looper", {"log": []}, max_attempts=3)
    assert run_all() == [(tid, "failed")]
    v = tasks.get(uid, tid)
    assert v["error_class"] == "max_steps" and v["attempts"] == 1


def test_cancel_stops_a_run_at_the_next_node_checkpoint():
    CALLS.clear()
    uid, _ = make_user()
    two_step_agent()
    tid = runtime.start(uid, "test_agent", {"goal": "g", "log": []})
    A.M.tasks.update_one({"_id": tid}, {"$set": {"cancel_requested": True}})
    task = tasks.claim("w") or None
    assert task is None  # a cancel requested before the claim keeps it from starting at all
    A.M.tasks.update_one({"_id": tid}, {"$set": {"cancel_requested": False}})
    task = tasks.claim("w")
    A.M.tasks.update_one({"_id": tid}, {"$set": {"cancel_requested": True}})
    assert tasks.execute(task) == "cancelled" and CALLS == ["first"]


def test_agents_can_only_be_granted_tools_that_exist_and_shadow_is_the_default():
    with pytest.raises(ValueError, match="don't exist"):
        runtime.register_agent(runtime.Agent("x", "1", "i", frozenset({"made.up"}), lambda: None))
    with pytest.raises(ValueError):
        runtime.register_agent(runtime.Agent("Bad-Id", "1", "i", frozenset(), lambda: None))
    uid, _ = make_user()
    two_step_agent()
    tid = runtime.start(uid, "test_agent", {"goal": "g", "log": []})
    assert A.M.tasks.find_one({"_id": tid}) and tasks.get(uid, tid)["input"]["mode"] == "shadow"
    with pytest.raises(ValueError):
        runtime.start(uid, "test_agent", {}, mode="live")
    with pytest.raises(ValueError):
        runtime.start(uid, "nobody", {})


def test_delegated_agent_run_is_a_child_task_in_the_same_trace():
    uid, _ = make_user()
    two_step_agent(agent_id="parent_agent")
    two_step_agent(agent_id="child_agent")
    parent = runtime.start(uid, "parent_agent", {"goal": "parent", "log": []})
    with pytest.raises(tasks.DelegationRefused, match="cycle"):
        runtime.start(uid, "parent_agent", {"goal": "itself", "log": []}, parent_id=parent)
    child = runtime.start(uid, "child_agent", {"goal": "child", "log": []}, parent_id=parent)
    with pytest.raises(tasks.DelegationRefused, match="cycle"):
        runtime.start(uid, "parent_agent", {"goal": "back up", "log": []}, parent_id=child)
    c = A.M.tasks.find_one({"_id": child})
    assert c["trace_id"] == parent and c["agent_chain"] == ["parent_agent"] and c["depth"] == 1


def test_account_deletion_removes_checkpoints():
    uid, _ = make_user()
    two_step_agent()
    runtime.start(uid, "test_agent", {"goal": "g", "log": []})
    run_all()
    A.delete_user_data(uid)
    assert A.M.checkpoints.count_documents({"uid": uid}) == 0 and A.M.checkpoint_writes.count_documents({"uid": uid}) == 0
