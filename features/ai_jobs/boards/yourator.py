"""Yourator (yourator.co): startup and tech jobs in Taiwan, public API."""
from features.ai_jobs.boards._util import get_json

NAME, KIND, REGIONS = "Yourator", "search", ["tw"]


def fetch(role):
    out = []
    for page in (1, 2, 3):
        p = (get_json("https://www.yourator.co/api/v4/jobs", params={"term[]": role, "page": page}) or {}).get("payload") or {}
        for j in p.get("jobs") or []:
            if not j.get("id") or not j.get("name") or not j.get("path"):
                continue
            out.append({"id": f"yr:{j['id']}", "ats": "", "board": "yourator", "title": j["name"], "company": (j.get("company") or {}).get("brand", ""),
                        "location": j.get("location") or "Taiwan", "url": "https://www.yourator.co" + j["path"], "text": " · ".join(j.get("tags") or []),
                        "posted": "", "salary": j.get("salary", ""), "via": NAME})
        if not p.get("hasMore"):
            break
    return out
