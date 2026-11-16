"""Apple's own career site (jobs.apple.com search page, read from its server-rendered data)."""
import json
import re
from urllib.parse import quote

from features.ai_jobs.boards._util import get_text, iso_day

NAME, KIND, REGIONS = "Apple Jobs", "search", ["global"]
BASE = "https://jobs.apple.com"
HYDRATION = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("((?:\\.|[^"\\])*)"\)')


def _rows(html):
    m = HYDRATION.search(html or "")
    try:
        return ((json.loads(json.loads(f'"{m.group(1)}"')).get("loaderData") or {}).get("search") or {}).get("searchResults") or []
    except (AttributeError, ValueError, TypeError):
        return []


def _job(r):
    team = (r.get("team") or {}).get("teamCode")
    url = f"{BASE}/en-us/details/{quote(str(r['id']), safe='')}/{quote(r.get('transformedPostingTitle') or 'role', safe='')}"
    locs = list(dict.fromkeys(l.get("name") for l in r.get("locations") or [] if l.get("name")))
    return {"id": f"apple:{r['id']}", "ats": "", "board": "apple", "title": r.get("postingTitle", "").strip(), "company": "Apple",
            "location": "; ".join(locs), "url": url + (f"?team={quote(team)}" if team else ""), "text": r.get("jobSummary", ""),
            "posted": iso_day(r.get("postDateInGMT")), "via": NAME}


def fetch(role):
    jobs = {}
    for page in (1, 2, 3):
        rows = _rows(get_text(f"{BASE}/en-us/search", params={"search": role, "sort": "relevance", "page": page}))
        for r in rows:
            if r.get("postingTitle") and r.get("id"):
                jobs.setdefault(r["id"], _job(r))
        if len(rows) < 20:
            break
    return list(jobs.values())
