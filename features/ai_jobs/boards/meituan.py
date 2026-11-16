"""Meituan's own career site (zhaopin.meituan.com experienced-hire job list)."""
from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Meituan Careers", "search", ["cn"]
HOST = "https://zhaopin.meituan.com"


def _names(arr):
    return "/".join(filter(None, (x if isinstance(x, str) else (x or {}).get("name") for x in arr or [])))


def fetch(role):
    body = {"page": {"pageNo": 1, "pageSize": 100}, "keywords": role, "jobShareType": "1",
            "jobType": [{"code": "3", "subCode": []}], "cityList": [], "department": [], "jfJgList": [], "typeCode": [], "specialCode": []}
    d = get_json(f"{HOST}/api/official/job/getJobList", method="POST", json=body) or {}
    jobs = []
    for p in (d.get("data") or {}).get("list") or []:
        if not p.get("name") or not p.get("jobUnionId"):
            continue
        ts = p.get("refreshTime") or p.get("firstPostTime") or ""
        jobs.append({"id": f"mt:{p['jobUnionId']}", "ats": "", "board": "meituan", "title": p["name"], "company": "Meituan",
                     "location": _names(p.get("cityList")), "url": f"{HOST}/web/position/detail?jobUnionId={p['jobUnionId']}",
                     "text": plain(f"{p.get('jobDuty') or ''} {p.get('jobRequirement') or ''}"),
                     "posted": iso_day(int(ts) // 1000 if isinstance(ts, (int, float)) and ts > 1e12 else ts), "via": NAME})
    return jobs
