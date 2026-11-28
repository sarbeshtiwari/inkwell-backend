"""Tools for agents over the existing job-search code (infra/tools.py registry). Read-only for now.

Each tool resolves the user from its ToolContext (ctx.uid), never from its arguments, and reuses the code the AI job
match already runs: the same search sources, the same ranking engine, the same liveness check and the same rules.
Tools that change data or act outside Inkwell come with the approvals layer (PH1-08)."""

from features.ai_jobs import ranking, signals
from features.ai_jobs.common import J
from features.ai_jobs.sources import make_tools
from infra import tools as tools_mod
from infra.tools import READ, tool

SOURCES = ["career_sites", "remote_boards"]
TEXT = {"type": "string", "maxLength": 20000}
SHORT = {"type": "string", "maxLength": 300}
JOB = {"type": "object", "required": ["id", "title", "company"], "properties": {
    "id": {"type": "string", "maxLength": 300}, "title": SHORT, "company": SHORT, "location": SHORT,
    "url": {"type": "string", "maxLength": 2000}, "ats": {"type": "string", "maxLength": 60}, "text": TEXT,
    "posted": {"type": "string", "maxLength": 40}}}
OBJECT = {"type": "object"}


def _profile(ws, role, years, location="", company="", resume_name=""):
    import re

    from core.validation import Invalid
    from features.ai_jobs.runs import MIN_RESUME_WORDS, pdf_text
    resume = pdf_text(ws, resume_name) if resume_name else J().resume_text(ws)
    if len(re.findall(r"[A-Za-z]{2,}", resume or "")) < MIN_RESUME_WORDS:
        raise Invalid("Inkwell couldn't find any text in that resume. It looks like a scanned or image-only PDF: "
                      "export it again as a text PDF (from Word, Google Docs or LaTeX) and pick it here.", "resume")
    return {"role": role, "years": years, "location": location, "company": company, "resume": resume,
            "skills": [s for s in J().skill_hits(resume, J().SKILLS) if s not in ("C", "R")]}


@tool("profile.candidate", "1", "The user's resume text and the skills Inkwell finds in it.",
      {"type": "object", "additionalProperties": False, "properties": {}},
      {"type": "object", "required": ["resume", "skills"], "properties": {
          "resume": TEXT, "skills": {"type": "array", "items": SHORT, "maxItems": 200}}}, READ)
def candidate(ctx):
    resume = J().resume_text(ctx.workspace())
    return {"resume": resume[:20000], "skills": [s for s in J().skill_hits(resume, J().SKILLS) if s not in ("C", "R")][:200]}


@tool("jobs.search", "2", "Search the chosen public sources (company career sites, public job boards) for openings and "
      "drop duplicates.",
      {"type": "object", "additionalProperties": False, "required": ["role"], "properties": {
          "role": {"type": "string", "maxLength": 120}, "company": {"type": "string", "maxLength": 120},
          "location": {"type": "string", "maxLength": 120},
          "sources": {"type": "array", "items": {"type": "string", "enum": SOURCES}, "maxItems": 4}}},
      {"type": "object", "required": ["jobs", "errors"], "properties": {
          "jobs": {"type": "array", "items": OBJECT}, "errors": {"type": "array", "items": SHORT}}}, READ, timeout=300)
def search(ctx, role, company="", location="", sources=("career_sites",)):
    jobs, errors = ranking.search_sources(make_tools(location), list(sources), {"role": role, "company": company, "location": location})
    return {"jobs": jobs, "errors": errors[:50]}


@tool("jobs.rank", "3", "Score jobs 0–100 against the user's resume with Inkwell's own engine (no model): role fit, "
      "skills, years asked, similarity, seniority, location, posting age. Returns reasons and gaps per job.",
      {"type": "object", "additionalProperties": False, "required": ["role", "years", "jobs"], "properties": {
          "role": {"type": "string", "maxLength": 120}, "years": {"type": "number", "minimum": 0, "maximum": 60},
          "location": {"type": "string", "maxLength": 120}, "company": {"type": "string", "maxLength": 120},
          "resume": {"type": "string", "maxLength": 200},
          "jobs": {"type": "array", "items": JOB, "maxItems": 500}}},
      {"type": "array", "items": {"type": "object", "required": ["id", "score", "reasons", "gaps"]}}, READ)
def rank(ctx, role, years, jobs, location="", company="", resume=""):
    ws = ctx.workspace()
    profile = _profile(ws, role, years, location, company, resume)
    return ranking.rank(profile, [{"location": "", "text": "", **j} for j in jobs])


@tool("matches.save", "1", "Save a finished job search as the user's AI job match results (what the AI job match page "
      "shows), record which jobs break their rules, and tell them how many strong matches there are.",
      {"type": "object", "additionalProperties": False, "required": ["want", "sources", "results"], "properties": {
          "want": {"type": "object"}, "sources": {"type": "array", "items": SHORT, "maxItems": 4},
          "results": {"type": "array", "items": {"type": "object"}, "maxItems": 100},
          "jds": {"type": "object"}, "errors": {"type": "array", "items": SHORT, "maxItems": 50},
          "engine": SHORT, "considered": {"type": "integer", "minimum": 0}, "error": {"type": "string", "maxLength": 300}}},
      {"type": "object", "required": ["saved"]}, tools_mod.WRITE_INTERNAL)
def save_matches(ctx, want, sources, results, jds=None, errors=(), engine="Inkwell AI", considered=0, error=""):
    from features.ai_jobs.runs import save_match
    n = save_match(ctx.workspace(), want, sources, results, jds or {}, list(errors), engine, considered, error)
    return {"saved": n}


@tool("jobs.liveness", "1", "Is this posting still open? Checks the company's job board, else the job page.",
      {"type": "object", "additionalProperties": False, "required": ["job"], "properties": {"job": JOB}},
      {"type": "object", "required": ["state", "reason"], "properties": {
          "state": {"type": "string", "enum": ["active", "expired", "uncertain"]}, "reason": SHORT}}, READ, timeout=60)
def liveness(ctx, job):
    return signals.liveness(job)


@tool("company.research", "1", "Public facts about an employer from its own website, each checked against the page it came "
      "from; claims a page doesn't support are dropped, namesakes refused. Read-only: the result isn't saved to the user's research list.",
      {"type": "object", "additionalProperties": False, "required": ["name"], "properties": {
          "name": {"type": "string", "maxLength": 200}, "website": {"type": "string", "maxLength": 500},
          "jobs": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
              "title": SHORT, "url": {"type": "string", "maxLength": 2000}}}}}},
      {"type": "object", "required": ["status", "fields"], "properties": {
          "status": {"type": "string", "enum": ["found", "partial", "not_found", "failed"]}, "fields": OBJECT,
          "error": {"type": "string", "maxLength": 300}}}, READ, timeout=900)
def research_company(ctx, name, website="", jobs=()):
    from features.research import company as research
    rec = {"name": name, "website": website, "jobs": list(jobs)}
    try:
        result = research.validate(research.research_text(ctx.workspace(), rec), rec, fetch=research.fetch_page)
    except ValueError as e:  # e.g. no website and no web-searching AI: an answer, not a crash
        return {"status": "failed", "fields": {}, "error": str(e)[:300]}
    return {"status": research.status_of(result), "fields": result.get("fields") or {}}


@tool("research.save", "1", "Save one company's research findings to the user's own research list (what the Research "
      "button shows). Changes only the user's own data.",
      {"type": "object", "additionalProperties": False, "required": ["name", "status"], "properties": {
          "name": {"type": "string", "maxLength": 200}, "website": {"type": "string", "maxLength": 500},
          "jobs": {"type": "array", "maxItems": 6, "items": OBJECT},
          "status": {"type": "string", "enum": ["found", "partial", "not_found", "failed"]}, "fields": OBJECT,
          "error": {"type": "string", "maxLength": 300}}},
      {"type": "object", "required": ["saved"]}, tools_mod.WRITE_INTERNAL)
def research_save(ctx, name, status, website="", jobs=(), fields=None, error=""):
    import time
    from features.research import company as research
    ws, cid = ctx.workspace(), research.company_id(name)
    with ws.lock:
        rows = research.records(ws)
        old = rows.get(cid) or {}
        data = {**(old.get("data") or {}), "fields": fields or {}} if status != "failed" else old.get("data")
        rows[cid] = {**old, "name": name, "website": website or old.get("website", ""), "jobs": list(jobs) or old.get("jobs", []),
                     "status": status, "data": data, "error": error if status == "failed" else "", "updated": time.time(),
                     "by_agent": "researcher"}
        rows[cid].pop("task_id", None)
        ws.save("company_research", rows)
    return {"saved": True}


@tool("writer.draft", "1", "Draft the application materials for one job without saving anything: a tailored resume "
      "(order only: tailor_rules rejects any change that adds, drops or alters a fact), a cover letter, and answers to "
      "the given form questions (approved answers from the user's bank as they are; new ones marked as drafts).",
      {"type": "object", "additionalProperties": False, "required": ["job"], "properties": {
          "job": JOB, "role": {"type": "string", "maxLength": 120}, "years": {"type": "number", "minimum": 0, "maximum": 60},
          "questions": {"type": "array", "items": SHORT, "maxItems": 12}}},
      {"type": "object", "required": ["resume", "cover_letter", "answers", "engine"]}, READ, timeout=900)
def draft(ctx, job, role="", years=0, questions=()):
    from features.ai_jobs import answers as bank, evaluate, extras, models
    ws = ctx.workspace()
    jd = job.get("text", "")
    j = {"location": "", "url": "", "summary": jd[:400], **job}
    t = extras.tailor(ws, j, jd=jd, cache=False)
    resume = {k: t.get(k) for k in ("summary", "skills", "highlights", "sections", "skill_groups", "checks", "notes",
                                    "analysis", "keywords") if t.get(k) is not None}
    profile = {"role": role or job["title"], "years": years, "resume": t.get("original", "")}
    with models.use(models.config(ws)):
        letter = evaluate.cover_letter(profile, j, jd, ws.profile().get("name", "").strip())
        saved = ws.load("ai_answers", {}) or {}
        out, missing = [], []
        for q in questions:
            a = bank.answer_for(saved, q, job["company"])  # only approved answers are ever returned
            if a:
                out.append({"question": q, "answer": a, "source": "approved"})
            else:
                missing.append(q)
        out += [{**a, "source": "draft"} for a in evaluate.draft_answers(profile, j, jd, missing)]
    return {"resume": resume, "cover_letter": letter, "answers": out, "engine": t.get("engine", "Inkwell AI")}


SUBMIT_JOB = {"type": "object", "required": ["id", "title", "company", "url"], "additionalProperties": False, "properties": {
    "id": {"type": "string", "maxLength": 300}, "title": SHORT, "company": SHORT, "location": SHORT,
    "url": {"type": "string", "maxLength": 2000}, "apply_url": {"type": "string", "maxLength": 2000},
    "ats": {"type": "string", "maxLength": 60}, "board": {"type": "string", "maxLength": 200}}}


def eligibility(ws, job):
    """Why this job can't be applied to by Inkwell (None when it can): the same limits the Apply button has."""
    from features.ai_jobs import siteapply
    from features.ai_jobs.apply import is_blocked_board
    m = siteapply.mode(job)
    if not m:
        return "Inkwell doesn't apply on this site (apply on the site yourself)."
    if m == "account":
        return "This site needs you to sign in first: use Watch mode on your own computer."
    if ((ws.load("ai_applied", {}) or {}).get(job["id"]) or {}).get("state") == "applied":
        return "Already applied."
    if is_blocked_board(ws, job):
        return "This job board blocked automated applications before."
    if not ws.profile().get("phone"):
        return "Add your phone number on the Profile page first; application forms ask for it."
    return None


@tool("applications.prepare", "1", "Check which jobs Inkwell could apply to for the user (supported site, not applied "
      "yet, board not blocked, phone number on file). Changes nothing.",
      {"type": "object", "additionalProperties": False, "required": ["jobs"], "properties": {
          "jobs": {"type": "array", "items": SUBMIT_JOB, "maxItems": 20}}},
      {"type": "array", "items": {"type": "object", "required": ["id", "ready"]}}, READ)
def prepare_applications(ctx, jobs):
    ws = ctx.workspace()
    out = []
    for j in jobs:
        why = eligibility(ws, j)
        out.append({"id": j["id"], "ready": why is None, "why_not": why})
    return out


@tool("applications.submit", "1", "Submit the application form for one job, as the user, with their details and "
      "resume. An external action: it runs only after the user approves this exact application.",
      {"type": "object", "additionalProperties": False, "required": ["job"], "properties": {"job": SUBMIT_JOB}},
      {"type": "object", "required": ["state"], "properties": {
          "state": {"type": "string", "enum": ["applied", "needs_you", "closed", "blocked", "skipped"]},
          "detail": {"type": "string", "maxLength": 1000}, "url": {"type": "string", "maxLength": 2000}}},
      tools_mod.EXTERNAL_ACTION, timeout=600)
def submit_application(ctx, job):
    from core.security import rate_limited
    from features.ai_jobs.common import APPLY_PER_DAY
    from features.ai_jobs.routes import submit_and_record
    ws = ctx.workspace()
    why = eligibility(ws, job)  # checked again now: things may have changed while the approval waited
    if why:
        return {"state": "skipped", "detail": why}
    if rate_limited(("ai-apply", ctx.uid), APPLY_PER_DAY, 86400):  # the same daily limit as the Apply button
        return {"state": "skipped", "detail": f"Inkwell applies to at most {APPLY_PER_DAY} jobs a day."}
    res = submit_and_record(ws, job)
    return {"state": res["state"] if res["state"] in ("applied", "needs_you", "closed", "blocked") else "needs_you",
            "detail": str(res.get("detail", ""))[:1000], "url": str(res.get("url", ""))[:2000]}
