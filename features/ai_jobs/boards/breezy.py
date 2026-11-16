"""Breezy HR company boards (<slug>.breezy.hr/json). Example: zeno-power"""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Breezy HR", "company", ["global"]


def fetch(slug):
    d = get_json(f"https://{slug}.breezy.hr/json", allow_redirects=False)
    out = []
    for j in d if isinstance(d, list) else []:
        if not (j.get("name") and str(j.get("url", "")).startswith("https://")):
            continue
        loc = j.get("location") or {}
        where = loc.get("name") or ", ".join(filter(None, [loc.get("city"), loc.get("state"), (loc.get("country") or {}).get("name")]))
        if loc.get("is_remote") and "remote" not in where.lower():
            where = ", ".join(filter(None, [where, "Remote"]))
        out.append({"id": f"bz:{slug}:{j.get('id') or j['url'].rsplit('/p/', 1)[-1]}", "ats": "", "board": slug,
                    "title": j["name"], "company": (j.get("company") or {}).get("name") or slug.capitalize(),
                    "location": where, "url": j["url"], "text": "", "posted": iso_day(j.get("published_date")), "via": NAME})
    return out
