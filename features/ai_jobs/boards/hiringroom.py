"""HiringRoom: Latin American companies' job sites (<company>.hiringroom.com). Example: growuphr"""
import re

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "HiringRoom", "company", ["global"]


def fetch(slug):
    sub = (re.search(r"([a-z0-9-]+)\.hiringroom\.com", slug or "") or [None, (slug or "").strip("/").lower()])[1]
    if not re.fullmatch(r"[a-z0-9-]+", sub):
        return []
    from features.ai_jobs import sitejobs
    origin = f"https://{sub}.hiringroom.com"
    for path in ("/portal/jobs", "/jobs"):
        html = get_text(origin + path)
        jobs = sitejobs.jobs_in(html, origin + path, f"{sub}.hiringroom.com")
        if jobs:
            return [{**j, "id": f"hr:{sub}:" + j["id"].split(":", 2)[-1], "company": j["company"] if j["company"] != f"{sub}.hiringroom.com" else sub.capitalize(),
                     "via": NAME} for j in jobs]
        ms = list(re.finditer(r'href="(/jobs/get_vacancy/[a-f0-9]+)"', html))
        out = []
        for k, m in enumerate(ms):
            block = html[m.start():ms[k + 1].start() if k + 1 < len(ms) else len(html)]
            title = plain((re.search(r"name__vacancy[^>]*>(.*?)</h4>", block, re.S) or [None, ""])[1])
            if title and not any(o["url"] == origin + m.group(1) for o in out):
                out.append({"id": f"hr:{sub}:{m.group(1).rsplit('/', 1)[-1]}", "ats": "", "board": sub, "title": title, "company": sub.capitalize(),
                            "location": plain((re.search(r"hr-Location-pin[^>]*></i>\s*([^<]+)", block) or [None, ""])[1]),
                            "url": origin + m.group(1), "text": "", "posted": "", "via": NAME})
        if out:
            return out
    return []
