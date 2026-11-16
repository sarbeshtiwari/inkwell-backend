"""ApplicantPro career sites (<slug>.applicantpro.com/jobs). Example: cota"""
import re
from datetime import datetime

from features.ai_jobs.boards._util import get_json, get_text

NAME, KIND, REGIONS = "ApplicantPro", "company", ["us"]


def _day(text):
    try:
        return datetime.strptime((text or "").strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return ""


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", (slug or "").lower()) or slug in ("www", "feeds"):
        return []
    origin = f"https://{slug}.applicantpro.com"
    m = re.search(r"""(?<![\w])domainId["']?\s*[:=]\s*["']?(\d+)|/core/jobs/(\d+)""", get_text(f"{origin}/jobs/", allow_redirects=False))
    if not m:
        return []
    d = get_json(f"{origin}/core/jobs/{m.group(1) or m.group(2)}?getParams=%7B%7D") or {}
    out = []
    for j in (d.get("data") or {}).get("jobs") or []:
        if not (j.get("id") and j.get("title")):
            continue
        where = j.get("jobLocation") or ", ".join(filter(None, [j.get("city"), j.get("abbreviation") or j.get("stateName")]))
        out.append({"id": f"apro:{slug}:{j['id']}", "ats": "", "board": slug, "title": " ".join(j["title"].split()),
                    "company": slug.capitalize(), "location": where, "url": f"{origin}/jobs/{j['id']}", "text": "",
                    "posted": _day(j.get("startDateRef")), "via": NAME})
    return out
