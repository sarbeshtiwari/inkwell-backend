"""Rheinmetall's own career site (server-rendered vacancy list, searched by term)."""
import re

from features.ai_jobs.boards._util import get_text, plain, title_matches

NAME, KIND, REGIONS = "Rheinmetall Careers", "search", ["de"]
ORIGIN = "https://www.rheinmetall.com"


def _cards(html):
    for block in html.split('<div class="flex gap-0.5 group">')[1:]:
        link = re.search(r'href="(/[a-z]{2}/job/[^"]+/(\d+))"', block)
        title = re.search(r'md:text-xl[^"]*">(.*?)</div>', block, re.S)
        if link and title:
            org = plain((re.search(r'class="flex flex-wrap mr-6">(.*?)</div>', block, re.S) or [None, ""])[1])
            yield link[2], plain(title.group(1)), ORIGIN + link[1], org


def _search(term, jobs):
    for page in (1, 2, 3):
        fresh = 0
        for jid, title, url, org in _cards(get_text(f"{ORIGIN}/en/career/vacancies", params={"term": term, "page": page})):
            if jid not in jobs:
                company, _, city = org.rpartition("|")
                jobs[jid] = {"id": f"rhm:{jid}", "ats": "", "board": "rheinmetall", "title": title,
                             "company": company.strip() or "Rheinmetall", "location": city.strip(), "url": url,
                             "text": "", "posted": "", "via": NAME}
                fresh += 1
        if fresh < 10:
            break


def fetch(role):
    jobs = {}
    _search(role, jobs)
    return [j for j in jobs.values() if title_matches(j["title"], role)]
