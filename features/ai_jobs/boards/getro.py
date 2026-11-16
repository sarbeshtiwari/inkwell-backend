"""Getro job boards (VC portfolio / network boards); slug is the numeric Getro collection id. Example: 4283"""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Getro", "company", ["global"]


def fetch(slug):
    if not str(slug).isdigit():
        return []
    out = []
    for page in range(5):  # 20 per page, newest first
        body = {"hitsPerPage": 20, "page": page, "filters": {"page": page}, "query": ""}
        res = (get_json(f"https://api.getro.com/api/v2/collections/{slug}/search/jobs", method="POST", json=body) or {}).get("results") or {}
        jobs = res.get("jobs") or []
        for j in jobs:
            if not (j.get("url") and j.get("title")):
                continue
            locs = j.get("locations") or j.get("searchable_locations") or []
            if j.get("work_mode") == "remote" and not any("remote" in l.lower() for l in locs):
                locs = [*locs, "Remote"]
            org = j.get("organization") or {}
            lo, hi = j.get("compensation_amount_min_cents"), j.get("compensation_amount_max_cents")
            pay = f"{(lo or hi) // 100}-{(hi or lo) // 100} {j.get('compensation_currency') or ''}".strip() if (lo or hi) else ""
            out.append({"id": f"getro:{slug}:{j.get('id') or j['url']}", "ats": "", "board": str(slug), "title": j["title"],
                        "company": org.get("name") or "", "location": ", ".join(locs), "url": j["url"], "text": "",
                        "posted": iso_day(j.get("created_at")), "salary": pay, "via": NAME})
        if len(jobs) < 20 or (page + 1) * 20 >= (res.get("count") or 0):
            break
    return out
