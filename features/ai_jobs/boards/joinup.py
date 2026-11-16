"""Joinup (joinup.ch): Swiss startup jobs, from the data its browse page carries."""
import json
import re

from features.ai_jobs.boards._util import get_text, iso_day, title_matches

NAME, KIND, REGIONS = "Joinup", "search", ["ch"]


def fetch(role):
    html = get_text("https://joinup.ch/browse/jobs")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    try:
        hits = json.loads(m.group(1))["props"]["pageProps"]["serverState"]["initialResults"]["jobs"]["results"][0]["hits"] if m else []
    except (ValueError, KeyError, TypeError, IndexError):
        hits = []
    out = []
    for h in hits:
        title = h.get("title") or h.get("headline") or ""
        if not (h.get("slug") and title) or not title_matches(title, role):
            continue
        loc = h.get("location")
        loc = loc if isinstance(loc, str) else (loc or {}).get("name") or (loc or {}).get("city") or ""
        out.append({"id": f"jnp:{h['slug'][-60:]}", "ats": "", "board": "joinup", "title": title, "company": h.get("startup", ""),
                    "location": loc or "Switzerland", "url": f"https://joinup.ch/job/{h['slug']}", "text": "",
                    "posted": iso_day(str(h.get("created") or "")), "via": NAME})
    return out
