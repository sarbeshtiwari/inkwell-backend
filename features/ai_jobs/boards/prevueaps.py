"""Prevue APS hiring sites (<slug>.prevueaps.ca/jobs). Example: tbca"""
import json
import re
from datetime import datetime

from features.ai_jobs.boards._util import get_json, get_text

NAME, KIND, REGIONS = "Prevue APS", "company", ["ca"]


def _day(text):  # "Oct 08, 2026"
    try:
        return datetime.strptime(text or "", "%b %d, %Y").date().isoformat()
    except ValueError:
        return ""


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        return []
    origin = f"https://{slug}.prevueaps.ca"
    page = get_text(f"{origin}/jobs/", allow_redirects=False)
    m = re.search(r"/core/jobs/(\d+)|data-domain-id=[\"'](\d+)|domainId[\"']?\s*[:=]\s*[\"']?(\d+)|siteId[\"']?\s*[:=]\s*[\"']?(\d+)", page)
    if not m:
        return []
    params = json.dumps({"showDate": True, "showLocation": True, "showEmploymentType": True, "showCategory": True})
    d = get_json(f"{origin}/core/jobs/{next(filter(None, m.groups()))}", params={"getParams": params}) or {}
    out = []
    for j in (d.get("data") or {}).get("jobs") or []:
        url = j.get("jobUrl") or ""
        if not (j.get("title") and url.startswith("https://")):
            continue
        pay = " - ".join(filter(None, [j.get("minSalary"), j.get("maxSalary")]))
        out.append({"id": f"prv:{slug}:{j.get('id')}", "ats": "", "board": slug, "title": j["title"].strip(),
                    "company": slug.upper() if len(slug) <= 4 else slug.capitalize(),
                    "location": j.get("jobLocation") or ", ".join(filter(None, [j.get("city"), j.get("abbreviation")])),
                    "url": url, "text": "", "posted": _day(j.get("startDateRef")),
                    "salary": f"{pay} {j.get('payTypeFrame') or ''}".strip() if pay else "", "via": NAME})
    return out
