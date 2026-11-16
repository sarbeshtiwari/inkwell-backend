"""Solid.Jobs (Polish jobs with salaries; documented public offers API, filtered by title)."""
import re

from features.ai_jobs.boards._util import get_json, plain, title_matches

NAME, KIND, REGIONS = "Solid.Jobs", "search", ["pl"]
DIVISIONS = [("market", "marketing"), ("sales|account", "sales"), ("\\bhr\\b|recruit|talent", "hr"), ("financ|account", "finances")]


def fetch(role):
    division = next((d for pat, d in DIVISIONS if re.search(pat, (role or "").lower())), "it")
    d = get_json(f"https://solid.jobs/public-api/offers/{division}", params={"campaign": "reachout"}) or {}
    out = []
    for j in d.get("jobs") or []:
        if not j.get("url") or not j.get("jobOfferKey") or not title_matches(j.get("title"), role):
            continue
        s = j.get("salary") or {}
        pay = f"{s.get('currency', '')} {int(s['from']):,}–{int(s['to']):,} / {s.get('period', '').lower()} ({s.get('employmentType', '')})" \
            if s.get("from") and s.get("to") else ""
        out.append({"id": f"sol:{j['jobOfferKey']}", "ats": "", "board": "solidjobs", "title": j["title"], "company": j.get("company", ""),
                    "location": ", ".join((j.get("locations") or []) + (["Remote"] if j.get("isRemote") else [])), "url": j["url"],
                    "text": plain(j.get("description", "")), "posted": "", "salary": pay, "via": NAME})
    return out[:100]
