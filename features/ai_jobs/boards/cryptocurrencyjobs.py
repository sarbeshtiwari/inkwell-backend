"""Cryptocurrency Jobs (web3 jobs, all remote; public RSS feed)."""
from features.ai_jobs.boards._feeds import rss_day, slug_of, split_at
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches, unescape

NAME, KIND, REGIONS = "Cryptocurrency Jobs", "search", ["global"]


def fetch(role):
    out = []
    for it in rss_items(get_text("https://cryptocurrencyjobs.co/index.xml")):
        title, company = split_at(it.get("title"))
        if not it.get("link") or not title_matches(title, role):
            continue
        out.append({"id": "ccj:" + slug_of(it["link"]), "ats": "", "board": "cryptocurrencyjobs", "title": title, "company": company,
                    "location": "Remote", "url": it["link"], "text": plain(unescape(it.get("description"))),
                    "posted": rss_day(it.get("pubDate")), "via": NAME})
    return out[:100]
