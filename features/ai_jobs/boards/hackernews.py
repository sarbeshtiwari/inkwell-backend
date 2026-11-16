"""Hacker News "Ask HN: Who is hiring?" (latest monthly thread, via the public HN Algolia API)."""
import re

from features.ai_jobs.boards._util import get_json, iso_day, title_matches, unescape

NAME, KIND, REGIONS = "Hacker News", "search", ["global"]
API = "https://hn.algolia.com/api/v1"


def first_line(text):
    text = re.sub(r'<a\s[^>]*href="([^"]+)"[^>]*>.*?</a>', r"\1", text or "", flags=re.I | re.S)
    text = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</?(?:p|br|div|li)\b[^>]*>", "\n", text, flags=re.I)))
    return next((l.strip() for l in text.split("\n") if l.strip()), "")


def fetch(role):
    d = get_json(f"{API}/search_by_date", params={"tags": "story,author_whoishiring", "hitsPerPage": 5}) or {}
    tid = next((h["objectID"] for h in d.get("hits", []) if re.search(r"who\s+is\s+hiring", h.get("title") or "", re.I)), None)
    item = get_json(f"{API}/items/{tid}") if tid else None
    out = []
    for c in (item or {}).get("children") or []:
        line = first_line(c.get("text"))
        if not line or not c.get("id"):
            continue
        parts = [re.sub(r"https?://\S+", "", p).strip() for p in line.split("|")]
        title = parts[1] if len(parts) >= 2 and parts[1] else parts[0]
        if not title_matches(line, role):
            continue
        out.append({"id": f"hn:{c['id']}", "ats": "", "board": "hackernews", "title": title[:140],
                    "company": parts[0][:80] if len(parts) >= 2 else "", "location": parts[2][:80] if len(parts) >= 3 else "",
                    "url": f"https://news.ycombinator.com/item?id={c['id']}", "text": "",
                    "posted": iso_day(c.get("created_at")), "via": NAME})
        if len(out) >= 100:
            break
    return out
