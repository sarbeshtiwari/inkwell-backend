"""Dover careers pages (app.dover.com/<Company>/careers/<board-uuid>); slug "Company/uuid". Example: CahiraTechnologies/36e53c57-ce36-45cd-a9fa-2556d522e211"""
import re
from urllib.parse import quote

from features.ai_jobs.boards._util import get_json

NAME, KIND, REGIONS = "Dover", "company", ["global"]
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _where(locs, workplace):
    out = []
    for loc in locs or []:
        place = ((loc.get("location_option") or {}).get("display_name") or loc.get("name") or "").strip()
        kind = (loc.get("location_type") or "").upper()
        out.append(f"Remote ({place})" if kind == "REMOTE" and place else "Remote" if kind == "REMOTE" else place)
    where = " · ".join(dict.fromkeys(filter(None, out)))
    return where or ("Remote" if (workplace or "").upper() == "REMOTE" else "")


def fetch(slug):
    company, _, board = (slug or "").partition("/")
    if not company or not UUID.match(board):
        return []
    out = []
    for page in range(5):
        d = get_json(f"https://app.dover.com/api/v1/careers-page/{board}/jobs?limit=100&offset={page * 100}") or {}
        for j in d.get("results") or []:
            if not j.get("title") or j.get("is_published") is False or j.get("is_sample"):
                continue
            out.append({"id": f"dov:{company}:{j['id']}", "ats": "", "board": slug, "title": j["title"].strip(),
                        "company": company, "location": _where(j.get("locations"), j.get("workplace_type")),
                        "url": f"https://app.dover.com/apply/{quote(company)}/{j['id']}", "text": "", "posted": "", "via": NAME})
        if not d.get("next"):
            break
    return out
