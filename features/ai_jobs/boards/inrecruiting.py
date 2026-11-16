"""InRecruiting / Intervieweb career pages (<slug>.intervieweb.it/<lang>/career; slug may be "tenant/en"). Example: maggioli"""
import re

import requests

from features.ai_jobs.boards._util import TIMEOUT, unescape
from features.ai_jobs.common import UA

NAME, KIND, REGIONS = "InRecruiting", "company", ["it"]

CARD = re.compile(r'<a[^>]+href="(https://[a-z0-9-]+\.intervieweb\.it/jobs/[^"#?]+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>', re.S)
LOC = re.compile(r'mi-location_on.*?</i>\s*(?:<span[^>]*sr-only[^>]*>.*?</span>)?(.*?)</span>', re.S)
NEXT = re.compile(r"pagination_next[^>]*onclick=\"researchAnnounces\(\s*(\d+)\s*,\s*'([a-z_]+)'")


def _cards(page, slug, host):
    hits = list(CARD.finditer(page))
    for i, m in enumerate(hits):
        if f"//{host}/" not in m.group(1):
            continue
        chunk = page[m.end():hits[i + 1].start() if i + 1 < len(hits) else len(page)]
        loc = LOC.search(chunk)
        jid = re.search(r"-(\d+)/[a-z]{2}/?$", m.group(1))
        yield {"id": f"inr:{slug}:{jid.group(1) if jid else m.group(1)}", "ats": "", "board": slug,
               "title": unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(2))), "company": slug.split("/")[0].capitalize(),
               "location": unescape(re.sub(r"<[^>]+>|\s+", " ", loc.group(1))) if loc else "",
               "url": unescape(m.group(1)), "text": "", "posted": "", "via": NAME}


def fetch(slug):
    tenant, _, lang = slug.partition("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", tenant or ""):
        return []
    host, s = f"{tenant}.intervieweb.it", requests.Session()
    s.headers.update(UA)
    try:
        page = s.get(f"https://{host}/{lang or 'it'}/career", timeout=TIMEOUT, allow_redirects=False).text
        jobs = list(_cards(page, slug, host))
        api = re.search(r'id="url-for-announces"[^>]*value="([^"]+)"', page)
        section = re.search(r"['\"]section['\"]\s*:\s*['\"]([A-Za-z0-9_-]+)", page)
        nxt = NEXT.search(page)
        for _ in range(4):  # at most 5 pages
            if not (nxt and api and section):
                break
            body = {"act1": "vacancyListCareer", "section": section.group(1), "order": nxt.group(2), "page": nxt.group(1)}
            page = (s.post(unescape(api.group(1)), data=body, timeout=TIMEOUT).json() or {}).get("data") or ""
            more = list(_cards(page, slug, host))
            if not more:
                break
            jobs += more
            nxt2 = NEXT.search(page)
            nxt = nxt2 if nxt2 and int(nxt2.group(1)) > int(nxt.group(1)) else None
    except (requests.RequestException, ValueError):
        pass
    seen = set()
    return [j for j in jobs if not (j["url"] in seen or seen.add(j["url"]))]
