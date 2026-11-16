"""Bulldogjob (bulldogjob.pl): IT jobs in Poland. Reads the job list the listing pages carry as data."""
import json
import re

from features.ai_jobs.boards._util import get_text, title_matches

NAME, KIND, REGIONS = "Bulldogjob", "search", ["pl"]


def fetch(role):
    out = []
    for page in range(1, 9):
        html = get_text("https://bulldogjob.pl/companies/jobs" + (f"/s/page,{page}" if page > 1 else ""))
        m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        try:
            rows = (json.loads(m.group(1))["props"]["pageProps"].get("jobs") or []) if m else []
        except (ValueError, KeyError, TypeError):
            rows = []
        for j in rows:
            if not (j.get("id") and j.get("position")) or not title_matches(j["position"], role):
                continue
            ext = j.get("redirectTo") if str(j.get("redirectTo") or "").startswith("https://") else ""
            out.append({"id": f"bdj:{j['id']}", "ats": "", "board": "bulldogjob", "title": j["position"],
                        "company": (j.get("company") or {}).get("name", ""),
                        "location": ", ".join(filter(None, ["Remote" if j.get("remote") else "", j.get("city") or ""])) or "Poland",
                        "url": ext or f"https://bulldogjob.pl/companies/jobs/{j['id']}", "text": "", "posted": "", "via": NAME})
        if len(rows) < 50:
            break
    return out[:100]
