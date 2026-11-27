"""The Communicator agent: handles replies to the user's emails; answers only with approval."""

import json
import time
import uuid

import pytest
from conftest import make_user

import app as A
from features import agent_communicator as comm, replies as R
from infra import approvals, gateway, model_config, rbac, runtime, tasks
from infra.gateway import Reply


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.approvals, A.M.agent_runs,
              A.M.model_configs):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    comm.register()
    sent = []
    monkeypatch.setattr(R, "send_answer", lambda ws, key, p: sent.append((ws.uid, key, p["to"], p["body"])) or "2026-11-27T12:00:00")
    monkeypatch.setattr(R, "draft_reply", lambda ws, rec: {"to": rec["addr"], "subject": "Re: " + rec["subject"],
        "body": "Thanks! My resume is attached." if "resume" in rec["text"] else "[write your answer]",
        "documents": ["resume.pdf"] if "resume" in rec["text"] else [], "missing": [] if "resume" in rec["text"] else ["answer"]})
    yield sent


def add_reply(uid, intent, text, handled=False):
    key = uuid.uuid4().hex
    A.M.replies.insert_one({"uid": uid, "mid": key, "ts": time.time(), "auto": False, "handled": handled, "intent": intent,
                            "data": A.seal({"from": "Priya", "addr": f"{intent}@corp.example", "subject": "Your application",
                                            "text": text, "info": {"intents": [intent], "questions": [], "asks": []}})})
    return key


def run_all():
    while tasks.run_once("w", lease=10) is not None:
        pass


def test_shadow_mode_reports_what_it_would_do_and_changes_nothing(_clean):
    uid, _ = make_user()
    a = add_reply(uid, "asks_resume", "Could you share your resume?")
    b = add_reply(uid, "not_interested", "We went with someone else.")
    tid = runtime.start(uid, "communicator", {}, mode="shadow")
    run_all()
    out = {o["id"]: o for o in tasks.get(uid, tid)["result"]["outcomes"]}
    assert out[a]["would"] == "answer" and out[a]["draft"]["body"].startswith("Thanks") and out[b]["would"] == "set_aside"
    assert _clean == [] and approvals.list_for(uid) == []
    assert A.M.replies.count_documents({"uid": uid, "handled": True}) == 0


def test_on_mode_answers_only_after_approval_and_sets_aside_the_rest(_clean):
    uid, _ = make_user()
    a = add_reply(uid, "asks_resume", "Could you share your resume?")
    b = add_reply(uid, "not_interested", "We went with someone else.")
    c = add_reply(uid, "question", "What is your notice period?")         # draft has a gap only the user can fill
    tid = runtime.start(uid, "communicator", {}, mode="on")
    run_all()
    [p] = approvals.list_for(uid, status="pending")
    assert p["tool_id"] == "replies.send" and p["proposed"]["id"] == a and _clean == []
    approvals.decide(uid, p["id"], "approve")
    run_all()
    assert _clean == [(uid, a, "asks_resume@corp.example", "Thanks! My resume is attached.")]
    out = {o["id"]: o["state"] for o in tasks.get(uid, tid)["result"]["outcomes"]}
    assert out == {a: "sent", b: "set_aside", c: "left_for_you"}
    assert A.M.replies.find_one({"mid": b})["handled"] is True and A.M.replies.find_one({"mid": c})["handled"] is False


def test_a_declined_draft_is_not_proposed_again(_clean):
    uid, _ = make_user()
    a = add_reply(uid, "asks_resume", "Could you share your resume?")
    runtime.start(uid, "communicator", {}, mode="on")
    run_all()
    approvals.decide(uid, approvals.list_for(uid, status="pending")[0]["id"], "reject")
    run_all()
    assert _clean == []
    tid = runtime.start(uid, "communicator", {}, mode="on")
    run_all()
    assert approvals.list_for(uid, status="pending") == [] and tasks.get(uid, tid)["result"]["outcomes"] == []
    assert A.M.replies.find_one({"mid": a})["handled"] is False          # still yours to answer by hand


def test_the_model_decides_among_the_allowed_options_only(_clean, monkeypatch):
    uid, _ = make_user()
    admin, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", "sk-ant-test-key-00000000000000000000")
    model_config.set_model(admin, "anthropic", "claude-sonnet-5-5", "agents model")
    answers = iter([{"do": "leave", "why": "salary question, only you can answer"}, {"do": "delete_everything"}])
    monkeypatch.setitem(gateway.ADAPTERS, "anthropic", lambda req: Reply(json.dumps(next(answers)), model_id="m"))
    a = add_reply(uid, "asks_resume", "Could you share your resume? And your salary?")
    b = add_reply(uid, "not_interested", "No thanks.")
    tid = runtime.start(uid, "communicator", {}, mode="shadow")
    run_all()
    out = {o["id"]: o for o in tasks.get(uid, tid)["result"]["outcomes"]}
    # the model was asked once per reply; one answer was valid, the other ("delete_everything") fell back to the rule
    assert sorted(o["would"] for o in out.values()) == sorted(["leave", "answer" if out[a]["would"] == "answer" else "set_aside"])
    assert "delete_everything" not in json.dumps(out) and all(o["would"] in ("answer", "set_aside", "leave") for o in out.values())


def test_only_the_owners_replies_are_seen(_clean):
    uid, _ = make_user()
    other, _ = make_user()
    add_reply(other, "asks_resume", "Could you share your resume?")
    tid = runtime.start(uid, "communicator", {}, mode="shadow")
    run_all()
    assert tasks.get(uid, tid)["result"]["outcomes"] == []
