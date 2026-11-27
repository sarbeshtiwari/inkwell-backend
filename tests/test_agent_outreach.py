"""The Outreach agent: plans an email campaign with the user's own template; sends only after approval."""

import pytest
from conftest import make_user

import app as A
from core import sending
from features import agent_outreach as OUT
from infra import approvals, runtime, tasks


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.approvals, A.M.agent_runs):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    OUT.register()
    started = []
    monkeypatch.setattr(sending, "start_job", lambda ws, cfg: started.append((ws.uid, sorted(cfg["recipient_ids"]), cfg["subject"])))
    yield started


def setup(uid, templates=None):
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("recipients", [
            {"id": "r1", "name": "Asha", "company": "Acme", "email": "asha@acme.example"},
            {"id": "r2", "name": "Ben", "company": "Globex", "email": "ben@globex.example"},
            {"id": "r3", "name": "Cara", "company": "Initech", "email": "cara@initech.example", "email_status": "sent"},
            {"id": "r4", "name": "Dev", "company": "Umbrella", "email": "dev@umbrella.example", "email_status": "bounced"},
            {"id": "r5", "name": "Eve", "company": "Hooli", "email": "eve@hooli.example", "replied_at": "2026-11-01"},
            {"id": "r6", "name": "Fay", "company": "Pied Piper", "email": ""}])
        ws.save("profile", {**ws.profile(), "smtp_password": "app-pass", "email": "me@example.test", "name": "Me"})
    ws.update("templates", lambda _: templates if templates is not None else [
        {"id": "t1", "name": "Draft", "subject": "Hello", "body": "Hi {name}, <say why you're writing>"},
        {"id": "t2", "name": "Intro", "subject": "Hello from {sender_name}", "body": "Hi {name}, I'd love to work at {company}."}], default=[])
    return ws


def run_all():
    while tasks.run_once("w", lease=10) is not None:
        pass


def test_shadow_plans_with_a_ready_template_and_only_new_contacts(_clean):
    uid, _ = make_user()
    setup(uid)
    tid = runtime.start(uid, "outreach", {"limit": 10}, mode="shadow")
    run_all()
    r = tasks.get(uid, tid)["result"]
    assert r["chosen"] == ["r1", "r2"]                                      # not sent, bounced, replied or no email
    assert r["outcome"]["state"] == "would_ask_approval" and r["template"]["id"] == "t2"  # t1 still has <placeholders>
    assert r["preview"][0]["body"] == "Hi Asha, I'd love to work at Acme."
    assert _clean == [] and approvals.list_for(uid) == []
    assert "asha@acme.example" not in repr(r["candidates"])                 # agents don't see addresses


def test_on_mode_sends_only_the_approved_campaign_once(_clean):
    uid, _ = make_user()
    setup(uid)
    tid = runtime.start(uid, "outreach", {}, mode="on")
    run_all()
    [p] = approvals.list_for(uid, status="pending")
    assert p["proposed"] == {"template_id": "t2", "recipient_ids": ["r1", "r2"]} and _clean == []
    approvals.decide(uid, p["id"], "approve", {"template_id": "t2", "recipient_ids": ["r2"]})  # the user trims the list
    run_all()
    assert _clean == [(uid, ["r2"], "Hello from {sender_name}")]
    assert tasks.get(uid, tid)["result"]["outcome"]["state"] == "started"


def test_declined_campaign_sends_nothing_and_no_ready_template_is_reported(_clean):
    uid, _ = make_user()
    setup(uid)
    tid = runtime.start(uid, "outreach", {}, mode="on")
    run_all()
    approvals.decide(uid, approvals.list_for(uid, status="pending")[0]["id"], "reject")
    run_all()
    assert _clean == [] and tasks.get(uid, tid)["result"]["outcome"]["state"] == "declined"
    other, _ = make_user()
    setup(other, templates=[{"id": "t1", "name": "Draft", "subject": "Hi", "body": "Hi {name}, <say why>"}])
    tid = runtime.start(other, "outreach", {}, mode="on")
    run_all()
    assert tasks.get(other, tid)["result"]["outcome"] == {"state": "nothing_to_send", "detail": "no ready template"}
    assert approvals.list_for(other) == []
