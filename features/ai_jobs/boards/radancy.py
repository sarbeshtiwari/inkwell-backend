"""Radancy (TalentBrew) career sites' search-jobs results. Slug: the careers host, optionally with its language path ("jobs.boeing.com", "careers.munichre.com/de"). Example: jobs.boeing.com"""
import re
from urllib.parse import urljoin

from features.ai_jobs.boards._util import get_json, plain, unescape

NAME, KIND, REGIONS = "Radancy", "company", ["global"]
GENERIC = {"jobs", "careers", "career", "www", "en", "us"}


def _rows(frag):
    anchors = list(re.finditer(r"<a\b([^>]*)>(.*?)</a>", frag, re.S))
    for n, a in enumerate(anchors):
        jid, href = re.search(r'data-job-id="([^"]+)"', a[1]), re.search(r'href="([^"]+)"', a[1])
        if not (jid and href):
            continue
        inner = a[2]
        head = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", inner, re.S) or re.search(r'class="[^"]*title[^"]*"[^>]*>(.*?)</', inner, re.S)
        after = frag[a.end():anchors[n + 1].start() if n + 1 < len(anchors) else len(frag)]
        loc = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</(?:span|p|div)>', inner + after, re.S)
        day = re.search(r'class="[^"]*date[^"]*"[^>]*>\s*(\d{1,2})/(\d{1,2})/(\d{4})', inner + after)
        yield (jid[1], plain(head[1] if head else inner), unescape(href[1]), plain(loc[1]) if loc else "",
               f"{day[3]}-{int(day[1]):02d}-{int(day[2]):02d}" if day else "")


def fetch(slug):
    m = re.fullmatch(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)((?:/[a-z]{2}){0,2})/?", (slug or "").strip().lower())
    if not m:
        return []
    origin, lang = f"https://{m[1]}", m[2] or "/en"
    company = next((w for w in m[1].split(".") if w not in GENERIC), m[1]).capitalize()
    out, seen = [], set()
    for page in range(1, 6):
        d = get_json(f"{origin}{lang}/search-jobs/results", headers={"X-Requested-With": "XMLHttpRequest"},
                     params={"CurrentPage": page, "RecordsPerPage": 100, "SearchType": 5, "IsPagination": "True",
                             "SearchResultsModuleName": "Search Results"}) or {}
        fresh = 0
        for jid, title, href, where, day in _rows(d.get("results") or "" if isinstance(d, dict) else ""):
            if not title or jid in seen:
                continue
            seen.add(jid)
            fresh += 1
            out.append({"id": f"rdy:{m[1]}:{jid}", "ats": "", "board": slug, "title": title, "company": company,
                        "location": where, "url": urljoin(origin, href), "text": "", "posted": day, "via": NAME})
        if fresh < 50 or len(out) >= 500:
            break
    return out
