"""Google's own career site (careers results page, server-rendered cards)."""
import re

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "Google Careers", "search", ["global"]
BASE = "https://www.google.com/about/careers/applications/jobs/results/"
ANCHOR = re.compile(r'<a\b[^>]*?\bhref="(?:[^"]*/)?jobs/results/(\d+)-([a-z0-9-]+)[^"]*"[^>]*>')
H3 = re.compile(r"<h3\b[^>]*>([^<]+)</h3>")
EMPLOYER_LINE = re.compile(r'<p class="l103df">(.*?)</p>', re.S)


def fetch(role):
    html, jobs, prev = get_text(BASE, params={"q": role, "sort_by": "date"}), {}, 0
    for a in ANCHOR.finditer(html):
        jid, slug = a.group(1), a.group(2)
        card, prev = html[prev:a.start()], a.end()
        h3s, lines = H3.findall(card), EMPLOYER_LINE.findall(card)
        employer, locs = "Google", []
        if lines and "|" in lines[-1]:
            who, where = lines[-1].split("|", 1)
            employer = plain(who) or "Google"
            locs = [l.strip() for l in plain(where).split(";") if l.strip() and not re.match(r"^\+\d+ more$", l.strip())]
        title = plain(h3s[-1]) if h3s else slug.replace("-", " ").title()
        jobs.setdefault(jid, {"id": f"goog:{jid}", "ats": "", "board": "google", "title": title, "company": employer,
                              "location": "; ".join(locs), "url": f"{BASE}{jid}-{slug}", "text": "", "posted": "", "via": NAME})
    return list(jobs.values())[:100]
