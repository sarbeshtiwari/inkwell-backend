"""Gem company boards (jobs.gem.com/<slug>, read via Gem's public Job Board API). Example: retool"""
from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Gem", "company", ["global"]


def fetch(slug):
    d = get_json(f"https://api.gem.com/job_board/v0/{slug}/job_posts/", allow_redirects=False)
    out = []
    for j in d if isinstance(d, list) else []:
        title, url = (j.get("title") or "").strip(), j.get("absolute_url") or ""
        if not (title and url.startswith("https://")):
            continue
        where = (j.get("location") or {}).get("name") or ""
        if j.get("location_type") == "remote" and "remote" not in where.lower():
            where = ", ".join(filter(None, [where, "Remote"]))
        out.append({"id": f"gem:{slug}:{j.get('id')}", "ats": "", "board": slug, "title": title,
                    "company": slug.capitalize(), "location": where, "url": url,
                    "text": j.get("content_plain") or plain(j.get("content", "")),
                    "posted": iso_day(j.get("first_published_at")), "via": NAME})
    return out
