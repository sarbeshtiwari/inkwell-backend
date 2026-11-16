"""Deutsche Bahn's own career site (db.jobs server-rendered search results)."""
import re

from features.ai_jobs.boards._util import get_text, plain, unescape

NAME, KIND, REGIONS = "DB Jobs", "search", ["de"]
ORIGIN = "https://db.jobs"
HIT = re.compile(r'<a\s+href="([^"]+)"[^>]*class="m-search-hit"[^>]*data-job-id="(\d+)"[^>]*>(.*?)</a>', re.S)


def fetch(role):
    html = get_text(f"{ORIGIN}/service/search/de-de/5441588",
                    params={"query": role, "sort": "score", "itemsPerPage": 100, "pageNum": 0})
    jobs = {}
    for href, jid, inner in HIT.findall(html):
        title = re.search(r'm-search-hit__title-text"[^>]*>(.*?)</span>', inner, re.S)
        if not title or jid in jobs:
            continue
        track = (re.search(r'track-interaction="bookmark job\|([^"]*)"', inner) or [None, ""])[1].split("|")
        loc = re.search(r'aria-label="Arbeitsort"[^>]*></i>(.*?)</li>', inner, re.S)
        jobs[jid] = {"id": f"db:{jid}", "ats": "", "board": "deutschebahn", "title": plain(title.group(1)),
                     "company": unescape(track[4]) if len(track) > 4 and track[4] else "Deutsche Bahn",
                     "location": plain(loc.group(1)) if loc else (track[1] if len(track) > 1 else ""),
                     "url": ORIGIN + unescape(href), "text": "", "posted": "", "via": NAME}
    return list(jobs.values())
