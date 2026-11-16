"""The Muse (public jobs API; no keyword search, so a matching category is asked for and titles are filtered)."""
import re

from features.ai_jobs.boards._util import get_json, iso_day, plain, title_matches

NAME, KIND, REGIONS = "The Muse", "search", ["global", "us"]
CATEGORIES = [("data|machine learning|\\bml\\b|\\bai\\b|analyst", "Data and Analytics"), ("design|ux|ui\\b", "Design and UX"),
              ("product manager|product owner", "Product Management"), ("market", "Marketing"), ("sales|account exec", "Sales"),
              ("engineer|developer|software|devops|stack|programmer", "Software Engineering")]


def fetch(role):
    cat = next((c for pat, c in CATEGORIES if re.search(pat, (role or "").lower())), None)
    out = []
    for page in range(3):
        d = get_json("https://www.themuse.com/api/public/jobs", params={"page": page, **({"category": cat} if cat else {})}) or {}
        for j in d.get("results") or []:
            url = (j.get("refs") or {}).get("landing_page", "")
            if not j.get("id") or not url or not title_matches(j.get("name"), role):
                continue
            out.append({"id": f"tm:{j['id']}", "ats": "", "board": "themuse", "title": j.get("name", ""),
                        "company": (j.get("company") or {}).get("name", ""),
                        "location": ", ".join(l.get("name", "") for l in j.get("locations") or [])[:120], "url": url,
                        "text": plain(j.get("contents", "")), "posted": iso_day(j.get("publication_date")), "via": NAME})
        if not d.get("results"):
            break
    return out[:100]
