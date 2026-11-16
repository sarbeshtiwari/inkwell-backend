"""Working Nomads (public remote jobs API)."""
from features.ai_jobs.boards._util import get_json, iso_day, plain, title_matches

NAME, KIND, REGIONS = "Working Nomads", "search", ["global"]


def fetch(role):
    d = get_json("https://www.workingnomads.com/api/exposed_jobs/")
    out = []
    for j in d if isinstance(d, list) else []:
        if not j.get("url") or not j.get("title") or not title_matches(j["title"], role):
            continue
        out.append({"id": "wn:" + j["url"].rstrip("/").rsplit("/", 1)[-1], "ats": "", "board": "workingnomads", "title": j["title"],
                    "company": j.get("company_name", ""), "location": j.get("location", "") or "Remote", "url": j["url"],
                    "text": plain(j.get("description", "")), "posted": iso_day(j.get("pub_date")), "via": NAME})
    return out[:100]
