"""Comeet company boards (public hosted page www.comeet.com/jobs/<name>/<uid>; slug is "<name>/<uid>"). Example: coralogix/06.004"""
import json
import re

from features.ai_jobs.boards._util import get_text, iso_day, plain

NAME, KIND, REGIONS = "Comeet", "company", ["il", "global"]


def fetch(slug):
    if not re.fullmatch(r"[\w-]+/[0-9A-Fa-f]{2}\.[0-9A-Fa-f]{3}", slug or ""):
        return []
    page = get_text(f"https://www.comeet.com/jobs/{slug}")
    m = re.search(r"COMPANY_POSITIONS_DATA\s*=\s*(\[.*?\]);\s*\n", page, re.S)
    company = re.search(r'COMPANY_DATA\s*=\s*\{"name":\s*"([^"]+)"', page)
    try:
        rows = json.loads(m.group(1)) if m else []
    except ValueError:
        return []
    out = []
    for j in rows:
        url = j.get("url_comeet_hosted_page") or j.get("url_active_page") or ""
        if not (j.get("name") and url.startswith("https://")):
            continue
        loc = j.get("location") or {}
        where = ", ".join(filter(None, [loc.get("name"), "Remote" if loc.get("is_remote") and "remote" not in (loc.get("name") or "").lower() else ""]))
        text = " ".join(plain(d.get("value") or "") for d in (j.get("custom_fields") or {}).get("details") or [])
        out.append({"id": f"cmt:{slug}:{j.get('uid')}", "ats": "", "board": slug, "title": j["name"].strip(),
                    "company": company.group(1) if company else slug.split("/")[0].capitalize(), "location": where,
                    "url": url, "text": text, "posted": iso_day(j.get("time_updated")), "via": NAME})
    return out
