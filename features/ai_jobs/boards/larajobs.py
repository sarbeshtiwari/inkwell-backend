"""LaraJobs (Laravel / PHP jobs; public RSS feed)."""
from features.ai_jobs.boards._feeds import rss_day, slug_of
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches, unescape, words

NAME, KIND, REGIONS = "LaraJobs", "search", ["global"]


def fetch(role):
    stack = {"php", "laravel", "vue", "vuejs", "livewire"}
    out = []
    for it in rss_items(get_text("https://larajobs.com/feed")):
        title = unescape(it.get("title"))
        if not it.get("link") or not (title_matches(title, role) or stack & set(words(role))):
            continue
        out.append({"id": "lj:" + slug_of(it["link"]), "ats": "", "board": "larajobs", "title": title,
                    "company": unescape(it.get("company") or it.get("creator")), "location": unescape(it.get("location")),
                    "url": it["link"], "text": plain(it.get("encoded") or it.get("description", "")) or unescape(it.get("tags")),
                    "posted": rss_day(it.get("pubDate")), "salary": unescape(it.get("salary")), "via": NAME})
    return out[:100]
