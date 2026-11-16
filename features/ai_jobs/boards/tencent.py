"""Tencent's own career site (careers.tencent.com post query API)."""
import re
import time

from features.ai_jobs.boards._util import get_json, plain

NAME, KIND, REGIONS = "Tencent Careers", "search", ["cn"]


def _day(s):
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s or "")
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else ""


def fetch(role):
    d = get_json("https://careers.tencent.com/tencentcareer/api/post/Query",
                 params={"timestamp": int(time.time() * 1000), "keyword": role, "pageIndex": 1, "pageSize": 100, "language": "zh-cn"}) or {}
    return [{"id": f"tc:{p.get('PostId')}", "ats": "", "board": "tencent", "title": p["RecruitPostName"], "company": "Tencent",
             "location": "-".join(filter(None, [p.get("CountryName"), p.get("LocationName")])),
             "url": p.get("PostURL") or f"https://careers.tencent.com/jobdesc.html?postId={p['PostId']}",
             "text": plain(p.get("Responsibility", "")), "posted": _day(p.get("LastUpdateTime")), "via": NAME}
            for p in ((d.get("Data") or {}).get("Posts") or []) if p.get("RecruitPostName") and (p.get("PostURL") or p.get("PostId"))]
