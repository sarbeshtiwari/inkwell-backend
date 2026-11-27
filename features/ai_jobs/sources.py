"""Search sources: public Greenhouse/Lever/Ashby (+ Workable, SmartRecruiters, Recruitee) boards and public job boards,
as LangChain tools."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from features.ai_jobs.common import get_json, plain
from features.ai_jobs.providers import extra_board_jobs
from features.ai_jobs import boards as more_boards


# Public job boards checked when no company is named (verified to exist; a missing one is simply skipped).
BOARDS = [("greenhouse", s) for s in ("razorpaysoftwareprivatelimited", "groww", "druva", "hackerrank", "gitlab", "stripe",
                                      "databricks", "airbnb", "coinbase", "figma", "dropbox", "twilio", "mongodb", "elastic",
                                      "samsara", "cloudflare", "okta", "datadog", "zscaler")] + \
         [("lever", s) for s in ("meesho", "cred", "paytm", "zeta", "spotify", "palantir")] + \
         [("ashby", s) for s in ("sarvam", "notion", "openai", "linear", "ramp", "supabase", "replit", "posthog", "cohere",
                                 "elevenlabs", "perplexity")]


def norm_gh(board, j):
    return {"id": f"gh:{board}:{j['id']}", "ats": "greenhouse", "board": board, "title": j.get("title", ""),
            "company": board.replace("softwareprivatelimited", "").capitalize(), "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""), "text": plain(j.get("content", "")), "posted": (j.get("updated_at") or "")[:10]}


def norm_lever(board, j):
    cat = j.get("categories") or {}
    return {"id": f"lv:{board}:{j['id']}", "ats": "lever", "board": board, "title": j.get("text", ""), "company": board.capitalize(),
            "location": ", ".join(filter(None, [cat.get("location", ""), j.get("workplaceType", "") if j.get("workplaceType") == "remote" else ""])),
            "url": j.get("hostedUrl", ""), "apply_url": j.get("applyUrl", ""),
            "text": plain(" ".join([j.get("descriptionPlain", ""), *[f"{x.get('text', '')} {plain(x.get('content', ''))}" for x in j.get("lists", [])]])),
            "posted": datetime.fromtimestamp(j.get("createdAt", 0) / 1000).date().isoformat() if j.get("createdAt") else ""}


def norm_ashby(board, j):
    return {"id": f"ab:{board}:{j['id']}", "ats": "ashby", "board": board, "title": j.get("title", ""), "company": board.capitalize(),
            "location": ", ".join(filter(None, [j.get("location", ""), "Remote" if j.get("isRemote") else ""])),
            "url": j.get("jobUrl", ""), "apply_url": j.get("applyUrl", ""), "text": plain(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
            "posted": (j.get("publishedAt") or "")[:10]}


def board_jobs(ats, board):
    if ats == "greenhouse":
        d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
        return [norm_gh(board, j) for j in (d or {}).get("jobs", [])]
    if ats == "lever":
        d = get_json(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})
        return [norm_lever(board, j) for j in d] if isinstance(d, list) else []
    if ats == "site":
        from features.ai_jobs import sitejobs
        return sitejobs.fetch(board)
    if ats in more_boards.company_boards():
        return more_boards.company_boards()[ats].fetch(board) or []
    if ats != "ashby":
        return extra_board_jobs(ats, board)
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    return [norm_ashby(board, j) for j in (d or {}).get("jobs", []) if j.get("isListed", True)]


def safe_board_jobs(ats, board):
    try:
        return board_jobs(ats, board) or []
    except Exception as e:  # one odd board never spoils the search
        print(f"[Inkwell] board {ats}:{board} skipped: {e!r}", flush=True)
        return []


def site_guesses(company):
    """Likely careers pages on the company's own domain: 'Acme Labs' -> acmelabs.com/careers, careers.acmelabs.com."""
    words = [w for w in re.findall(r"[a-z0-9]+", company.lower()) if w not in ("pvt", "ltd", "private", "limited", "inc", "llc")]
    name = "".join(words)
    return [f"https://{name}.com/careers", f"https://careers.{name}.com"] if 2 < len(name) <= 30 else []


def company_boards(company):
    """Likely board names for a company: 'Acme Labs' -> acme, acmelabs, acme-labs."""
    words = re.findall(r"[a-z0-9]+", company.lower())
    words = [w for w in words if w not in ("pvt", "ltd", "private", "limited", "inc", "llc", "technologies", "india")] or words
    names = {"".join(words), "-".join(words), words[0]} if words else set()
    return [(ats, n) for n in names for ats in ("greenhouse", "lever", "ashby") + GUESSABLE]


# Company boards whose address is just a name, so a company name can be tried on them (Workday, SuccessFactors
# and the like need a full address: add those under My companies).
GUESSABLE = ("workable", "smartrecruiters", "recruitee", "bamboohr", "teamtailor", "breezy", "personio", "pinpoint",
             "homerun", "rippling", "gem", "jazzhr", "jobvite")


def make_tools(location=""):
    """The search sources as LangChain tools (the Job Scout's jobs.search tool calls them)."""
    from langchain_core.tools import tool

    @tool
    def career_sites(role: str, company: str = "") -> list:
        """Open positions from public company career sites on Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee."""
        if company:  # that company's boards: its guessable boards and its own site
            boards = company_boards(company) + [("site", u) for u in site_guesses(company)]
        else:
            boards = BOARDS
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [j for js in pool.map(lambda b: safe_board_jobs(*b), boards) for j in js]
        if company:  # a guessed site can belong to someone else: keep the jobs of the company asked for
            key = re.sub(r"[^a-z0-9]", "", company.lower())[:12]
            found = [j for j in found if not j["id"].startswith("site:") or key in re.sub(r"[^a-z0-9]", "", j.get("company", "").lower())]
        return found

    @tool
    def remote_boards(role: str) -> list:
        """Jobs from public job boards: remote boards, developer boards, big employers' career sites and the boards
        of the user's country (Remotive, Hacker News, Built In, MyCareersFuture, JustJoin, Amazon, Google...)."""
        jobs, _errors = more_boards.run_all([(m, role) for m in more_boards.search_boards(location).values()], location=location)
        return jobs

    return {"career_sites": career_sites, "remote_boards": remote_boards}
