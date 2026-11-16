"""We Work Remotely (public RSS feed of all remote jobs)."""
from features.ai_jobs.boards._feeds import rss_day
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches, unescape

NAME, KIND, REGIONS = "We Work Remotely", "search", ["global"]



def fetch(role):
    out = []
    for it in rss_items(get_text("https://weworkremotely.com/remote-jobs.rss")):
        company, _, title = unescape(it.get("title")).partition(": ")
        if not title:
            company, title = "", company
        if not it.get("link") or not title_matches(title, role):
            continue
        out.append({"id": "wwr:" + it["link"].rstrip("/").rsplit("/", 1)[-1], "ats": "", "board": "weworkremotely",
                    "title": title, "company": company, "location": "Remote" + (f" ({it['region']})" if it.get("region") else ""),
                    "url": it["link"], "text": plain(it.get("description", "")), "posted": rss_day(it.get("pubDate")), "via": NAME})
    return out[:100]
