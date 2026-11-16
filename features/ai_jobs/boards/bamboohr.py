"""BambooHR company boards (<slug>.bamboohr.com/careers). Example: hubstaff"""
from features.ai_jobs.boards._util import get_json

NAME, KIND, REGIONS = "BambooHR", "company", ["global"]


def fetch(slug):
    origin = f"https://{slug}.bamboohr.com"
    d = get_json(f"{origin}/careers/list", allow_redirects=False) or {}
    out = []
    for j in d.get("result") or []:
        if not (j.get("jobOpeningName") and j.get("id")):
            continue
        loc = j.get("location") or {}
        where = ", ".join(dict.fromkeys(filter(None, [loc.get("city"), loc.get("state"), "Remote" if j.get("isRemote") else ""])))
        out.append({"id": f"bhr:{slug}:{j['id']}", "ats": "", "board": slug, "title": j["jobOpeningName"],
                    "company": slug.capitalize(), "location": where, "url": f"{origin}/careers/{j['id']}",
                    "text": "", "posted": "", "via": NAME})
    return out
