"""Flowxtra central job feed (all Flowxtra employers); slug "all" or a company name to keep only that employer. Example: all"""
import re

from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Flowxtra", "company", ["global"]


def _key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def fetch(slug):
    want = "" if _key(slug) == "all" else _key(slug)
    out = []
    for page in range(1, 4):
        d = (get_json(f"https://app.flowxtra.com/api/central/jobs?status=Live&per_page=100&page={page}") or {}).get("data") or {}
        for j in d.get("data") or []:
            url, company = j.get("urlJobApplay") or "", j.get("name_company") or "Flowxtra"
            if not (j.get("title") and url.startswith("https://flowxtra.com/")) or (want and _key(company) != want):
                continue
            where = ", ".join(filter(None, [j.get("city_company"), j.get("state_company"), j.get("country_company"),
                                            "Remote" if j.get("workplace") == "Remote" else ""]))
            pay = f"{j['min_salary']}-{j['max_salary']} {j.get('currency') or ''}/{j.get('rate_salary') or ''}" if j.get("max_salary") else ""
            out.append({"id": f"fx:{slug}:{j.get('has_id') or j.get('id')}", "ats": "", "board": slug, "title": j["title"].strip(),
                        "company": company, "location": where, "url": url, "text": "", "posted": iso_day(j.get("date_share")),
                        "salary": pay, "via": NAME})
        if not d.get("next_page_url"):
            break
    return out
