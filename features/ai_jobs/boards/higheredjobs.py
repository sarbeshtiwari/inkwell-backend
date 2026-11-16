"""HigherEdJobs: university and college jobs in the US, public category RSS feeds."""
import re

from features.ai_jobs.boards._feeds import rss_day, split_at
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches

NAME, KIND, REGIONS = "HigherEdJobs", "search", ["us"]
CATEGORIES = (68, 141, 3, 117)  # technology / IT, executive, administrative, faculty (computer science)


def fetch(role):
    out, seen = [], set()
    for cat in CATEGORIES:
        for it in rss_items(get_text(f"https://www.higheredjobs.com/rss/categoryFeed.cfm?catID={cat}")):
            title, company = split_at(it.get("title", ""))
            link = it.get("link") or ""
            if not link.startswith("https://") or link in seen or not title_matches(title, role):
                continue
            seen.add(link)
            desc = plain(it.get("description", ""))
            org = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", desc)
            out.append({"id": "hej:" + (link.split("JobCode=")[-1].split("&")[0] if "JobCode=" in link else link.rsplit("/", 1)[-1])[-40:],
                        "ats": "", "board": "higheredjobs", "title": title, "company": company or (org.group(1) if org else ""),
                        "location": org.group(2) if org else "", "url": link, "text": desc, "posted": rss_day(it.get("pubDate")), "via": NAME})
    return out[:100]
