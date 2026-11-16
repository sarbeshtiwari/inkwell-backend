"""Just Join IT (Polish IT jobs; the public offers API its own site uses)."""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Just Join IT", "search", ["pl"]


def fetch(role):
    d = get_json("https://justjoin.it/api/candidate-api/offers",
                 params={"from": 0, "itemsCount": 100, "keywords": role, "currency": "pln"}) or {}
    out = []
    for j in d.get("data") or []:
        if not j.get("slug") or not j.get("title"):
            continue
        pay = next((f"PLN {int(e['from']):,}–{int(e['to']):,} ({e.get('type', '')})" for e in j.get("employmentTypes") or []
                    if e.get("from") and e.get("to")), "")
        out.append({"id": f"jj:{j['slug']}", "ats": "", "board": "justjoin", "title": j["title"], "company": j.get("companyName", ""),
                    "location": ", ".join(filter(None, [j.get("city"), (j.get("workplaceType") or "").replace("_", " ")])),
                    "url": f"https://justjoin.it/job-offer/{j['slug']}",
                    "text": "Skills: " + ", ".join(s.get("name", "") for s in j.get("requiredSkills") or []) if j.get("requiredSkills") else "",
                    "posted": iso_day(j.get("lastPublishedAt") or j.get("publishedAt")), "salary": pay, "via": NAME})
    return out[:100]
