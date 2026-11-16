"""Collage HR job sites (secure.collage.co/jobs/<slug>). Example: recoverysciencecorporation"""
from urllib.parse import quote

from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Collage", "company", ["ca"]


def fetch(slug):
    d = get_json(f"https://api.collage.co/v1/positions/{quote(slug or '', safe='')}", allow_redirects=False) or {}
    out = []
    for j in d.get("positions") or [] if isinstance(d, dict) else []:
        url = j.get("hostedUrl") or j.get("applyUrl") or ""
        if not (j.get("title") and url.startswith("https://")):
            continue
        loc = j.get("location")
        out.append({"id": f"clg:{slug}:{j.get('id')}", "ats": "", "board": slug, "title": j["title"].strip(),
                    "company": slug.capitalize(), "location": "; ".join(loc) if isinstance(loc, list) else (loc or ""),
                    "url": url, "text": plain(j.get("description", "")), "posted": iso_day(j.get("createdDate")), "via": NAME})
    return out
