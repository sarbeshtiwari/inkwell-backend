"""Jobs read from a company's own careers site, and applying there."""

from features.ai_jobs import siteapply, sitejobs

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
