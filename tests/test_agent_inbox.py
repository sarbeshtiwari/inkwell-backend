"""The Inbox agent: sorts new job mail and tells the user what needs them; never replies or contacts anyone."""

import json
import time
import uuid

import pytest
from conftest import make_user

import app as A
from features import agent_inbox as AI_, inbox as IB
from infra import gateway, model_config, rbac, runtime, tasks
from infra.gateway import Reply


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.agent_runs, A.M.model_configs):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    AI_.register()
    synced = []
    monkeypatch.setattr(IB, "scan", lambda ws, full=False, op=None: synced.append(ws.uid) or {"added": 2})
    yield synced


def mail(uid, cat, subject, company="Acme", ago=60):
    A.M.mail_index.insert_one({"uid": uid, "mid": uuid.uuid4().hex[:24], "ts": time.time() - ago, "key": "k", "cat": cat,
                               "data": A.seal({"company": company, "from": "HR", "addr": "hr@acme.example",
                                               "subject": subject, "snippet": "Please pick a slot"})})


def run_all():
    while tasks.run_once("w", lease=10) is not None:
        pass


def notes_of(uid):
    return [A.unseal(n["data"], {}) for n in A.M.notifications.find({"uid": uid})]


def test_on_mode_syncs_sorts_and_sends_one_summary_then_remembers(_clean):
    uid, _ = make_user()
    mail(uid, "interview", "Interview invitation")
    mail(uid, "rejection", "Update on your application", "Globex")
    mail(uid, "newsletter", "Weekly deals")                       # not job mail: ignored
    tid = runtime.start(uid, "inbox_watcher", {}, mode="on")
    run_all()
    assert _clean == [uid]
    r = tasks.get(uid, tid)["result"]
    assert {(m["category"], m["priority"]) for m in r["sorted"]} == {("interview", "urgent"), ("rejection", "fyi")}
    [n] = notes_of(uid)
    assert n["title"] == "Inbox: 1 urgent, 1 to know" and n["body"].startswith("Urgent: Acme: Interview invitation")
    tid2 = runtime.start(uid, "inbox_watcher", {}, mode="on")
    run_all()
    assert tasks.get(uid, tid2)["result"]["sorted"] == []        # nothing new since it last looked


def test_shadow_mode_syncs_nothing_notifies_no_one_remembers_nothing(_clean):
    uid, _ = make_user()
    mail(uid, "offer", "Your offer letter")
    tid = runtime.start(uid, "inbox_watcher", {}, mode="shadow")
    run_all()
    assert _clean == [] and notes_of(uid) == []
    assert tasks.get(uid, tid)["result"]["sorted"][0]["priority"] == "urgent"
    assert A.Workspace(uid).load("agent_inbox", None) is None


def test_the_model_sorts_among_the_three_choices_only(_clean, monkeypatch):
    uid, _ = make_user()
    admin, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", "sk-ant-test-key-00000000000000000000")
    model_config.set_model(admin, "anthropic", "claude-sonnet-5-5", "agents model")
    monkeypatch.setitem(gateway.ADAPTERS, "anthropic", lambda req: Reply(json.dumps(
        {"priority": "urgent", "why": "asks for a reply by Friday"} if "recruiter" in req.human else {"priority": "panic"}), model_id="m"))
    mail(uid, "recruiter", "Opening at Acme")
    mail(uid, "job_alert", "10 new jobs")
    tid = runtime.start(uid, "inbox_watcher", {}, mode="shadow")
    run_all()
    got = {m["category"]: m["priority"] for m in tasks.get(uid, tid)["result"]["sorted"]}
    assert got == {"recruiter": "urgent", "job_alert": "skip"}    # invalid model answer fell back to the category


def test_only_the_owners_mail_is_read(_clean):
    uid, _ = make_user()
    other, _ = make_user()
    mail(other, "offer", "Offer for someone else")
    tid = runtime.start(uid, "inbox_watcher", {}, mode="shadow")
    run_all()
    assert tasks.get(uid, tid)["result"]["sorted"] == []
