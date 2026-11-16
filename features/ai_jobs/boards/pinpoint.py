"""Pinpoint company boards (<slug>.pinpointhq.com/postings.json). Example: sans"""
from features.ai_jobs.boards._util import get_json, plain

NAME, KIND, REGIONS = "Pinpoint", "company", ["global"]


def fetch(slug):
    d = get_json(f"https://{slug}.pinpointhq.com/postings.json", allow_redirects=False) or {}
    out = []
    for j in d.get("data") or []:
        title, url = (j.get("title") or "").strip(), (j.get("url") or "").strip()
        if not (title and url.startswith("https://")) or "youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw" in (j.get("description") or ""):
            continue
        loc = j.get("location") or {}
        where = loc.get("name") or ", ".join(filter(None, [loc.get("city"), loc.get("province")]))
        out.append({"id": f"pp:{slug}:{j.get('id')}", "ats": "", "board": slug, "title": title,
                    "company": slug.capitalize(), "location": where, "url": url,
                    "text": plain(j.get("description", "")), "posted": "", "via": NAME})
    return out
