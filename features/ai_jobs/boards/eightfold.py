"""Eightfold AI company career sites (<tenant>.eightfold.ai/careers), public jobs API. Example: netflix.eightfold.ai"""
import re
from datetime import datetime

from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Eightfold", "company", ["global"]


def fetch(slug):
    host = (slug or "").strip("/").lower()
    host = host if "." in host else f"{host}.eightfold.ai"
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        return []
    out = []
    for start in range(0, 500, 10):  # the API serves 10 per call
        d = get_json(f"https://{host}/api/apply/v2/jobs", params={"start": start, "num": 10}) or {}
        rows = d.get("positions") or []
        for j in rows:
            if not j.get("id") or not j.get("name"):
                continue
            ts = j.get("t_create") or j.get("t_update")
            out.append({"id": f"ef:{host.split('.')[0]}:{j['id']}", "ats": "", "board": host, "title": j.get("posting_name") or j["name"],
                        "company": (d.get("domain") or host).split(".")[0].capitalize(), "location": j.get("location", ""),
                        "url": j.get("canonicalPositionUrl") or f"https://{host}/careers/job/{j['id']}", "text": j.get("job_description") or "",
                        "posted": iso_day(datetime.fromtimestamp(int(ts)).isoformat()) if ts else "", "via": NAME})
        if len(rows) < 10 or start + 10 >= (d.get("count") or 0) or len(out) >= 200:
            break
    return out
