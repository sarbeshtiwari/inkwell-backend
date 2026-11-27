"""Job Alerts and Weekly Digest agents take over those background jobs for accounts with agents On."""

import json
from datetime import datetime

import pytest
from conftest import make_user

import app as A
from features import agent_career as AC, jobs as J
from features.ai_jobs import extras
from infra import gateway, model_config, rbac, runtime, tasks
from infra.gateway import Reply


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.agent_runs, A.M.schedules,
              A.M.model_configs):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    AC.register()
    scans = []
    monkeypatch.setattr(J, "scan_alerts", lambda ws, days=7, limit=300: scans.append(ws.uid) or {"added": 2})
    yield scans


def account(mode, jobs=True):
    uid, _ = make_user()
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("profile", {**ws.profile(), "smtp_password": "x"})
        ws.save("job_prefs", {**J.prefs(ws), "auto_scan": True, "roles": ["Backend Developer"], "skills": ["Python"], "min_score": 10})
        if jobs:
            ws.save("jobs", {"j1": {"title": "Backend Developer", "company": "Acme", "snippet": "Python Django", "state": "new"},
                             "j2": {"title": "Sales Executive", "company": "Globex", "snippet": "cold calls", "state": "new"},
                             "j3": {"title": "Backend Engineer", "company": "Old", "snippet": "Python", "state": "dismissed"}})
        ws.save("ai_match", {"results": [{"id": "m1", "score": 80}, {"id": "m2", "score": 50}]})
        if mode:
            ws.save("agent_mode", {"mode": mode})
    return uid


def notes(uid):
    return [A.unseal(n["data"], {}) for n in A.M.notifications.find({"uid": uid})]


def run_all():
    while tasks.run_once("w", lease=10) is not None:
        pass


def test_job_alert_scan_hands_on_accounts_to_the_agent(_clean):
    on, plain = account("on"), account(None)
    J.auto_scan_all()
    assert plain in _clean and on not in _clean
    run_all()
    assert on in _clean
    t = A.M.tasks.find_one({"uid": on, "kind": "agent.job_alerts"})
    assert t["status"] == "done"
    [n] = notes(on)
    assert "Backend Developer at Acme" in n["body"] and "Sales" not in n["body"]
    jobs = A.Workspace(on).load("jobs", {})
    assert jobs["j1"]["state"] == "new" and jobs["j1"]["agent"]["seen"] and "agent" not in jobs["j3"]
    tid = runtime.start(on, "job_alerts", {}, mode="on")
    run_all()
    assert tasks.get(on, tid)["result"]["jobs"] == []              # already looked at: not announced again


def test_model_picks_only_among_found_jobs(_clean, monkeypatch):
    uid = account("on")
    admin, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", "sk-ant-test-key-00000000000000000000")
    model_config.set_model(admin, "anthropic", "claude-sonnet-5-5", "agents model")
    monkeypatch.setitem(gateway.ADAPTERS, "anthropic", lambda req: Reply(json.dumps({"ids": ["j2", "made-up"], "why": "x"}), model_id="m"))
    tid = runtime.start(uid, "job_alerts", {}, mode="shadow")
    run_all()
    assert tasks.get(uid, tid)["result"]["picks"] == ["j2"] and notes(uid) == []   # shadow: nothing sent


def test_weekly_digest_hands_on_accounts_to_the_agent_once_a_week(_clean, monkeypatch):
    class Monday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 30, 9, 0, tzinfo=tz)
    monkeypatch.setattr(extras, "datetime", Monday)
    monkeypatch.setattr(AC, "datetime", Monday)
    on, plain = account("on"), account(None)
    extras.weekly_digest_all()
    assert len(notes(plain)) == 1 and notes(on) == []
    run_all()
    [n] = notes(on)
    assert n["title"] == "Your job search this week" and n["body"].startswith("1 strong matches")
    extras.weekly_digest_all()
    run_all()
    assert len(notes(on)) == 1 and A.M.tasks.count_documents({"uid": on, "kind": "agent.weekly_digest"}) == 1
