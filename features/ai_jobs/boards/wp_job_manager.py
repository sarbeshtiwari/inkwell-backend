"""WordPress sites running the WP Job Manager plugin: their public job feed (<site>/?feed=job_feed).
Board address: the site's host. Example: jobspresso.co"""
import re

from features.ai_jobs.boards._feeds import rss_day, slug_of
from features.ai_jobs.boards._util import get_text, plain, rss_items, unescape

NAME, KIND, REGIONS = "WP Job Manager", "company", ["global"]


def fetch(slug):
    host = re.sub(r"^https?://", "", (slug or "").strip()).split("/")[0].lower()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
        return []
    out = []
    for it in rss_items(get_text(f"https://{host}/", params={"feed": "job_feed"})):
        title = unescape(it.get("title"))
        if not it.get("link") or not title:
            continue
        out.append({"id": f"wpjm:{host}:{slug_of(it['link'])[-60:]}", "ats": "", "board": host, "title": title,
                    "company": unescape(it.get("company")) or host.split(".")[-2].capitalize(), "location": unescape(it.get("location")),
                    "url": it["link"], "text": plain(it.get("encoded") or it.get("description", "")), "posted": rss_day(it.get("pubDate")),
                    "salary": unescape(it.get("salary")), "via": NAME})
    return out
