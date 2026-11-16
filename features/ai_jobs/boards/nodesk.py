"""NoDesk (remote jobs; public RSS feed)."""
from features.ai_jobs.boards._feeds import rss_day, slug_of, split_at
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches

NAME, KIND, REGIONS = "NoDesk", "search", ["global"]


def fetch(role):
    out = []
    for it in rss_items(get_text("https://nodesk.co/remote-jobs/index.xml")):
        title, company = split_at(it.get("title"))
        if not it.get("link") or not title_matches(title, role):
            continue
        out.append({"id": "nd:" + slug_of(it["link"]), "ats": "", "board": "nodesk", "title": title, "company": company,
                    "location": "Remote", "url": it["link"], "text": plain(it.get("description", "")),
                    "posted": rss_day(it.get("pubDate")), "via": NAME})
    return out[:100]
