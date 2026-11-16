"""Gupy job portal (portal.gupy.io), searched by role across Brazilian employers. Example: desenvolvedor"""
import re

from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Gupy", "search", ["br"]
WORKPLACE = {"remote": "Remoto", "hybrid": "Híbrido", "on-site": "Presencial"}


def fetch(role):
    out, seen = [], set()
    for page in range(5):
        d = get_json("https://portal.gupy.io/api/job-search/jobs",
                     params={"jobName": role or "", "limit": 100, "offset": page * 100}) or {}
        rows = d.get("data") if isinstance(d, dict) else None
        for j in rows or []:
            url, company = (j.get("jobUrl") or "").strip(), (j.get("careerPageName") or "").strip()
            if (j.get("isConfidentialCareerPage") or not j.get("name") or j.get("id") in seen
                    or not re.match(r"https://[a-z0-9.-]*gupy\.io/", url) or not company
                    or re.fullmatch(r"(empresa\s+)?confidencial", company, re.I)):
                continue
            seen.add(j.get("id"))
            where = [WORKPLACE.get(j.get("workplaceType"), j.get("workplaceType") or ("Remoto" if j.get("isRemoteWork") else ""))]
            out.append({"id": f"gupy:{j['id']}", "ats": "", "board": "gupy", "title": j["name"].strip(), "company": company,
                        "location": ", ".join(filter(None, where + [j.get(k) for k in ("city", "state", "country")])),
                        "url": url, "text": plain(j.get("description", "")), "posted": iso_day(j.get("publishedDate")), "via": NAME})
        if not rows or len(rows) < 100:
            break
    return out
