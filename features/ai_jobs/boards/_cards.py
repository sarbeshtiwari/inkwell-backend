"""Reading job cards out of a board's listing HTML (boards with no data feed)."""
import re

from features.ai_jobs.boards._util import plain


def cards(html, start):
    """[(match, card html)]: each card runs from one `start` match to the next."""
    ms = list(re.finditer(start, html or "", re.I))
    return [(m, html[m.start():(ms[i + 1].start() if i + 1 < len(ms) else m.start() + 8000)]) for i, m in enumerate(ms)]


def first(pattern, text, default=""):
    m = re.search(pattern, text or "", re.I | re.S)
    return plain(m.group(1)) if m else default
