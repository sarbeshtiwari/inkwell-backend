"""Garena's own career site (careers.garena.com job list, the whole board in one call)."""
from features.ai_jobs.boards._util import get_json, plain, title_matches

NAME, KIND, REGIONS = "Garena Careers", "search", ["sg", "id", "vn", "th", "tw", "ph", "my", "br"]


def fetch(role):
    d = get_json("https://careers.garena.com/api/job/list", method="POST", params={"office": "global"}, json={}) or {}
    jobs = []
    for j in d.get("jobs") or []:
        title, jid = (j.get("title") or "").strip(), str(j.get("id") or "").strip()
        if not title or not jid or not title_matches(title, role):
            continue
        locs = [l for l in (j.get("tags") or {}).get("location") or [] if isinstance(l, str) and l.strip()]
        jobs.append({"id": f"garena:{jid}", "ats": "", "board": "garena", "title": title, "company": "Garena",
                     "location": ", ".join(locs), "url": f"https://careers.garena.com/global/careers/{jid}",
                     "text": plain(j.get("description", "")), "posted": "", "via": NAME})
    return jobs[:100]
