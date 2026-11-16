"""SchoolSpring: K-12 school district job boards (<district>.schoolspring.com), public API. Example: vansd.schoolspring.com"""
import re

from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "SchoolSpring", "company", ["us"]


def fetch(slug):
    host = (slug or "").strip("/").lower()
    host = host if host.endswith(".schoolspring.com") else f"{host}.schoolspring.com"
    if not re.fullmatch(r"[a-z0-9-]+\.schoolspring\.com", host) or host.split(".")[0] in ("www", "api"):
        return []
    out = []
    for page in range(1, 6):
        v = (get_json("https://api.schoolspring.com/api/Jobs/GetPagedJobsWithSearch",
                      params={"domainName": host, "keyword": "", "location": "", "category": "", "gradelevel": "", "jobtype": "", "organization": "",
                              "swLat": "", "swLon": "", "neLat": "", "neLon": "", "page": page, "size": 100, "sortDateAscending": "false"}) or {}).get("value") or {}
        rows = v.get("jobsList") or []
        for j in rows:
            if j.get("jobId") and j.get("title"):
                out.append({"id": f"ss:{j['jobId']}", "ats": "", "board": host, "title": j["title"], "company": j.get("employer", ""),
                            "location": j.get("location", ""), "url": f"https://{host}/?jobid={j['jobId']}", "text": "",
                            "posted": iso_day(j.get("displayDate")), "via": NAME})
        if len(rows) < 100:
            break
    return out
