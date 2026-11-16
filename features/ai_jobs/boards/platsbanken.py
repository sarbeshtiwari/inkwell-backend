"""Platsbanken (Arbetsförmedlingen, Sweden's public employment service) via the open JobTech JobSearch API."""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Platsbanken", "search", ["se"]


def fetch(role):
    out = []
    for offset in range(0, 300, 100):
        d = get_json("https://jobsearch.api.jobtechdev.se/search", params={"q": role, "limit": 100, "offset": offset}) or {}
        hits = d.get("hits") or []
        for j in hits:
            if not j.get("id") or not j.get("headline"):
                continue
            addr = j.get("workplace_address") or {}
            pay = j.get("salary_description") or ""
            out.append({"id": f"pb:{j['id']}", "ats": "", "board": "platsbanken", "title": j["headline"],
                        "company": (j.get("employer") or {}).get("name") or "", "location": ", ".join(filter(None, [addr.get("municipality"), addr.get("country")])),
                        "url": j.get("webpage_url") or f"https://arbetsformedlingen.se/platsbanken/annonser/{j['id']}",
                        "text": ((j.get("description") or {}).get("text") or "")[:20000], "posted": iso_day(j.get("publication_date")),
                        "closes": (j.get("application_deadline") or "")[:10], "salary": pay, "via": NAME})
        if len(hits) < 100:
            break
    return out
