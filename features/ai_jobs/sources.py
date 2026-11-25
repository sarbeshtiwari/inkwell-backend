"""Search sources: public Greenhouse/Lever/Ashby (+ Workable, SmartRecruiters, Recruitee) boards, remote job boards
and Tavily/Brave web search, as LangChain tools."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlsplit
import requests

from features.ai_jobs.common import C, get_json, plain
from features.ai_jobs.providers import extra_board_jobs
from features.ai_jobs import boards as more_boards


# Public job boards checked when no company is named (verified to exist; a missing one is simply skipped).
BOARDS = [("greenhouse", s) for s in ("razorpaysoftwareprivatelimited", "groww", "druva", "hackerrank", "gitlab", "stripe",
                                      "databricks", "airbnb", "coinbase", "figma", "dropbox", "twilio", "mongodb", "elastic",
                                      "samsara", "cloudflare", "okta", "datadog", "zscaler")] + \
         [("lever", s) for s in ("meesho", "cred", "paytm", "zeta", "spotify", "palantir")] + \
         [("ashby", s) for s in ("sarvam", "notion", "openai", "linear", "ramp", "supabase", "replit", "posthog", "cohere",
                                 "elevenlabs", "perplexity")]


ATS_URL = re.compile(r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?P<gh>[\w-]+)/jobs/(?P<ghid>\d+)"
                     r"|https?://jobs\.lever\.co/(?P<lv>[\w-]+)/(?P<lvid>[0-9a-f-]{36})"
                     r"|https?://jobs\.ashbyhq\.com/(?P<ab>[\w.-]+)/(?P<abid>[0-9a-f-]{36})", re.I)


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


def from_ats_url(url):
    """A job found by web search, read from its job board when it's on Greenhouse, Lever or Ashby."""
    m = ATS_URL.search(url or "")
    if not m:
        return None
    if m.group("gh"):
        d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{m.group('gh')}/jobs/{m.group('ghid')}")
        return norm_gh(m.group("gh"), d) if d and d.get("id") else None
    if m.group("lv"):
        d = get_json(f"https://api.lever.co/v0/postings/{m.group('lv')}/{m.group('lvid')}")
        return norm_lever(m.group("lv"), d) if d and d.get("id") else None
    jobs = board_jobs("ashby", m.group("ab"))
    return next((j for j in jobs if j["id"].endswith(m.group("abid"))), None)


SEARCH_SITES = "(site:boards.greenhouse.io OR site:job-boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com)"


def search_query(want):
    parts = [f'"{want["role"]}"', want.get("company", ""), want.get("location", "")]
    return " ".join(p for p in parts if p) + " " + SEARCH_SITES


def page_job(url):
    """A job page on a company's own site, read from the JobPosting data it publishes (None when it has none)."""
    from features.ai_jobs import sitejobs
    final, html = sitejobs.get_page(url)
    jobs = sitejobs.jobs_in(html, final, (urlsplit(final).hostname or "").removeprefix("www.")) if html else []
    return jobs[0] if len(jobs) == 1 else None


def web_hits_to_jobs(hits):
    out, seen, rest = [], set(), []
    for h in hits:
        url = h.get("url", "")
        if url in seen:
            continue
        seen.add(url)
        job = from_ats_url(url)
        if job:
            out.append(job)
        elif re.match(r"https?://", url):
            rest.append(h)
    with ThreadPoolExecutor(max_workers=8) as pool:  # a job page elsewhere: its own JobPosting data when it has some
        pages = list(pool.map(lambda h: page_job(h["url"]), rest[:20])) + [None] * max(0, len(rest) - 20)
    for h, job in zip(rest, pages):
        url = h["url"]
        if job:
            out.append(job)
        else:
            out.append({"id": "web:" + C().lookup_hash(url)[:16], "ats": "", "board": "", "title": h.get("title", "")[:160],
                        "company": urlsplit(url).hostname.removeprefix("www.").split(".")[0].capitalize(), "location": "",
                        "url": url, "text": plain(h.get("content", ""))[:4000], "posted": ""})
    return out


def make_tools(keys, portals=(), location=""):
    """The search sources as LangChain tools, so the chain (or an agent) can call them the same way."""
    from langchain_core.tools import tool

    @tool
    def career_sites(role: str, company: str = "") -> list:
        """Open positions from public company career sites on Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee."""
        mine = [(p["ats"], p["slug"]) for p in portals]
        if company:  # that company's boards: the ones under My companies with its name, its guessable boards, its own site
            key = re.sub(r"[^a-z0-9]", "", company.lower())
            boards = [(p["ats"], p["slug"]) for p in portals if key and key in re.sub(r"[^a-z0-9]", "", p["name"].lower())]
            boards += company_boards(company) + [("site", u) for u in site_guesses(company)]
        else:
            boards = BOARDS + mine
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [j for js in pool.map(lambda b: safe_board_jobs(*b), boards) for j in js]
        if company:  # a guessed site can belong to someone else: keep the jobs of the company asked for
            key = re.sub(r"[^a-z0-9]", "", company.lower())[:12]
            found = [j for j in found if not j["id"].startswith("site:") or key in re.sub(r"[^a-z0-9]", "", j.get("company", "").lower())]
        return found

    @tool
    def tavily_search(query: str) -> list:
        """Web search with Tavily (user's own key)."""
        r = requests.post("https://api.tavily.com/search", timeout=30, headers={"Authorization": f"Bearer {keys['tavily']}"},
                          json={"query": query, "max_results": 20, "search_depth": "advanced"})
        if r.status_code in (401, 403):
            raise RuntimeError("Tavily didn't accept your API key.")
        r.raise_for_status()
        return web_hits_to_jobs(r.json().get("results", []))

    @tool
    def brave_search(query: str) -> list:
        """Web search with Brave Search (user's own key)."""
        r = requests.get("https://api.search.brave.com/res/v1/web/search", timeout=30, params={"q": query, "count": 20},
                         headers={"X-Subscription-Token": keys["brave"], "Accept": "application/json"})
        if r.status_code in (401, 403, 422):
            raise RuntimeError("Brave didn't accept your API key.")
        r.raise_for_status()
        return web_hits_to_jobs([{"url": x.get("url"), "title": x.get("title"), "content": x.get("description")}
                                 for x in r.json().get("web", {}).get("results", [])])

    @tool
    def remote_boards(role: str) -> list:
        """Jobs from public job boards: remote boards, developer boards, big employers' career sites and the boards
        of the user's country (Remotive, Hacker News, Built In, MyCareersFuture, JustJoin, Amazon, Google...)."""
        jobs, _errors = more_boards.run_all([(m, role) for m in more_boards.search_boards(location).values()], location=location)
        return jobs

    return {"career_sites": career_sites, "tavily": tavily_search, "brave": brave_search, "remote_boards": remote_boards}
