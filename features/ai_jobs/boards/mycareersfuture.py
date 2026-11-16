"""MyCareersFuture (Singapore government job portal; the public search API its site uses)."""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "MyCareersFuture", "search", ["sg"]


def fetch(role):
    d = get_json("https://api.mycareersfuture.gov.sg/v2/search", method="POST", params={"limit": 100, "page": 0},
                 json={"search": role, "sortBy": ["new_posting_date"]}) or {}
    out = []
    for r in d.get("results") or []:
        meta = r.get("metadata") or {}
        if not meta.get("jobPostId") or not meta.get("jobDetailsUrl") or not r.get("title"):
            continue
        company = (r.get("hiringCompany") or {}).get("name") or (r.get("postedCompany") or {}).get("name", "")
        s = r.get("salary") or {}
        pay = f"SGD {s['minimum']:,}–{s['maximum']:,} / {((s.get('type') or {}).get('salaryType') or 'month').lower()}" \
            if s.get("minimum") and s.get("maximum") and not meta.get("isHideSalary") else ""
        district = ((r.get("address") or {}).get("districts") or [{}])[0].get("region", "")
        out.append({"id": f"mcf:{meta['jobPostId']}", "ats": "", "board": "mycareersfuture", "title": r["title"], "company": company.title(),
                    "location": ", ".join(filter(None, [district, "Singapore"])), "url": meta["jobDetailsUrl"], "text": "",
                    "posted": iso_day(meta.get("newPostingDate")), "salary": pay, "via": NAME})
    return out[:100]
