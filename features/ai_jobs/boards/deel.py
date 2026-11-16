"""Deel job boards (jobs.deel.com/<company>): the whole list comes with the board's page. Example: klarna"""
import re

from features.ai_jobs.boards._flight import flight_value
from features.ai_jobs.boards._util import get_text, iso_day

NAME, KIND, REGIONS = "Deel", "company", ["global"]


def fetch(slug):
    slug = (re.search(r"jobs\.deel\.com/([\w-]+)", slug or "") or [None, (slug or "").strip("/")])[1]
    if not re.fullmatch(r"[\w-]+", slug):
        return []
    rows = flight_value(get_text(f"https://jobs.deel.com/{slug}"), "jobPostings") or []
    out = []
    for j in rows if isinstance(rows, list) else []:
        if not isinstance(j, dict) or not j.get("id") or not j.get("title"):
            continue
        job = j.get("job") or {}
        locs = [", ".join(filter(None, [(l.get("location") or {}).get("name"), ((l.get("location") or {}).get("country") or {}).get("name")]))
                for l in job.get("jobLocations") or []]
        mode = {"REMOTE": "Remote", "HYBRID": "Hybrid"}.get(job.get("workArrangementEnum"), "")
        pay = job.get("currentCompensation") or {}
        out.append({"id": f"deel:{slug}:{j['id']}", "ats": "", "board": slug, "title": j["title"].strip(), "company": slug.capitalize(),
                    "location": "; ".join(filter(None, locs + [mode])), "url": f"https://jobs.deel.com/{slug}/job-details/{j['id']}/overview",
                    "text": "", "posted": iso_day(j.get("createdAt")),
                    "salary": f"{pay.get('currencyIsoCode', '')} {pay.get('minAmount', '')}–{pay.get('maxAmount', '')}".strip(" –")
                    if j.get("isCompensationVisible") and pay.get("maxAmount") else "", "via": NAME})
    return out
