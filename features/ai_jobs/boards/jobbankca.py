"""Job Bank (Government of Canada), public Atom search feed."""
import re

from features.ai_jobs.boards._util import get_text, plain
from features.ai_jobs.boards._feeds import rss_day

NAME, KIND, REGIONS = "Job Bank Canada", "search", ["ca"]


def fetch(role):
    xml = get_text("https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed", params={"searchstring": role, "sort": "D"})
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml or "", re.S):
        def tag(t):
            m = re.search(rf"<{t}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", entry, re.S)
            return m.group(1).strip() if m else ""
        link = (re.search(r'<link[^>]+href="([^"]+)"', entry) or [None, ""])[1].split(";jsessionid")[0]
        title = plain(tag("title"))
        jid = re.search(r"/jobposting/(\d+)", link)
        if not title or not jid:
            continue
        summary = plain(tag("summary") or tag("content"))
        company = (re.search(r"Employer:\s*([^\n]+?)\s*(Location|Salary|$)", summary) or [None, ""])[1]
        where = (re.search(r"Location:\s*([^\n]+?)\s*(Salary|Employer|$)", summary) or [None, ""])[1]
        pay = (re.search(r"Salary:\s*([^\n]+?)\s*(Location|Employer|$)", summary) or [None, ""])[1]
        out.append({"id": f"jbc:{jid.group(1)}", "ats": "", "board": "jobbankca", "title": title, "company": company.strip(),
                    "location": where.strip() or "Canada", "url": link, "text": summary, "posted": rss_day(tag("updated")),
                    "salary": pay.strip(), "via": NAME})
    return out[:100]
