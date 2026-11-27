"""Background jobs under agent control: for accounts with agents On, the reply check and the morning sync hand their
work to agents; every other account keeps the plain jobs, unchanged."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import make_user

import app as A
from features import agent_communicator, agent_inbox, agent_morning, replies as R
from features.apps import sync as S
from infra import runtime, tasks


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.agent_runs, A.M.schedules):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    for m in (agent_communicator, agent_inbox, agent_morning):
        m.register()
    calls = {"scan": [], "sync_all": [], "start_sync": [], "inbox_scan": []}
    monkeypatch.setattr(R, "scan", lambda ws, first_days=21: calls["scan"].append(ws.uid) or {"found": 0})
    monkeypatch.setattr(S, "start_sync", lambda uid, full=False, reason="manual": calls["start_sync"].append(uid) or True)
    monkeypatch.setattr(S, "sync_all", lambda uid, full=False, reason="manual", op=None: calls["sync_all"].append((uid, reason)))
    from features import inbox as IB
    monkeypatch.setattr(IB, "scan", lambda ws, full=False, op=None: calls["inbox_scan"].append(ws.uid) or {"added": 0})
    yield calls


def account(mode):
    uid, _ = make_user()
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("profile", {**ws.profile(), "smtp_password": "app-pass", "email": f"{uid[:6]}@example.test"})
        if mode:
            ws.save("agent_mode", {"mode": mode})
    A.M.send_log.insert_one({"uid": uid, "ts": time.time(), "day": "2026-11-27", "data": A.seal({})})
    return uid


def run_all():
    while tasks.run_once("w", lease=10) is not None:
        pass


def test_reply_check_goes_to_the_communicator_only_for_accounts_with_agents_on(_clean):
    on, plain, off = account("on"), account("shadow"), account("off")
    R.tick()
    assert {plain, off} <= set(_clean["scan"]) and on not in _clean["scan"]  # plain watcher, unchanged
    [t] = list(A.M.tasks.find({"kind": "agent.communicator", "uid": on}))
    assert t["uid"] == on and A.unseal(t["input"], {})["input"] == {"check": True}
    run_all()
    assert on in _clean["scan"]                                    # the agent read the mailbox itself
    R.tick()
    assert A.M.tasks.count_documents({"kind": "agent.communicator", "uid": on}) == 1  # once per check interval, not every tick


def test_morning_sync_goes_to_the_morning_agent_only_for_accounts_with_agents_on(_clean, monkeypatch):
    on, plain = account("on"), account(None)
    for uid in (on, plain):
        ws = A.Workspace(uid)
        with ws.lock:
            ws.save("apps_sync_prefs", {"auto": True, "time": "00:00", "tz": "UTC"})
    S.daily_tick()
    mine = lambda xs: [x for x in xs if x in (on, plain)]
    assert mine(_clean["start_sync"]) == [plain]
    run_all()
    m = A.M.tasks.find_one({"kind": "agent.morning", "uid": on})
    assert m["status"] == "done" and [x for x in _clean["sync_all"] if x[0] in (on, plain)] == [(on, "agent")]
    kids = [k["agent_id"] for k in tasks.children(on, m["_id"])]
    assert kids == ["inbox_watcher", "communicator"] and all(k["status"] == "done" for k in tasks.children(on, m["_id"]))
    S.daily_tick()
    assert A.M.tasks.count_documents({"kind": "agent.morning", "uid": on}) == 1 and mine(_clean["start_sync"]) == [plain]


def test_a_failed_hand_off_falls_back_to_the_plain_job(_clean, monkeypatch):
    on = account("on")
    monkeypatch.setattr(runtime, "AGENTS", {})                     # e.g. agents not loaded in this process
    R.tick()
    assert on in _clean["scan"] and A.M.tasks.count_documents({"uid": on}) == 0
