"""Startup Jobs (startup.jobs public RSS feed, narrowed by role slug when one exists)."""
import re

from features.ai_jobs.boards._feeds import rss_day, slug_of, split_at
from features.ai_jobs.boards._util import get_text, rss_items, title_matches

NAME, KIND, REGIONS = "Startup Jobs", "search", ["global"]
FEED = "https://startup.jobs/feeds/jobs"


def fetch(role):
    slug = re.sub(r"[^a-z0-9]+", "-", (role or "").lower()).strip("-")
    items = rss_items(get_text(FEED, params={"role": slug})) if slug else []
    if not any(title_matches(i.get("title"), role) for i in items):
        items = rss_items(get_text(FEED))
    out, seen = [], set()
    for it in items:
        title, company = split_at(it.get("title"))
        url = (it.get("guid") or it.get("link") or "").split("?")[0]
        if not url or url in seen or not title_matches(title, role):
            continue
        seen.add(url)
        lines = [l.strip() for l in (it.get("description") or "").split("\n") if l.strip()]
        out.append({"id": "sj:" + slug_of(url), "ats": "", "board": "startup_jobs", "title": title, "company": company,
                    "location": lines[-1].split(" · ")[0] if len(lines) > 1 else "", "url": url,
                    "text": lines[0] if lines else "", "posted": rss_day(it.get("pubDate")), "via": NAME})
    return out[:100]
