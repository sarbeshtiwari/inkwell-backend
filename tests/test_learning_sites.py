"""Learning from feedback, jobs read from a company's own careers site, and applying there."""

from conftest import H

from features.ai_jobs import learning, siteapply, sitejobs

BASE = {"ats": "", "board": "x", "location": "Remote", "url": "https://acme.com/careers/1", "posted": "", "reasons": [], "gaps": [],
        "summary": "Build React and Node.js services. Python is a plus.", "score": 60}


def job(i, title, company="Acme"):
    return {**BASE, "id": f"t:{i}", "title": title, "company": company}


def test_feedback_moves_rankings_and_suggests_rules(user):
    c, ws = user["client"], user["ws"]
    good = [job(i, "Frontend Engineer (React)") for i in range(3)]
    bad = [job(10 + i, "Sales Executive", "Globex") for i in range(3)]
    with ws.lock:
        ws.save("ai_match", {"results": good + bad, "want": {"years": 3}})
    for j in good:
        assert c.post(f"/api/ai-jobs/feedback/{j['id']}", json={"verdict": "good"}, headers=H).status_code == 200
    for j in bad:
        c.post(f"/api/ai-jobs/feedback/{j['id']}", json={"verdict": "bad", "reason": "company"}, headers=H)
    m = learning.model(ws)
    assert m["lessons"] == 6 and m["weights"]
    ranked = learning.adjust([job(20, "React Frontend Developer"), job(21, "Sales Executive", "Globex")], m)
    assert ranked[0]["id"] == "t:20" and ranked[0]["learned"] > 0 and ranked[1]["learned"] < 0
    assert any("You liked jobs like this" in r for r in ranked[0]["reasons"])
    # "Not for me" discards the job; the repeated reason becomes a suggestion that waits for approval
    assert ws.load("ai_pipeline", {})["t:10"]["status"] == "discarded"
    d = c.get("/api/ai-jobs/learning").get_json()
    sug = next(s for s in d["suggestions"] if s["id"] == "bl:globex")
    assert "Globex" not in (ws.load("ai_rules", {}) or {}).get("blacklist", [])
    r = c.post(f"/api/ai-jobs/learning/suggestion/{sug['id']}", json={"accept": True}, headers=H).get_json()
    assert "Globex" in r["rules"]["blacklist"]
    assert not any(s["id"] == "bl:globex" for s in r["learning"]["suggestions"])
    assert learning.prompt_hint(ws).startswith("From the candidate's own feedback")


def test_outcomes_count_and_calibration_needs_enough_jobs(user):
    c, ws = user["client"], user["ws"]
    jobs = [job(i, "Backend Engineer") for i in range(4)]
    for i, j in enumerate(jobs):
        j["score"] = 85 if i < 2 else 40
    with ws.lock:
        ws.save("ai_match", {"results": jobs})
    for j, st in zip(jobs, ["interview", "interview", "rejected", "rejected"]):
        c.put(f"/api/ai-jobs/status/{j['id']}", json={"status": "applied"}, headers=H)
        c.put(f"/api/ai-jobs/status/{j['id']}", json={"status": st}, headers=H)
    assert learning.model(ws)["lessons"] == 4
    cal = learning.calibration(ws, floor=2)
    assert [(b["band"], b["rate"]) for b in cal["bands"]] == [("80+", 100), ("below 50", 0)]
    assert learning.calibration(ws)["bands"][0]["rate"] is None  # below the default sample floor: counts only


PAGE = """<html><body><script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
 {"@type": "JobPosting", "title": "Platform Engineer", "identifier": {"value": "PE-7"}, "datePosted": "2026-10-30",
  "description": "<p>Run our <b>Kubernetes</b> fleet.</p>", "hiringOrganization": {"name": "Acme Corp"},
  "jobLocation": {"address": {"addressLocality": "Pune", "addressCountry": "IN"}}, "jobLocationType": "TELECOMMUTE",
  "baseSalary": {"currency": "INR", "value": {"minValue": 2000000, "maxValue": 3000000, "unitText": "YEAR"}}}]}</script>
 <a href="/careers/jobs/platform-engineer-1234">Platform Engineer</a><a href="/careers/open-roles">See open roles</a>
 <a href="/blog/why-we-hire">Blog</a><iframe src="https://job-boards.greenhouse.io/embed/job_board?for=acme"></iframe></body></html>"""


def test_careers_site_reading():
    jobs = sitejobs.jobs_in(PAGE, "https://acme.com/careers", "acme.com")
    assert jobs == [{**jobs[0], "title": "Platform Engineer", "company": "Acme Corp", "id": "site:acme.com:PE-7",
                     "location": "Pune, IN", "text": "Run our Kubernetes fleet.", "posted": "2026-10-30"}]
    assert "INR" in jobs[0]["salary"]
    assert sitejobs.embedded_boards(PAGE)[0] == ("greenhouse", "acme")
    listings, details = sitejobs.classify("https://acme.com/careers", sitejobs.anchors_of(PAGE), "acme.com")
    assert listings == ["https://acme.com/careers/open-roles"]
    assert list(details) == ["https://acme.com/careers/jobs/platform-engineer-1234"]
    assert sitejobs.site_of("https://careers.swiggy.in/x") == "swiggy.in" and sitejobs.site_of("https://jobs.acme.co.uk") == "acme.co.uk"


def test_discover_uses_the_board_a_site_embeds(monkeypatch):
    pages = {"https://acme.com/careers": PAGE.replace("application/ld+json", "x")}
    monkeypatch.setattr(sitejobs, "get_page", lambda url, hops=5: (url, pages.get(url, "")))
    monkeypatch.setattr(sitejobs, "render", lambda url: ("", "", [], []))
    d = sitejobs.discover("https://acme.com/careers")
    assert d["boards"][0] == ("greenhouse", "acme") and not d["jobs"]


def test_apply_modes():
    assert siteapply.mode({"ats": "lever", "url": "https://jobs.lever.co/a/1"}) == "board"
    assert siteapply.mode({"ats": "", "url": "https://careers.acme.com/jobs/1"}) == "site"
    assert siteapply.mode({"ats": "", "url": "https://acme.wd5.myworkdayjobs.com/en-US/ext/job/1"}) == "account"
    assert siteapply.mode({"ats": "", "url": "https://www.linkedin.com/jobs/view/1"}) == ""
    assert siteapply.mode({"ats": "", "url": "http://acme.com/jobs/1"}) == ""


def test_site_memory(user):
    ws = user["ws"]
    for _ in range(2):
        siteapply.remember(ws, "https://www.acme.com/jobs/1", "needs_account", "Apply now")
    assert siteapply.known_account_site(ws, "https://acme.com/jobs/2")
    assert siteapply.memory(ws, "https://acme.com/x")["button"] == "Apply now"
    siteapply.remember(ws, "https://acme.com/jobs/3", "applied")
    assert not siteapply.known_account_site(ws, "https://acme.com/jobs/4")


def test_past_applications_are_lessons(user):
    ws = user["ws"]
    jobs = [job(i, "Data Engineer") for i in range(3)]
    with ws.lock:
        ws.save("ai_match", {"results": jobs})
        ws.save("ai_applied", {j["id"]: {"state": "applied", "at": 1790000000} for j in jobs})
        ws.save("ai_pipeline", {jobs[0]["id"]: {"status": "evaluated", "at": "2026-10-01T10:00:00"}})
    assert learning.model(ws)["lessons"] == 3 and learning.taste(ws)["likes"]


def test_new_board_links_are_recognised():
    from features.ai_jobs.portals import from_link
    cases = {"https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1002/jobs": ("oraclecloud", "jpmc.fa.oraclecloud.com/CX_1002"),
             "https://career-ohb.csod.com/ux/ats/careersite/4/home?c=career-ohb": ("csod", "career-ohb.csod.com/4"),
             "https://cu.taleo.net/careersection/2/jobsearch.ftl": ("taleo", "cu.taleo.net/2"),
             "https://jobs.deel.com/klarna": ("deel", "klarna"), "https://t.me/s/remotejobss": ("telegram_channel", "remotejobss")}
    for link, want in cases.items():
        assert from_link(link)[0] == want
    from features.ai_jobs import boards
    assert {"oraclecloud", "taleo", "csod", "deel", "peoplesoft"} <= set(boards.company_boards())
    assert {"arbeitsagentur", "jobstreet"} <= set(boards.search_boards("Berlin, Germany")) | set(boards.search_boards("Jakarta"))
