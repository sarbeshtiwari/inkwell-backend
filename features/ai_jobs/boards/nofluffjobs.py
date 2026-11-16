"""No Fluff Jobs (Polish IT jobs; the public search API its own site uses)."""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "No Fluff Jobs", "search", ["pl"]


def fetch(role):
    d = get_json("https://nofluffjobs.com/api/search/posting", method="POST",
                 params={"sort": "newest", "pageTo": 1, "pageSize": 100, "salaryCurrency": "PLN", "salaryPeriod": "month",
                         "region": "pl", "language": "en-GB"},
                 json={"rawSearch": role, "criteria": "", "url": {"searchParam": role}, "pageSize": 100, "withSalaryMatch": True},
                 headers={"content-type": "application/infiniteSearch+json"}) or {}
    out = []
    for j in d.get("postings") or []:
        if not j.get("url") or not j.get("title"):
            continue
        places = (j.get("location") or {}).get("places") or []
        cities = list(dict.fromkeys(p["city"] for p in places if p.get("city")))
        s = j.get("salary") or {}
        pay = f"{s.get('currency', '')} {int(s['from']):,}–{int(s['to']):,} / {s.get('period', 'Month').lower()} ({s.get('type', '')})" \
            if s.get("from") and s.get("to") else ""
        out.append({"id": f"nfj:{j['url']}", "ats": "", "board": "nofluffjobs", "title": j["title"], "company": j.get("name", ""),
                    "location": ", ".join(cities[:4]) or "Poland", "url": f"https://nofluffjobs.com/job/{j['url']}", "text": "",
                    "posted": iso_day(int(j["posted"]) // 1000 if j.get("posted") else ""), "salary": pay, "via": NAME})
    return out[:100]
