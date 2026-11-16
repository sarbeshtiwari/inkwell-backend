"""Small extras for feed-based board readers: RSS dates and "Title at Company" splitting."""
import re
from email.utils import parsedate_to_datetime

from features.ai_jobs.boards._util import iso_day, unescape


def rss_day(value):
    """'Mon, 02 Nov 2026 07:31:23 +0000' (or ISO) -> '2026-11-02' ('' when unknown)."""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return iso_day(value)


def split_at(title):
    """'Backend Engineer at Acme' -> ('Backend Engineer', 'Acme'); no ' at ' -> (title, '')."""
    title = unescape(unescape(title))
    head, sep, tail = title.rpartition(" at ")
    return (head.strip(), tail.strip()) if sep and head.strip() else (title, "")


def slug_of(url):
    return re.sub(r"[?#].*$", "", url or "").rstrip("/").rsplit("/", 1)[-1]
