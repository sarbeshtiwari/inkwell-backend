"""Y Combinator startups hiring: the job posts YC companies publish on Hacker News (public HN API).
Adapted from Morrow (MIT, (c) 2026 Morrow contributors): sites_bot/startup_jobs.py (jobstories + parse_yc_title)."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from features.ai_jobs.boards._util import get_json, plain, title_matches

NAME, KIND, REGIONS = "Y Combinator", "search", ["global"]
API = "https://hacker-news.firebaseio.com/v0"
YC_TITLE = re.compile(r"^(?P<company>.+?)\s*\((?:YC\s*)?(?P<batch>[A-Z]\d{2})\)\s*(?:is\s+)?(?:hiring|looking for)?\s*(?:an?\s+)?(?P<role>.*)$", re.I)


def parse_yc_title(title):
    """'Acme (YC W24) Is Hiring a Senior Engineer' -> (company, batch, role)."""
    m = YC_TITLE.match((title or "").strip())
    if not m:
        return "", "", title or ""
    role = re.sub(r"^(?:is\s+)?hiring\s+(?:an?\s+)?", "", m.group("role"), flags=re.I).strip(" -–:")
    return m.group("company").strip(), m.group("batch").upper(), role or "Open roles"


def item(i):
    return get_json(f"{API}/item/{i}.json") or {}


def fetch(role):
    ids = (get_json(f"{API}/jobstories.json") or [])[:100]
    with ThreadPoolExecutor(6) as pool:
        items = list(pool.map(item, ids))
    out = []
    for it in items:
        if not it.get("title") or it.get("dead") or it.get("deleted"):
            continue
        company, batch, title = parse_yc_title(it["title"])
        if not title_matches(it["title"], role):
            continue
        where = "Remote" if re.search(r"\bremote\b", it["title"], re.I) else ""
        out.append({"id": f"yc:{it['id']}", "ats": "", "board": "news.ycombinator.com", "title": title, "company": company,
                    "location": where, "url": it.get("url") or f"https://news.ycombinator.com/item?id={it['id']}",
                    "text": plain(it.get("text") or "") + (f"\nY Combinator batch {batch}" if batch else ""),
                    "posted": datetime.fromtimestamp(it.get("time", 0)).date().isoformat() if it.get("time") else "", "via": NAME})
    return out
