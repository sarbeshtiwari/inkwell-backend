"""career-ops features in AI job match: liveness, keyword coverage, role clarity, legitimacy, rules, the A–H
evaluation, statuses, follow-up cadence, insights and the apply gate."""

import time
from datetime import datetime, timedelta

import pytest
from conftest import H, patch_shared

from features import ai_jobs as AI
from features.ai_jobs import evaluate as EV, signals, tracker
from infra import operations

REAL_LIVENESS = signals.liveness


RESUME = ("Sarbesh Tiwari\nFull stack developer with 3 years of experience.\nBuilt a React and Node.js dashboard used by 2,000 "
          "users at Acme.\nDesigned REST APIs in Python and FastAPI backed by MongoDB.\nDeployed services on AWS with Docker.")
JD = ("About the role: You will own the customer dashboard end to end. Reports to the Engineering Manager in the Platform team. "
      "Requirements: 2+ years of experience with React and TypeScript. Strong Node.js and REST API design. Experience with "
      "MongoDB or PostgreSQL. Nice to have: Kubernetes and GraphQL. You will work closely with product and design. "
      "Success looks like shipping the new billing flow in your first 90 days. We ship weekly releases. "
      "Salary: $90,000 - $120,000. " + "We value ownership and clear writing. " * 20)
JOB = {"id": "lv:acme:0001", "ats": "lever", "board": "acme", "title": "Full Stack Engineer", "company": "Acme", "location": "Remote",
       "url": "https://jobs.lever.co/acme/0001", "posted": datetime.now().date().isoformat(), "score": 82,
       "reasons": ["Same kind of role", "Your skills: React, Node.js"], "gaps": [], "can_apply": True, "summary": JD[:400]}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    patch_shared(monkeypatch, "liveness", src=AI, value=lambda job: {"state": "active", "reason": "Still listed on the company's job board"})


@pytest.mark.parametrize("text,state", [("Sorry, this job has expired.", "expired"), ("We are no longer accepting applications", "expired"),
                                        ("This role is closed-loop control of robots. Apply now. " + "x" * 2000, "active"),
                                        ("Just a moment... performing security verification", "uncertain")])
def test_liveness_reads_closure_banners(monkeypatch, text, state):
    class R:
        status_code, ok, url = 200, True, "https://example.com/job/1"
    R.text = f"<html><body>{text}</body></html>"
    monkeypatch.setattr(signals, "ats_alive", lambda job: None)
    monkeypatch.setattr(signals.requests, "get", lambda *a, **k: R())
    assert REAL_LIVENESS({"id": "web:1", "url": "https://example.com/job/1"})["state"] == state


def test_keyword_coverage_uses_synonyms_and_never_invents():
    kw = signals.keyword_coverage(JD, RESUME)
    assert {"React", "MongoDB"} <= set(kw["covered"]) and "Kubernetes" in kw["missing"]
    assert 0 < kw["percent"] < 100


def test_role_clarity_finds_the_six_points():
    rc = signals.role_clarity(JD)
    status = {p["id"]: p["status"] for p in rc["points"]}
    assert status["supervisor"] == "present" and status["success"] == "present" and status["cadence"] == "present"
    assert not rc["ask_before"]
    vague = signals.role_clarity("Join us! Participate in projects, support the team, assist with tasks and other duties as assigned.")
    assert vague["ask_before"] and vague["questions"] and vague["soft_heavy"]


def test_legitimacy_tiers_and_fee_scams():
    assert signals.legitimacy(JOB, JD, {"state": "active", "reason": "x"})["tier"] == "High Confidence"
    old = {**JOB, "posted": (datetime.now() - timedelta(days=200)).date().isoformat()}
    assert signals.legitimacy(old, "Developer wanted.", None, reposts=3)["tier"] == "Suspicious"
    scam = signals.legitimacy(JOB, JD + " You must pay a training fee before joining.")
    assert scam["tier"] == "Suspicious" and scam["notes"]
    assert signals.legitimacy(JOB, JD, {"state": "expired", "reason": "gone"})["tier"] == "Closed"


def test_pay_and_archetype():
    assert signals.pay_range(JOB, "Pay ₹18 - 25 LPA")["high"] == 2_500_000
    assert signals.pay_range(JOB, JD)["currency"] == "USD"
    assert signals.pay_range(JOB, "Pay: $146,200.00 - $219,300.00.")["high"] == 219_300  # a full stop after the number
    a = signals.archetype("Full Stack Engineer", JD, ["Full stack"])
    assert "Full Stack" in a["archetype"] and a["targeted"] is True


def test_evaluate_report_and_status(user, monkeypatch):
    c, ws = user["client"], user["ws"]
    ws.save("ai_match", {"want": {"role": "Full Stack Developer", "years": 3}, "results": [JOB]})
    ws.save("ai_jds", {JOB["id"]: JD})
    monkeypatch.setattr(EV, "resume_for", lambda ws: RESUME)  # the resume normally comes from the run's resume file
    r = c.post(f"/api/ai-jobs/evaluate/{JOB['id']}", json={}, headers=H)
    assert r.status_code == 200
    rep = r.get_json()["report"]
    assert 1 <= rep["score5"] <= 5 and rep["verdict"] and rep["confidence"] in ("High", "Medium", "Low")
    assert len(rep["dimensions"]) == 5 and rep["match"]["rows"] and rep["legitimacy"]["tier"] == "High Confidence"
    assert any(row["evidence"] for row in rep["match"]["rows"])  # quotes a real resume line
    assert all(row["evidence"] in RESUME for row in rep["match"]["rows"] if row["evidence"])
    assert ws.load("ai_pipeline", {})[JOB["id"]]["status"] == "evaluated"
    st = c.get("/api/ai-jobs", headers=H).get_json()
    assert st["results"][0]["report"]["score5"] == rep["score5"] and st["results"][0]["status"] == "evaluated"
    assert c.put(f"/api/ai-jobs/status/{JOB['id']}", json={"status": "interview"}, headers=H).get_json()["status"]["status"] == "interview"
    assert c.put(f"/api/ai-jobs/status/{JOB['id']}", json={"status": "nope"}, headers=H).status_code == 400


def test_closed_postings_are_never_applied_to(user, monkeypatch):
    c, ws = user["client"], user["ws"]
    patch_shared(monkeypatch, "liveness", src=AI, value=lambda job: {"state": "expired", "reason": "No longer listed"})
    called = []
    patch_shared(monkeypatch, "apply_one", src=AI, value=lambda *a, **k: called.append(1) or {"state": "applied"})
    ws.save("ai_match", {"results": [JOB]})
    ws.update_settings(auto_apply_consent=True)
    ws.save("profile", {**ws.profile(), "phone": "+919999999999"})
    assert c.post("/api/ai-jobs/apply", json={"ids": [JOB["id"]]}, headers=H).status_code == 200
    for _ in range(100):
        if not operations.running(ws.uid, "ai_apply"):
            break
        time.sleep(0.05)
    assert not called and ws.load("ai_applied", {})[JOB["id"]]["state"] == "closed"


def test_followup_cadence(user):
    c, ws = user["client"], user["ws"]
    old = (datetime.now() - timedelta(days=9)).isoformat(timespec="seconds")
    fresh = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    ws.save("applications", {
        "a1": {"id": "a1", "company": "Acme", "role": "Engineer", "status": "applied", "applied_at": old, "updated_at": old, "history": []},
        "a2": {"id": "a2", "company": "Beta", "role": "Engineer", "status": "applied", "applied_at": fresh, "updated_at": fresh, "history": []},
        "a3": {"id": "a3", "company": "Gamma", "role": "Dev", "status": "interview", "applied_at": old, "updated_at": fresh,
               "interview_at": fresh, "history": [{"status": "interview", "at": fresh, "from": "Recruiter <jane@gamma.io>"}]}})
    due = {f["id"]: f for f in c.get("/api/ai-jobs/followups", headers=H).get_json()["followups"]}
    assert set(due) == {"a1", "a3"} and due["a1"]["kind"] == "follow_up" and due["a3"]["kind"] == "thank_you"
    assert due["a3"]["to"] == "jane@gamma.io" and "Acme" in due["a1"]["body"]
    left = c.post("/api/ai-jobs/followups/a1", json={"action": "sent"}, headers=H).get_json()["followups"]
    assert "a1" not in {f["id"] for f in left}
    later = tracker.due_followups(ws, today=datetime.now() + timedelta(days=8))
    assert next(f for f in later if f["id"] == "a1")["count"] == 1


def test_cover_letter_drafts_without_a_model(user, monkeypatch):
    c, ws = user["client"], user["ws"]
    ws.save("ai_match", {"want": {"role": "Full Stack Developer", "years": 3}, "results": [JOB]})
    ws.save("ai_jds", {JOB["id"]: JD})
    monkeypatch.setattr(EV, "resume_for", lambda ws: RESUME)
    text = c.post(f"/api/ai-jobs/cover/{JOB['id']}", json={}, headers=H).get_json()["cover"]["text"]
    assert "Acme" in text and "Full Stack Engineer" in text and "React" in text


def test_remote_boards_and_dedup(monkeypatch):
    from types import SimpleNamespace
    from features.ai_jobs import boards
    fake = SimpleNamespace(NAME="X", KIND="search", REGIONS=["global"], fetch=lambda q: [
        {"id": "rm:1", "ats": "", "board": "x", "title": "Full Stack Engineer", "company": "Acme", "location": "Remote",
         "url": "https://x/1", "text": "React Node.js", "posted": ""}])
    monkeypatch.setattr(boards, "search_boards", lambda location="": {"x": fake})
    patch_shared(monkeypatch, "board_jobs", src=AI, value=lambda ats, board: [{**JOB, "text": JD}] if board == "acme" else [])
    patch_shared(monkeypatch, "BOARDS", src=AI, value=[("lever", "acme")])
    jobs, _ = AI.search_sources(AI.make_tools(), ["career_sites", "remote_boards"],
                                {"role": "Full Stack Developer", "company": "", "location": ""})
    assert [j["id"] for j in jobs] == [JOB["id"]]  # the same job from a remote board is dropped; the appliable one stays
    assert jobs[0]["text"].startswith("About the role")


def test_board_readers_follow_the_contract():
    from features.ai_jobs import boards
    mods = boards.load()
    assert len(mods) >= 70
    for name, m in mods.items():
        assert m.NAME and m.KIND in ("company", "search") and m.REGIONS and callable(m.fetch), name
    assert set(boards.search_boards("Bengaluru, India")) >= {"remotive", "hackernews"}
    assert "mycareersfuture" in boards.search_boards("Singapore") and "mycareersfuture" not in boards.search_boards("India")


def test_portal_links_are_recognised():
    from features.ai_jobs.portals import from_link
    assert from_link("https://job-boards.greenhouse.io/stripe/jobs/123")[0] == ("greenhouse", "stripe")
    assert from_link("https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers")[0] == ("workday", "mastercard/wd1/CorporateCareers")
    assert from_link("https://hubstaff.bamboohr.com/careers")[0] == ("bamboohr", "hubstaff")
