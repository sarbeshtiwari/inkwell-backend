"""Jobspresso (curated remote jobs; public WP Job Manager RSS feed with keyword search)."""
from features.ai_jobs.boards._feeds import rss_day, slug_of
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches, unescape

NAME, KIND, REGIONS = "Jobspresso", "search", ["global"]


def fetch(role):
    out = []
    for it in rss_items(get_text("https://jobspresso.co/", params={"feed": "job_feed", "search_keywords": role})):
        title = unescape(it.get("title"))
        if not it.get("link") or not title_matches(title, role):
            continue
        out.append({"id": "jp:" + slug_of(it["link"]), "ats": "", "board": "jobspresso", "title": title,
                    "company": unescape(it.get("company")), "location": unescape(it.get("location")) or "Remote", "url": it["link"],
                    "text": plain(it.get("encoded") or it.get("description", "")), "posted": rss_day(it.get("pubDate")),
                    "salary": unescape(it.get("salary")), "via": NAME})
    return out[:100]
