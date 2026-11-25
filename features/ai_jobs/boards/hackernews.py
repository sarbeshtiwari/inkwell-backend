"""Hacker News "Ask HN: Who is hiring?" (latest monthly thread, via the public HN Algolia API)."""
import re

from features.ai_jobs.boards._util import get_json, iso_day, title_matches, unescape

NAME, KIND, REGIONS = "Hacker News", "search", ["global"]

# Morrow's open_to_india (sites_bot/startup_jobs.py, MIT): keep a post when someone in India could take it.
REMOTE = re.compile(r"\b(?:remote(?:ly)?|wfh|work\s+from\s+home|distributed)\b", re.I)
ANYWHERE = re.compile(r"\b(?:anywhere|world\s?wide|global(?:ly)?|any\s+(?:country|location|time\s?zone))\b", re.I)
OPEN_TO_INDIA = re.compile(r"\b(?:india|indian|apac|asia|asia[-\s]pacific|ist)\b", re.I)
ELSEWHERE = re.compile(r"\b(?:(?-i:US|USA|U\.S\.?|UK|U\.K\.|EU|EMEA|LATAM|EST|PST|CET)|united states|america|north america"
                       r"|canada|united kingdom|europe|germany|france|spain|netherlands|australia|brazil|mexico)\b", re.I)
INDIAN_CITIES = re.compile(r"(?<![a-z])(?:bangalore|bengaluru|mumbai|pune|hyderabad|chennai|delhi|new delhi|gurgaon|gurugram|noida|"
                           r"kolkata|ahmedabad|kochi|jaipur|chandigarh)(?![a-z])", re.I)


def open_to_india(header):
    if INDIAN_CITIES.search(header):
        return True
    if re.search(r"\bindia\b", header, re.I) and not re.search(r"\bnot\s+(?:in\s+)?india\b", header, re.I):
        return True
    restricted = False
    for part in re.split(r"[|;\n•·]|\s[-–—]\s", header):
        if not REMOTE.search(part):
            continue
        if ANYWHERE.search(part) or OPEN_TO_INDIA.search(part):
            return True
        restricted = restricted or bool(ELSEWHERE.search(part))
    return bool(REMOTE.search(header)) and not restricted
API = "https://hn.algolia.com/api/v1"


def first_line(text):
    text = re.sub(r'<a\s[^>]*href="([^"]+)"[^>]*>.*?</a>', r"\1", text or "", flags=re.I | re.S)
    text = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</?(?:p|br|div|li)\b[^>]*>", "\n", text, flags=re.I)))
    return next((l.strip() for l in text.split("\n") if l.strip()), "")


def fetch(role, countries=()):
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
        if not title_matches(line, role) or ("in" in countries and not open_to_india(line)):
            continue
        out.append({"id": f"hn:{c['id']}", "ats": "", "board": "hackernews", "title": title[:140],
                    "company": parts[0][:80] if len(parts) >= 2 else "", "location": parts[2][:80] if len(parts) >= 3 else "",
                    "url": f"https://news.ycombinator.com/item?id={c['id']}", "text": "",
                    "posted": iso_day(c.get("created_at")), "via": NAME})
        if len(out) >= 100:
            break
    return out
