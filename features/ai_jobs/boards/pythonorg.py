"""Python.org Jobs (public RSS feed of Python job postings)."""
from features.ai_jobs.boards._feeds import rss_day, slug_of
from features.ai_jobs.boards._util import get_text, plain, rss_items, title_matches, unescape

NAME, KIND, REGIONS = "Python.org Jobs", "search", ["global"]


def fetch(role):
    out = []
    for it in rss_items(get_text("https://www.python.org/jobs/feed/rss/")):
        title, _, company = unescape(it.get("title")).rpartition(", ")
        title = title or company
        if not it.get("link") or not (title_matches(title, role) or "python" in (role or "").lower()):
            continue
        desc = it.get("description") or ""
        out.append({"id": "py:" + slug_of(it["link"]), "ats": "", "board": "pythonorg", "title": title,
                    "company": company if title != company else "", "location": desc.split("\n", 1)[0].strip()[:100],
                    "url": it["link"], "text": plain(desc), "posted": rss_day(it.get("pubDate")), "via": NAME})
    return out[:100]
