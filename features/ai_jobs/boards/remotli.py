"""Remotli (remotli.ch): Swiss remote and hybrid jobs, public API."""
from features.ai_jobs.boards._util import get_json, iso_day, plain, title_matches

NAME, KIND, REGIONS = "Remotli", "search", ["ch"]


def fetch(role):
    out = []
    for page in range(1, 9):
        d = get_json(f"https://remotli.ch/api/jobs?page={page}&limit=50&remote=all") or {}
        rows = d.get("jobs") or []
        for row in rows:
            j = row.get("jobs") or {}
            if not j.get("title") or not title_matches(j["title"], role):
                continue
            url = j.get("applyUrl") if str(j.get("applyUrl", "")).startswith("https://") else f"https://remotli.ch/jobs/{j.get('slug', '')}"
            pay = f"{j.get('salaryCurrency') or ''} {j.get('salaryMin') or ''}–{j.get('salaryMax') or ''}".strip(" –") if j.get("salaryMin") else ""
            out.append({"id": f"rml:{j.get('id')}", "ats": "", "board": "remotli", "title": j["title"],
                        "company": j.get("company") or (row.get("companies") or {}).get("name", ""), "location": j.get("location", ""),
                        "url": url, "text": plain(j.get("description", "")), "posted": iso_day(j.get("publishedAt")), "salary": pay, "via": NAME})
        if len(rows) < 50 or len(out) >= 100:
            break
    return out[:100]
