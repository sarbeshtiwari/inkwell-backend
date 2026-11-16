"""Kalibrr: jobs in the Philippines and Indonesia, public job-board API."""
from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Kalibrr", "search", ["ph", "id"]


def fetch(role):
    out = []
    for offset in (0, 50):
        d = get_json("https://www.kalibrr.com/kjs/job_board/search", params={"limit": 50, "offset": offset, "text": role}) or {}
        rows = d.get("jobs") or []
        for j in rows:
            co = (j.get("company") or {}).get("code") or (j.get("company_info") or {}).get("code")
            if not (j.get("id") and j.get("name") and co):
                continue
            loc = (j.get("google_location") or {}).get("address_components") or {}
            pay = f"{j.get('salary_currency') or ''} {j.get('base_salary') or ''}–{j.get('maximum_salary') or ''}".strip(" –") \
                if j.get("salary_shown") and j.get("maximum_salary") else ""
            out.append({"id": f"klb:{j['id']}", "ats": "", "board": "kalibrr", "title": j["name"], "company": j.get("company_name", ""),
                        "location": ", ".join(filter(None, [loc.get("city"), loc.get("country"), "Remote" if j.get("is_work_from_home") else ""])),
                        "url": f"https://www.kalibrr.com/c/{co}/jobs/{j['id']}/{j.get('slug', '')}",
                        "text": plain(f"{j.get('description', '')} {j.get('qualifications', '')}"), "posted": iso_day(j.get("activation_date")),
                        "closes": (j.get("application_end_date") or "")[:10], "salary": pay, "via": NAME})
        if len(rows) < 50:
            break
    return out
