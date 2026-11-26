"""Tools for agents over the existing job-search code (infra/tools.py registry). Read-only for now.

Each tool resolves the user from its ToolContext (ctx.uid), never from its arguments, and reuses the code the AI job
match already runs: the same search sources, the same ranking engine, the same liveness check and the same rules.
Tools that change data or act outside Inkwell come with the approvals layer (PH1-08)."""

from features.ai_jobs import learning, ranking, signals
from features.ai_jobs.common import J
from features.ai_jobs.evaluate import get_rules
from features.ai_jobs.sources import make_tools
from infra.tools import READ, tool

SOURCES = ["career_sites", "remote_boards", "tavily", "brave"]
TEXT = {"type": "string", "maxLength": 20000}
SHORT = {"type": "string", "maxLength": 300}
JOB = {"type": "object", "required": ["id", "title", "company"], "properties": {
    "id": {"type": "string", "maxLength": 300}, "title": SHORT, "company": SHORT, "location": SHORT,
    "url": {"type": "string", "maxLength": 2000}, "ats": {"type": "string", "maxLength": 60}, "text": TEXT,
    "posted": {"type": "string", "maxLength": 40}}}
OBJECT = {"type": "object"}


def _profile(ws, role, years, location="", company=""):
    resume = J().resume_text(ws)
    return {"role": role, "years": years, "location": location, "company": company, "resume": resume,
            "skills": [s for s in J().skill_hits(resume, J().SKILLS) if s not in ("C", "R")]}


@tool("profile.candidate", "1", "The user's resume text and the skills Inkwell finds in it.",
      {"type": "object", "additionalProperties": False, "properties": {}},
      {"type": "object", "required": ["resume", "skills"], "properties": {
          "resume": TEXT, "skills": {"type": "array", "items": SHORT, "maxItems": 200}}}, READ)
def candidate(ctx):
    resume = J().resume_text(ctx.workspace())
    return {"resume": resume[:20000], "skills": [s for s in J().skill_hits(resume, J().SKILLS) if s not in ("C", "R")][:200]}


@tool("rules.read", "1", "The user's own job rules: target roles, companies to avoid, minimum pay, remote, dealbreakers.",
      {"type": "object", "additionalProperties": False, "properties": {}}, OBJECT, READ)
def rules(ctx):
    return get_rules(ctx.workspace())


@tool("preferences.taste", "1", "What the user has liked and passed on so far (learned from their feedback).",
      {"type": "object", "additionalProperties": False, "properties": {}},
      {"type": "object", "required": ["likes", "dislikes", "reasons"], "properties": {
          "likes": {"type": "array", "items": SHORT}, "dislikes": {"type": "array", "items": SHORT},
          "reasons": {"type": "array", "items": OBJECT}}}, READ)
def taste(ctx):
    return learning.taste(ctx.workspace())


@tool("jobs.search", "1", "Search the chosen public sources for openings and drop duplicates. Web search sources use "
      "the user's own keys and are skipped when the user has none.",
      {"type": "object", "additionalProperties": False, "required": ["role"], "properties": {
          "role": {"type": "string", "maxLength": 120}, "company": {"type": "string", "maxLength": 120},
          "location": {"type": "string", "maxLength": 120},
          "sources": {"type": "array", "items": {"type": "string", "enum": SOURCES}, "maxItems": 4}}},
      {"type": "object", "required": ["jobs", "errors"], "properties": {
          "jobs": {"type": "array", "items": OBJECT}, "errors": {"type": "array", "items": SHORT}}}, READ, timeout=300)
def search(ctx, role, company="", location="", sources=("career_sites",)):
    ws = ctx.workspace()
    keys = ws.load("ai_keys", {}) or {}
    usable = [s for s in sources if s not in ("tavily", "brave") or keys.get(s)]
    skipped = [f"{s}: no search key saved" for s in sources if s not in usable]
    tools = make_tools(keys, ws.load("ai_portals", []) or [], location)
    jobs, errors = ranking.search_sources(tools, usable, {"role": role, "company": company, "location": location})
    return {"jobs": jobs, "errors": (errors + skipped)[:50]}


@tool("jobs.rank", "1", "Score jobs 0–100 against the user's resume with Inkwell's own engine (no model): role fit, "
      "skills, years asked, similarity, seniority, location, posting age. Returns reasons and gaps per job.",
      {"type": "object", "additionalProperties": False, "required": ["role", "years", "jobs"], "properties": {
          "role": {"type": "string", "maxLength": 120}, "years": {"type": "number", "minimum": 0, "maximum": 60},
          "location": {"type": "string", "maxLength": 120}, "company": {"type": "string", "maxLength": 120},
          "jobs": {"type": "array", "items": JOB, "maxItems": 500}}},
      {"type": "array", "items": {"type": "object", "required": ["id", "score", "reasons", "gaps"]}}, READ)
def rank(ctx, role, years, jobs, location="", company=""):
    profile = _profile(ctx.workspace(), role, years, location, company)
    return ranking.rank(profile, [{"location": "", "text": "", **j} for j in jobs])


@tool("jobs.liveness", "1", "Is this posting still open? Checks the company's job board, else the job page.",
      {"type": "object", "additionalProperties": False, "required": ["job"], "properties": {"job": JOB}},
      {"type": "object", "required": ["state", "reason"], "properties": {
          "state": {"type": "string", "enum": ["active", "expired", "uncertain"]}, "reason": SHORT}}, READ, timeout=60)
def liveness(ctx, job):
    return signals.liveness(job)


@tool("rules.check", "1", "Which of the user's own rules each job breaks (never apply to, pay floor, remote only, no "
      "contract, sponsorship, most years, words to avoid). A missing fact never breaks a rule.",
      {"type": "object", "additionalProperties": False, "required": ["jobs"], "properties": {
          "jobs": {"type": "array", "items": JOB, "maxItems": 500}}},
      {"type": "object"}, READ)
def check_rules(ctx, jobs):
    from features.ai_jobs.signals import rule_hits
    r = get_rules(ctx.workspace())
    out = {}
    for j in jobs:
        try:
            out[j["id"]] = rule_hits({"location": "", "url": "", **j}, j.get("text", ""), r)
        except Exception:  # one odd posting never spoils the rest (as the match run does)
            out[j["id"]] = []
    return out
