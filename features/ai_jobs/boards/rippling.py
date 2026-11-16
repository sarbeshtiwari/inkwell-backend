"""Rippling company boards (ats.rippling.com/<slug>/jobs). Example: rippling"""
from features.ai_jobs.boards._util import get_json

NAME, KIND, REGIONS = "Rippling", "company", ["global"]


def fetch(slug):
    jobs = {}  # the API lists a posting once per location
    for page in range(5):
        d = get_json(f"https://ats.rippling.com/api/v2/board/{slug}/jobs", params={"page": page, "pageSize": 100}) or {}
        for j in d.get("items") or []:
            title, url = (j.get("name") or "").strip(), j.get("url") or ""
            if not (title and url.startswith("https://") and j.get("id")):
                continue
            job = jobs.setdefault(j["id"], {"id": f"rip:{slug}:{j['id']}", "ats": "", "board": slug, "title": title,
                                            "company": slug.capitalize(), "location": "", "url": url, "text": "",
                                            "posted": "", "via": NAME})
            places = [p for p in job["location"].split("; ") if p]
            places += [l.get("name") for l in j.get("locations") or [] if l.get("name") and l.get("name") not in places]
            job["location"] = "; ".join(places)
        if page + 1 >= (d.get("totalPages") or 0):
            break
    return list(jobs.values())
