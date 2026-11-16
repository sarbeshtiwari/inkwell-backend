"""The Hub (thehub.io): Nordic startup jobs, public API."""
from features.ai_jobs.boards._util import get_json

NAME, KIND, REGIONS = "The Hub", "search", ["dk", "se", "no", "fi"]


def fetch(role):
    out = []
    for page in range(1, 6):
        d = (get_json("https://thehub.io/api/v2/jobsandfeatured", params={"search": role, "page": page}) or {}).get("jobs") or {}
        for j in d.get("docs") or []:
            if not j.get("id") or not j.get("title"):
                continue
            loc = j.get("location") or {}
            out.append({"id": f"hub:{j['id']}", "ats": "", "board": "thehub", "title": j["title"], "company": (j.get("company") or {}).get("name", ""),
                        "location": ", ".join(filter(None, [loc.get("locality") if isinstance(loc, dict) else "", "Remote" if j.get("isRemote") else ""])),
                        "url": f"https://thehub.io/jobs/{j['id']}", "text": "", "posted": (j.get("publishedAt") or j.get("createdAt") or "")[:10], "via": NAME})
        if page >= (d.get("pages") or 1):
            break
    return out[:100]
