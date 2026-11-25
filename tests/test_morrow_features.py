"""Features adapted from Morrow: Claude Code as the AI, the 90-day course and spaced review, the daily briefing,
reorder-only resume tailoring, evidence-checked company research and the new job sources."""
import datetime as dt
import os
import stat

from conftest import H

from features.ai_jobs import cli_llm, models, tailor_rules as R
from features.ai_jobs.boards import cutshort, hackernews, hirist, wellfound, ycombinator
from features.learning import briefing, course
from features.learning.content import interview_review
from features.research import company as research


# --- Claude Code / Kiro -------------------------------------------------------------------------------------------

def fake_tool(tmp_path, monkeypatch, out, code=0):
    """A stand-in `claude` that prints `out`, so no real plan usage happens in tests."""
    path = tmp_path / "claude"
    path.write_text(f"#!/bin/sh\necho '{out}'\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(cli_llm, "available", lambda pid: pid == "claude_cli")
    monkeypatch.setattr(models, "local_allowed", lambda: True)


def test_claude_code_is_the_default_ai_on_this_computer(user, tmp_path, monkeypatch):
    fake_tool(tmp_path, monkeypatch, 'Sure: {"ok": true, "score": 4}')
    cfg = models.config(user["ws"])
    assert cfg["provider"] == "claude_cli"
    assert models.chat(cfg, "sys", "q", as_json=True) == {"ok": True, "score": 4}
    assert user["client"].get("/api/ai-jobs/model", headers=H).get_json()["cli"]["claude_cli"] is True


def test_claude_code_falls_back_to_ollama_when_unavailable(user, monkeypatch):
    monkeypatch.setattr(models, "local_allowed", lambda: False)  # e.g. the hosted service
    user["ws"].save("ai_model", {"provider": "claude_cli"})
    monkeypatch.setattr(models, "ollama_up", lambda: True)
    assert models.config(user["ws"])["provider"] == "ollama"


def test_plan_limit_is_reported_clearly(tmp_path, monkeypatch):
    fake_tool(tmp_path, monkeypatch, "Claude usage limit reached", code=1)
    try:
        cli_llm.run("claude_cli", "hi")
        raise AssertionError("should fail")
    except cli_llm.QuotaError as e:
        assert "usage limit" in str(e)


def test_claude_code_command_has_no_tools_unless_research():
    assert cli_llm.command("claude_cli", "p")[cli_llm.command("claude_cli", "p").index("--tools") + 1] == ""
    assert "WebSearch,WebFetch" in cli_llm.command("claude_cli", "p", web=True)


# --- course and spaced review --------------------------------------------------------------------------------------

def test_every_course_day_builds():
    for d in range(1, 91):
        L = course.lesson(d)
        assert L["tracks"] and L["dsa"]["depth"]["concept"] and L["questions"] is not None


def test_one_day_per_calendar_day_and_reviews_follow(user):
    c = user["client"]
    s = c.get("/api/learning", headers=H).get_json()
    assert s["current"] == 1 and s["lesson"]["day"] == 1
    assert c.post("/api/learning/day/1/complete", json={}, headers=H).status_code == 200
    r = c.post("/api/learning/day/2/complete", json={}, headers=H)
    assert r.status_code == 400 and "tomorrow" in r.get_json()["error"]
    data = user["ws"].load("learning", {})
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    assert interview_review.due(data, tomorrow, 99)  # day 1's questions come back tomorrow
    qid = interview_review.due(data, tomorrow, 1)[0]
    assert c.post(f"/api/learning/review/{qid}", json={"rating": "good"}, headers=H).status_code == 200
    assert user["ws"].load("learning", {})["reviews"][qid]["reps"] == 1


def test_question_bank_search(user):
    d = user["client"].get("/api/learning/questions?q=machine&track=ai_ml", headers=H).get_json()
    assert d["total"] > 0 and all(q["track"] == "ai_ml" for q in d["items"])


# --- briefing --------------------------------------------------------------------------------------------------------

def test_briefing_uses_feeds_and_skips_repeats(user, monkeypatch):
    monkeypatch.setattr(briefing, "get_text", lambda *a, **k: (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1</id><title>Paper One</title>'
        '<published>2026-11-19T00:00:00Z</published><summary>About agents.</summary><author><name>A</name></author></entry></feed>'))
    monkeypatch.setattr(briefing, "get_json", lambda url, **k: {"hits": [{"objectID": "9", "title": "New LLM agent", "points": 99}]}
                        if "algolia" in url else {"items": [{"full_name": "a/agent", "html_url": "https://github.com/a/agent",
                                                             "description": "An AI agent", "stargazers_count": 500}]})
    b = briefing.build(user["ws"])
    assert b["papers"][0]["url"] == "https://arxiv.org/abs/1" and b["news"][0]["points"] == 99 and b["repos"][0]["name"] == "a/agent"
    assert b["goal"]["day"] == 1 and b["notes"] == {}  # no AI configured: feed text only
    assert briefing.build(user["ws"])["papers"] == []  # the same paper isn't shown twice


# --- reorder, never add ----------------------------------------------------------------------------------------------

def test_skills_and_bullets_may_only_be_reordered():
    base = [("Languages", ["Python", "Go"])]
    assert R.accept_skills(base, {"Languages": ["Go", "Python"]})[0] == [("Languages", ["Go", "Python"])]
    kept, notes = R.accept_skills(base, {"Languages": ["Go", "Python", "Rust"]})
    assert kept == base and "added or dropped" in notes[0]
    assert R.accept_bullets([["a", "b", "c"]], ["Role"], [[2, 0, 1]])[0] == [["c", "a", "b"]]
    assert R.accept_bullets([["a", "b"]], ["Role"], [[0, 0]])[0] == [["a", "b"]]


def test_summary_rewrite_must_stay_on_the_resume():
    original = "Engineer with 2 years building Python and React apps at Acme for clients in finance and retail sectors."
    terms = R.terms(original)
    good = "Python and React engineer with 2 years building apps at Acme for finance and retail clients and sectors."
    assert R.accept_text("Summary", good, original, (10, 40), terms)[0] == good
    text, notes = R.accept_text("Summary", "Python engineer with 5 years of Kubernetes at Acme for finance and retail sectors.",
                                original, (10, 40), terms)
    assert text == original and "Kubernetes" in notes[0] and "5" in notes[0]
    assert R.accept_text("Summary", good.replace("building", "passionately leveraging"), original, (10, 40), terms)[0] == original


# --- company research --------------------------------------------------------------------------------------------------

PAGES = {"https://acme.example/about": {"url": "https://acme.example/about", "links": ["https://acme.example/careers"],
                                        "text": "Acme Labs builds payment tools. Jane Doe, CEO and co-founder. Write to jobs@acme.example."}}


def test_research_keeps_only_claims_the_page_shows():
    raw = {"identity": {"matched": True, "source": "https://acme.example/about"}, "notes": "",
           "fields": {"ceo": [{"value": "Jane Doe", "source": "https://acme.example/about"}],
                      "cto": [{"value": "John Roe", "source": "https://acme.example/about"}],
                      "careers": [{"value": "https://acme.example/careers", "source": "https://acme.example/about"}],
                      "email": [{"value": "jobs@acme.example", "source": "https://acme.example/about"},
                                {"value": "ceo@acme.example", "source": "https://acme.example/about"}]}}
    res = research.validate(raw, {"name": "Acme Labs"}, fetch=lambda u: PAGES[u])
    assert res["fields"]["ceo"][0]["value"] == "Jane Doe" and "CEO" in res["fields"]["ceo"][0]["evidence"]
    assert "cto" not in res["fields"] and [e["value"] for e in res["fields"]["email"]] == ["jobs@acme.example"]
    assert res["fields"]["careers"][0]["value"] == "https://acme.example/careers"
    assert "2 claims could not be checked" in res["notes"] and research.status_of(res) == "partial"


def test_research_refuses_a_different_company():
    raw = {"identity": {"matched": True, "source": "https://acme.example/about"}, "fields": {"ceo": [{"value": "Jane Doe", "source": "https://acme.example/about"}]}}
    res = research.validate(raw, {"name": "Globex"}, fetch=lambda u: PAGES[u])
    assert res["fields"] == {} and "review" in res["notes"]


def test_research_api_runs_in_background(user, monkeypatch):
    monkeypatch.setattr(research, "research_text", lambda ws, co: {"identity": {"matched": True, "source": "https://acme.example/about"},
                                                                   "fields": {"ceo": [{"value": "Jane Doe", "source": "https://acme.example/about"}]}})
    monkeypatch.setattr(research, "fetch_page", lambda u: PAGES[u])
    monkeypatch.setattr(research.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda self: target(*args)})())
    c = user["client"]
    assert c.post("/api/research/companies", json={"name": "Acme Labs", "website": "http://10.0.0.1"}, headers=H).status_code == 400
    assert c.post("/api/research/companies", json={"name": "Acme Labs", "website": "acme.example"}, headers=H).status_code == 200
    row = c.get("/api/research/companies", headers=H).get_json()["companies"][0]
    assert row["status"] == "partial" and row["data"]["fields"]["ceo"][0]["value"] == "Jane Doe"


# --- job sources ----------------------------------------------------------------------------------------------------------

def test_yc_titles_and_india_filter():
    assert ycombinator.parse_yc_title("Acme (YC P25) Is Hiring a Founding Engineer") == ("Acme", "P25", "Founding Engineer")
    assert hackernews.open_to_india("Acme | Engineer | Remote (worldwide)")
    assert not hackernews.open_to_india("Acme | Engineer | Remote (US only)")


def test_board_parsers(monkeypatch):
    cs = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"dehydratedState":{"queries":[{"state":{"data":'
          '{"data":{"pageData":{"jobs":[{"_id":"6af5e3387325a0adab00bf53","publicUrl":"https://cutshort.io/job/x","headline":"AI Engineer",'
          '"companyDetails":{"name":"Acme"},"locations":["Pune"],"salaryRange":{"min":1000000,"max":2000000},"expRange":{"min":2,"max":5}}]}}}}}]}}}}</script>')
    monkeypatch.setattr(cutshort, "get_text", lambda url, **k: cs)
    j = cutshort.fetch("AI Engineer")[0]
    assert (j["title"], j["company"], j["location"], j["salary"]) == ("AI Engineer", "Acme", "Pune", "₹10–20 LPA")
    wf = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"apolloState":{"data":{'
          '"StartupResult:1":{"name":"Vested","highlightedJobListings":[{"__ref":"JobListingSearchResult:9"}]},'
          '"JobListingSearchResult:9":{"id":"9","title":"Backend Engineer","slug":"backend-engineer","locationNames":["Bangalore"],"remote":true}}}}}}</script>')
    monkeypatch.setattr(wellfound, "get_text", lambda url, **k: wf)
    j = wellfound.fetch("backend")[0]
    assert (j["company"], j["url"], j["location"]) == ("Vested", "https://wellfound.com/jobs/9-backend-engineer", "Bangalore · Remote")
    jobs = hirist.parse([("https://www.hirist.tech/j/acme-python-developer-123?ref=x", "Acme - Python Developer\n2 - 5 yrs\nPune\nPython | Django")])
    assert (jobs[0]["company"], jobs[0]["title"], jobs[0]["location"]) == ("Acme", "Python Developer", "Pune")


def test_new_boards_are_never_auto_applied():
    from features.ai_jobs import siteapply
    for url in ("https://www.hirist.tech/j/x-1", "https://cutshort.io/job/x", "https://wellfound.com/jobs/1-x", "https://www.ycombinator.com/companies/x/jobs"):
        assert siteapply.NEVER.search(url)


# --- LaTeX resumes --------------------------------------------------------------------------------------------------------

OWN_TEX = r"""\documentclass{article}
\begin{document}
{\LARGE\bfseries A B}\\[3pt]
{\normalsize\color{muted} Engineer}\\[5pt]
\section{Summary}
Engineer building Python and React apps at Acme for finance clients over two years with care and tests in production.

\section{Experience}
\begin{itemize}
  \item Built Python APIs
  \item Shipped React UI
\end{itemize}

\section{Skills}
{\bfseries Languages:} Python, Go\par\vspace{2pt}

\end{document}
"""


def test_own_tex_is_reordered_never_added():
    from features.ai_jobs import latex
    terms = R.terms(latex.plain(OWN_TEX))
    tex, notes, _ = latex.tailor_tex(OWN_TEX, {"skills": {"Languages": ["Go", "Python"]}, "bullets": [[1, 0]]}, terms)
    assert "Go, Python" in tex and tex.index("Shipped React UI") < tex.index("Built Python APIs")
    tex, notes, _ = latex.tailor_tex(OWN_TEX, {"skills": {"Languages": ["Go", "Python", "Rust"]}, "bullets": [[0, 0]]}, terms)
    assert "Python, Go" in tex and "Rust" not in tex and tex.index("Built Python APIs") < tex.index("Shipped React UI")


def test_template_escapes_and_blocks_unsafe_tex():
    from features.ai_jobs import latex
    tex = latex.from_parts({"name": "A_B & Co"}, {}, {"summary": "50% #1 $5", "sections": [], "skill_groups": [("Lang", ["C#"])]})
    assert r"A\_B \& Co" in tex and r"50\% \#1 \$5" in tex and r"C\#" in tex
    assert latex.parse(tex)["skills"] == [("Lang", [r"C\#"])]
    try:
        latex.compile_pdf(OWN_TEX.replace(r"\section{Summary}", r"\input{/etc/passwd}\section{Summary}"))
        raise AssertionError("should refuse")
    except latex.LatexError as e:
        assert "input" in str(e)


# --- website builder: the notebook look ----------------------------------------------------------------------------------

def test_notebook_look_renders_and_old_sites_stay_modern():
    import app as A
    from features import site
    ctx = A.app.test_request_context("/")
    ctx.push()
    nb = {k: v for k, v in site.PRESETS["notebook"].items() if k != "label"}
    cfg = {"theme": {**nb, "preset": "notebook"}, "profile": {"name": "A <b>", "socials": {}}, "seo": {},
           "sections": [{"id": "h1", "type": "hero", "on": True, "variant": "center", "nav": "",
                         "data": {"title": "hi, I build *LLM agents* <script>"}}]}
    html = site.render(site.clean(cfg), [], slug="a", nonce="n")
    assert "<em>LLM agents</em>" in html and "&lt;script&gt;" in html and "<script>\"" not in html
    assert 'class="anim nb"' in html and "family=Caveat" in html and "prefers-color-scheme: dark" in html
    old = site.clean({**cfg, "theme": {k: v for k, v in site.PRESETS["midnight"].items() if k not in ("label", "look")}})
    assert old["theme"]["look"] == "modern" and "family=Caveat" not in site.render(old, [], slug="a", nonce="n")
    ctx.pop()
