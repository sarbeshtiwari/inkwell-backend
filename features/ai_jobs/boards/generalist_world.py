"""Generalist World (generalist.world): generalist, operations and chief-of-staff roles."""
import re

from features.ai_jobs.boards._cards import first
from features.ai_jobs.boards._util import get_text, title_matches

NAME, KIND, REGIONS = "Generalist World", "search", ["global"]


def fetch(role):
    html = get_text("https://generalist.world/jobs/")
    out = []
    for attrs, body in re.findall(r'<a(\s(?:[^>]*?\s)?class="(?:[^"]*\s)?gw-job-card(?:\s[^"]*)?"[^>]*)>(.*?)</a>', html, re.S):
        href = first(r'\shref="([^"]*)"', attrs)
        title = first(r'class="(?:[^"]*\s)?gw-job-title(?:\s[^"]*)?"[^>]*>(.*?)</div>', body)
        href = "https://generalist.world" + href if href.startswith("/") else href
        if not href.startswith("https://") or not title or not title_matches(title, role):
            continue
        out.append({"id": "gw:" + re.sub(r"[^\w-]", "-", href.rstrip("/").rsplit("/", 1)[-1])[-60:], "ats": "", "board": "generalist", "title": title,
                    "company": first(r'class="(?:[^"]*\s)?gw-job-company(?:\s[^"]*)?"[^>]*>(.*?)</div>', body),
                    "location": first(r'class="(?:[^"]*\s)?gw-location(?:\s[^"]*)?"[^>]*>(.*?)</span>', body) or first(r'\sdata-region="([^"]*)"', attrs),
                    "url": href, "text": first(r'class="(?:[^"]*\s)?gw-job-description(?:\s[^"]*)?"[^>]*>(.*?)</p>', body), "posted": "", "via": NAME})
    return out
