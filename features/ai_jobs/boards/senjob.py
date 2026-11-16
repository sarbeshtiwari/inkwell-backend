"""Senjob (senjob.com): jobs in Senegal and West Africa."""
import re

from features.ai_jobs.boards._cards import first
from features.ai_jobs.boards._util import get_text, title_matches, plain

NAME, KIND, REGIONS = "Senjob", "search", ["sn"]


def fetch(role):
    out = []
    for page in (1, 2, 3):
        html = get_text("https://senjob.com/offres-d-emploi.php" + (f"?page={page}" if page > 1 else ""))
        rows = re.split(r"<tr\b", html, flags=re.I)
        for row in rows:
            link = re.search(r'href="(https://senjob\.com/jobseekers/[^"]*?_e_(\d+)\.html)"[^>]*>(.*?)</a>', row, re.S)
            if not link:
                continue
            title = plain(link.group(3))
            if not title or not title_matches(title, role) or any(o["id"] == f"sj:{link.group(2)}" for o in out):
                continue
            out.append({"id": f"sj:{link.group(2)}", "ats": "", "board": "senjob", "title": title, "company": "",
                        "location": "Senegal", "url": link.group(1), "text": "",
                        "posted": first(r"display:\s*none;?\s*\"?>\s*(\d{4}-\d{2}-\d{2})\s*<", row), "via": NAME})
        if len(rows) < 5:
            break
    return out
