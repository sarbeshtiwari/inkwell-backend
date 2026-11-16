"""Jibe (iCIMS) career sites (<slug>.jibeapply.com/api/jobs). Example: ascension"""
import re

from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Jibe", "company", ["global"]


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        return []
    origin, out = f"https://{slug}.jibeapply.com", []
    for page in range(1, 6):
        d = get_json(f"{origin}/api/jobs", params={"limit": 100, "page": page}) or {}
        rows = d.get("jobs") or []
        for row in rows:
            j = (row or {}).get("data") or row or {}
            jid = j.get("slug") or j.get("req_id")
            if not (j.get("title") and jid):
                continue
            out.append({"id": f"jibe:{slug}:{jid}", "ats": "", "board": slug, "title": j["title"].strip(),
                        "company": j.get("hiring_organization") or slug.capitalize(),
                        "location": j.get("full_location") or ", ".join(filter(None, [j.get("city"), j.get("country")])),
                        "url": f"{origin}/jobs/{jid}", "text": plain(j.get("description", "")),
                        "posted": iso_day(j.get("posted_date") or j.get("create_date")), "via": NAME})
        if len(rows) < 100 or len(out) >= 500:
            break
    return out
