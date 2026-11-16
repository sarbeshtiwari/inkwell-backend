"""Landing.jobs (European tech jobs, mostly Portugal; public jobs API with search)."""
from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Landing.jobs", "search", ["pt", "es", "de", "nl", "global"]


def fetch(role):
    d = get_json("https://landing.jobs/api/v1/jobs", params={"q": role})
    out = []
    for j in d if isinstance(d, list) else []:
        if not j.get("id") or not j.get("url") or not j.get("title"):
            continue
        loc = (j.get("locations") or [{}])[0] or {}
        pay = f"{j.get('currency_code') or ''} {j['gross_salary_low']:,}–{j['gross_salary_high']:,}".strip() \
            if j.get("gross_salary_low") and j.get("gross_salary_high") else ""
        out.append({"id": f"lnd:{j['id']}", "ats": "", "board": "landingjobs", "title": j["title"],
                    "company": j["url"].split("/at/", 1)[-1].split("/", 1)[0].replace("-", " ").title() if "/at/" in j["url"] else "",
                    "location": ", ".join(filter(None, [loc.get("city"), loc.get("country_code"), "Remote" if j.get("remote") else ""])),
                    "url": j["url"], "text": plain(f"{j.get('role_description') or ''} {j.get('main_requirements') or ''}"),
                    "posted": iso_day(j.get("published_at")), "salary": pay, "via": NAME})
    return out[:100]
