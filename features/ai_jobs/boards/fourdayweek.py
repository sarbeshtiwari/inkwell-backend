"""4 Day Week (public jobs API with keyword search; locked listings without a public page are skipped)."""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "4 Day Week", "search", ["global"]


def fetch(role):
    out = []
    for page in (1, 2, 3):
        d = get_json("https://4dayweek.io/api/jobs", params={"page": page, "q": role}) or {}
        for j in d.get("jobs") or []:
            if not j.get("slug") or j.get("is_expired") or j.get("locked"):
                continue
            loc = (j.get("locations") or [{}])[0] or {}
            where = ", ".join(filter(None, [loc.get("city"), loc.get("country"), "Remote" if j.get("work_arrangement") == "remote" else ""]))
            out.append({"id": f"4dw:{j['slug']}", "ats": "", "board": "fourdayweek", "title": j.get("title", ""),
                        "company": j.get("company_name", ""), "location": where, "url": f"https://4dayweek.io/job/{j['slug']}",
                        "text": "", "posted": iso_day(j.get("posted")), "salary": j.get("salary") or "", "via": NAME})
        if not d.get("has_more"):
            break
    return out[:100]
