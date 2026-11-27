"""First workflow, read-only part (tasks PH2-01 Orchestrator, PH2-02 Job Scout, PH2-03 Job Analyst), in shadow mode."""

import json

import pytest
from conftest import make_user

import app as A
from features.ai_jobs import agent_tools, agents, signals
from infra import approvals, gateway, runtime, tasks, tools
from infra.gateway import Reply

RESUME = "Backend developer, 4 years: Python, Django, PostgreSQL, Docker, AWS. Built REST APIs."


def job(i, title, company, text="Python Django PostgreSQL REST APIs, 3+ years.", ats="greenhouse"):
    return {"id": f"gh:{company.lower()}:{i}", "title": title, "company": company, "location": "Remote",
            "url": f"https://boards.example/{company}/{i}", "ats": ats, "text": text, "posted": ""}


BOARD = [job(1, "Backend Engineer", "Acme"), job(2, "Senior Backend Developer", "Globex"),
         job(3, "Python Developer", "Initech"), job(4, "Backend Engineer", "Blocked Co"),
         job(5, "Backend Engineer", "Gone Inc"), job(6, "Sales Manager", "Acme", "quotas and CRM"),
         job(7, "Backend Developer", "Contracty", "Python Django. This is a fixed-term contract role.")]


class FakeTool:
    def __init__(self, log, source, jobs):
        self.log, self.source, self.jobs = log, source, jobs

    def invoke(self, args):
        self.log.append((self.source, args["role"]))
        return [dict(j) for j in self.jobs]


@pytest.fixture
def world(monkeypatch):
    for c in (A.M.tasks, A.M.task_events, A.M.checkpoints, A.M.checkpoint_writes, A.M.agent_runs, A.M.agent_versions,
              A.M.approvals, A.M.model_usage, A.M.traces, A.M.spans):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    monkeypatch.setattr(runtime, "AGENTS", {})
    agents.register()
    searches = []
    w = {"searches": searches, "board": list(BOARD), "remote": []}
    monkeypatch.setattr(agent_tools, "make_tools", lambda location="": {
        "career_sites": FakeTool(searches, "career_sites", w["board"]),
        "remote_boards": FakeTool(searches, "remote_boards", w["remote"])})
    monkeypatch.setattr(signals, "ats_alive", lambda j: "gone" not in j["id"])
    monkeypatch.setattr(agent_tools, "_profile", lambda ws, role, years, location="", company="", resume_name="": {
        "role": role, "years": years, "location": location, "company": company, "resume": RESUME,
        "skills": ["Python", "Django", "PostgreSQL", "Docker", "AWS"]})
    monkeypatch.setattr(agents, "_model", lambda config: None)  # no model unless a test sets one
    uid, _ = make_user()
    w["uid"] = uid
    return w


def with_model(monkeypatch, answer):
    """A fake model whose answer depends on which decision it's asked for."""
    calls = []

    def adapter(req):
        calls.append(req.system)
        return Reply(json.dumps(answer(req.system, req.human)), model_id="fake-planner-1", input_tokens=50, output_tokens=20)
    monkeypatch.setitem(gateway.ADAPTERS, "fake", adapter)
    monkeypatch.setattr(agents, "_model", lambda config: {"provider": "fake", "model": "m", "label": "x",
                                                          "uid": runtime.ctx(config).uid})
    return calls


def run_all(limit=200):
    for _ in range(limit):
        if tasks.run_once("w", lease=10) is None:
            return


def start(uid, **inp):
    return runtime.start(uid, "orchestrator", inp)


def report(uid, tid):
    v = tasks.get(uid, tid)
    assert v["status"] == "done", v
    return v["result"]["report"]


def test_whole_workflow_without_a_model(world):
    uid = world["uid"]
    before = A.Workspace(uid).load("ai_match", None)
    tid = start(uid, goal="find backend roles", criteria={"role": "Backend Developer", "years": 4, "count": 5})
    run_all()
    r = report(uid, tid)
    ids = [p["id"] for p in r["recommended"]]
    assert "gh:acme:1" in ids and "gh:globex:2" in ids
    assert "gh:acme:6" not in ids                                     # not the role: filtered by the Scout
    assert "gh:gone inc:5" not in ids and r["found"] == 5             # closed posting dropped
    assert all(p["score"] >= 50 for p in r["recommended"])
    assert r["problems"] == [] and r["mode"] == "shadow" and any("no model set up" in n for n in r["analyst_notes"])
    kids = tasks.children(uid, tid)
    assert [k["agent_id"] for k in kids] == ["job_scout", "job_analyst", "writer"] and all(k["status"] == "done" for k in kids)
    assert {k["trace_id"] for k in kids} == {tid} and all(k["depth"] == 1 for k in kids)
    # shadow and read-only: no approvals, nothing saved to the user's own match results
    assert approvals.list_for(uid) == [] and A.Workspace(uid).load("ai_match", None) == before
    ev = [e["event"] for e in tasks.events(uid, tid)]
    assert ev.count("waiting") == 3 and ev.count("resumed") == 3
    runs = runtime.runs_for(uid)
    assert {r_["agent_id"] for r_ in runs} == {"orchestrator", "job_scout", "job_analyst", "writer"}


def test_the_model_reads_the_goal_plans_the_search_and_reviews_the_shortlist(world, monkeypatch):
    uid = world["uid"]

    def answer(system, human):
        if "search criteria" in system:
            return {"role": "Backend Developer", "years": 4, "location": "", "count": 3, "research": False, "prepare": True, "apply": False,
                             "match": False, "company": "", "resume": "", "sources": [], "review": True}
        if "plan a job search" in system:
            return {"searches": [{"source": "career_sites", "role": "Backend Engineer"}], "why": "ATS boards first"}
        return {"fit": 20 if "Initech" in human else 85, "verdict": "Strong match on Django APIs", "red_flags": []}
    calls = with_model(monkeypatch, answer)
    tid = start(uid, goal="Find me 3 backend jobs that use Django")
    run_all()
    r = report(uid, tid)
    assert r["criteria"] == {"role": "Backend Developer", "years": 4.0, "location": "", "count": 3, "research": False, "prepare": True, "apply": False,
                             "match": False, "company": "", "resume": "", "sources": [], "review": True}
    assert world["searches"][0] == ("career_sites", "Backend Engineer")       # the model's plan was followed
    assert len(world["searches"]) == len(set(world["searches"]))              # no search is ever repeated
    assert r["scout_notes"][0] == "Round 1: ATS boards first"
    rec = r["recommended"]
    assert len(rec) <= 3 and all(p.get("verdict") == "Strong match on Django APIs" for p in rec)
    assert "gh:initech:3" not in [p["id"] for p in rec]                       # the model's low fit counted
    by_agent = {u["agent_id"] for u in A.M.model_usage.find({"uid": uid})}
    assert by_agent == {"orchestrator", "job_scout", "job_analyst"}           # usage attributed per agent
    assert len(calls) >= 3


def test_a_bad_model_answer_falls_back_to_rules(world, monkeypatch):
    uid = world["uid"]
    with_model(monkeypatch, lambda s, h: {"searches": [{"source": "linkedin_scrape", "role": "x"}], "fit": "high"})
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4})
    run_all()
    r = report(uid, tid)
    assert ("linkedin_scrape", "x") not in world["searches"] and ("career_sites", "Backend Developer") in world["searches"]
    assert r["recommended"] and all("verdict" not in p for p in r["recommended"])


def test_the_scout_refines_its_search_when_too_few_jobs_turn_up(world):
    uid = world["uid"]
    world["board"] = [job(1, "Backend Engineer", "Acme")]
    world["remote"] = [job(10 + i, "Backend Developer", f"Remote{i}") for i in range(3)]
    tid = start(uid, criteria={"role": "Senior Backend Developer", "years": 6})
    run_all()
    rounds = [s for s in world["searches"]]
    assert ("career_sites", "Senior Backend Developer") in rounds and ("remote_boards", "Backend Developer") in rounds
    scout = tasks.children(uid, tid)[0]["result"]
    assert scout["round"] == 2 and len(scout["notes"]) == 3


def test_a_failed_child_is_reported_not_hidden(world, monkeypatch):
    import dataclasses
    uid = world["uid"]

    def broken(ctx, **kw):
        raise tasks.Permanent("search backend down", "search_down")
    monkeypatch.setitem(tools.REGISTRY, "jobs.search", dataclasses.replace(tools.REGISTRY["jobs.search"], fn=broken))
    tid = start(uid, criteria={"role": "Backend Developer"})
    run_all()
    r = report(uid, tid)
    assert r["recommended"] == [] and r["problems"] and "Job search failed" in r["problems"][0]
    assert tasks.children(uid, tid)[0]["error_class"] == "search_down"


def test_no_role_and_no_model_fails_clearly(world):
    uid = world["uid"]
    tid = start(uid, goal="find me something nice")
    run_all()
    v = tasks.get(uid, tid)
    assert v["status"] == "failed" and v["error_class"] == "invalid_arguments" and v["attempts"] == 1


def test_cancelling_the_goal_cancels_the_delegated_work(world):
    uid = world["uid"]
    tid = start(uid, criteria={"role": "Backend Developer"})
    assert tasks.run_once("w", lease=10)  # the Orchestrator plans and delegates to the Scout, then waits
    kids = tasks.children(uid, tid)
    assert [k["status"] for k in kids] == ["queued"] and tasks.get(uid, tid)["status"] == "waiting_children"
    tasks.cancel(uid, tid)
    run_all()
    assert tasks.get(uid, tid)["status"] == "cancelled" and tasks.children(uid, tid)[0]["status"] == "cancelled"
    assert world["searches"] == []


def test_another_users_runs_are_never_visible(world, monkeypatch):
    other, _ = make_user()
    uid = world["uid"]
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4})
    run_all()
    assert "gh:acme:1" in [p["id"] for p in report(uid, tid)["recommended"]]
    assert tasks.get(other, tid) is None and tasks.children(other, tid) == []


def test_researcher_runs_only_when_asked_and_its_facts_reach_the_report(world, monkeypatch):
    from features.research import company as research
    uid = world["uid"]
    seen = []

    def fake_text(ws, rec):
        seen.append((ws.uid, rec["name"]))
        return {"identity": {"matched": True}, "fields": {}}
    monkeypatch.setattr(research, "research_text", fake_text)
    monkeypatch.setattr(research, "validate", lambda raw, rec, fetch=None: {"fields": {"website": [
        {"value": f"https://{rec['name'].lower()}.example", "source": "x", "excerpt": "e"}]}})
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4, "prepare": False})
    run_all()
    assert seen == [] and [k["agent_id"] for k in tasks.children(uid, tid)] == ["job_scout", "job_analyst"]
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4, "research": True, "count": 2, "prepare": False})
    run_all()
    r = report(uid, tid)
    assert [k["agent_id"] for k in tasks.children(uid, tid)] == ["job_scout", "job_analyst", "researcher"]
    assert len(seen) <= 2 and all(u == uid for u, _ in seen)
    rec = r["recommended"][0]
    assert rec["company_research"]["status"] == "partial" and rec["company_research"]["fields"]["website"]
    assert A.Workspace(uid).load("company_research", None) is None  # read-only: nothing saved to the research list


def test_research_without_a_website_is_a_reported_answer_not_a_crash(world):
    uid = world["uid"]
    ctx = tools.ToolContext(uid=uid, grants=frozenset({"company.research"}), mode="shadow")
    out = tools.call(ctx, "company.research", {"name": "Acme"})
    assert out["status"] == "failed" and "website" in out["error"]


def test_writer_prepares_drafts_for_the_top_picks_and_saves_nothing(world, monkeypatch):
    from features.ai_jobs import evaluate, extras
    uid = world["uid"]
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("ai_answers", {"a1": {"question": "Why do you want to work here?", "answer": "I like {company}.",
                                     "status": "approved", "company": ""}})
    before = {k: ws.load(k, None) for k in ("ai_resumes", "ai_covers", "ai_resume_struct", "ai_answers")}
    monkeypatch.setattr(evaluate, "resume_for", lambda w: RESUME)
    monkeypatch.setattr(extras, "resume_for", lambda w: RESUME)
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4, "count": 5})
    run_all()
    r = report(uid, tid)
    assert [k["agent_id"] for k in tasks.children(uid, tid)] == ["job_scout", "job_analyst", "writer"]
    prepared = [p for p in r["recommended"] if "materials" in p]
    assert 1 <= len(prepared) <= 3
    m = prepared[0]["materials"]
    assert m["cover_letter"].startswith("Dear") and prepared[0]["company"] in m["cover_letter"]
    assert m["resume"]["skills"] and m["engine"] == "Inkwell AI" and m["rejected_changes"] == []
    assert {k: ws.load(k, None) for k in before} == before            # nothing saved: drafts live only in the report
    assert approvals.list_for(uid) == []


def test_writer_uses_only_approved_answers_and_marks_drafts(world, monkeypatch):
    from features.ai_jobs import evaluate, extras
    uid = world["uid"]
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("ai_answers", {"a1": {"question": "Why do you want to work here?", "answer": "I like {company}.",
                                     "status": "approved"},
                               "a2": {"question": "Expected salary?", "answer": "lots", "status": "review"}})
    monkeypatch.setattr(extras, "resume_for", lambda w: RESUME)
    monkeypatch.setattr(evaluate, "draft_answers", lambda profile, job, jd, qs: [{"question": q, "answer": "draft"} for q in qs])
    ctx = tools.ToolContext(uid=uid, grants=frozenset({"writer.draft"}), mode="shadow")
    out = tools.call(ctx, "writer.draft", {"job": {"id": "gh:acme:1", "title": "Backend Engineer", "company": "Acme",
                                                   "text": "Python Django"},
                                           "questions": ["Why do you want to work here?", "Expected salary?"]})
    assert out["answers"] == [{"question": "Why do you want to work here?", "answer": "I like Acme.", "source": "approved"},
                              {"question": "Expected salary?", "answer": "draft", "source": "draft"}]


def test_prepare_false_skips_the_writer(world):
    uid = world["uid"]
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4, "prepare": False})
    run_all()
    assert [k["agent_id"] for k in tasks.children(uid, tid)] == ["job_scout", "job_analyst"]


@pytest.fixture
def applying(world, monkeypatch):
    from features.ai_jobs import routes
    ws = A.Workspace(world["uid"])
    with ws.lock:
        ws.save("profile", {**ws.profile(), "phone": "+1 555 0100"})
    submitted = []

    def fake_submit(ws_, job, visible=False):
        submitted.append((ws_.uid, job["id"]))
        return {"state": "applied", "detail": "Thank you for applying", "url": job["url"], "at": 0}
    monkeypatch.setattr(routes, "submit_and_record", fake_submit)
    world["submitted"] = submitted
    return world


def test_shadow_mode_applicant_asks_nothing_and_submits_nothing(applying):
    uid = applying["uid"]
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4, "count": 3, "apply": True, "prepare": False})
    run_all()
    r = report(uid, tid)
    assert [k["agent_id"] for k in tasks.children(uid, tid)] == ["job_scout", "job_analyst", "applicant"]
    assert {p["application"]["state"] for p in r["recommended"]} == {"would_ask_approval"}
    assert applying["submitted"] == [] and approvals.list_for(uid) == []


def test_on_mode_asks_one_approval_per_application_and_submits_only_approved_ones_once(applying):
    uid = applying["uid"]
    tid = runtime.start(uid, "orchestrator", {"criteria": {"role": "Backend Developer", "years": 4, "count": 3,
                                                           "apply": True, "prepare": False}}, mode="on")
    run_all()
    pend = approvals.list_for(uid, status="pending")
    assert len(pend) >= 2 and applying["submitted"] == []                 # all asked together, nothing sent yet
    applicant = [k for k in tasks.children(uid, tid) if k["agent_id"] == "applicant"][0]
    assert applicant["status"] == "waiting_approval" and tasks.get(uid, tid)["status"] == "waiting_children"
    yes, no = pend[0], pend[1:]
    approvals.decide(uid, yes["id"], "approve")
    run_all()
    assert applying["submitted"] == []                                     # still waiting for the other decisions
    for a in no:
        approvals.decide(uid, a["id"], "reject")
    run_all()
    assert applying["submitted"] == [(uid, yes["proposed"]["job"]["id"])]  # exactly the approved one, once
    r = report(uid, tid)
    states = {p["id"]: p["application"]["state"] for p in r["recommended"]}
    assert states[yes["proposed"]["job"]["id"]] == "applied" and list(states.values()).count("declined") == len(no)
    run_all()
    assert len(applying["submitted"]) == 1


def test_jobs_that_cant_be_applied_to_are_skipped_before_asking(applying):
    uid = applying["uid"]
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("ai_applied", {"gh:acme:1": {"state": "applied"}})
    ctx = tools.ToolContext(uid=uid, grants=frozenset({"applications.prepare"}), mode="shadow")
    out = tools.call(ctx, "applications.prepare", {"jobs": [
        {"id": "gh:acme:1", "title": "Dev", "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/1", "ats": "greenhouse"},
        {"id": "x:li:1", "title": "Dev", "company": "LI", "url": "https://www.linkedin.com/jobs/view/1"},
        {"id": "gh:globex:2", "title": "Dev", "company": "Globex", "url": "https://boards.greenhouse.io/globex/jobs/2", "ats": "greenhouse"}]})
    assert [(o["id"], o["ready"]) for o in out] == [("gh:acme:1", False), ("x:li:1", False), ("gh:globex:2", True)]
    assert out[0]["why_not"] == "Already applied."


def test_submit_rechecks_eligibility_after_approval(applying):
    uid = applying["uid"]
    from features.ai_jobs.agent_tools import submit_application
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("profile", {**ws.profile(), "phone": ""})
    ctx = tools.ToolContext(uid=uid, grants=frozenset(), mode="on")
    out = submit_application(ctx, {"id": "gh:a:1", "title": "Dev", "company": "A", "url": "https://boards.greenhouse.io/a/jobs/1",
                                   "ats": "greenhouse"})
    assert out["state"] == "skipped" and "phone" in out["detail"] and applying["submitted"] == []


def test_huge_board_results_keep_the_agent_state_small(world):
    """Production: a board returned so many full postings that the Scout's checkpoint passed MongoDB's 16 MB limit."""
    uid = world["uid"]
    world["board"] = [job(i, "Backend Engineer" if i % 3 else "Sales Rep", f"Co{i}", "Python Django " + "x" * 20000)
                      for i in range(3000)]
    tid = start(uid, criteria={"role": "Backend Developer", "years": 4, "prepare": False})
    run_all()
    assert tasks.get(uid, tid)["status"] == "done"
    import bson
    biggest = max(len(bson.encode(d)) for d in A.M.checkpoints.find({"uid": uid}))
    assert biggest < 4_000_000
