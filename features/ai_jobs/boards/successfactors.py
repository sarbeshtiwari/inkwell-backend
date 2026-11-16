"""SAP SuccessFactors career sites (Recruiting Marketing); slug is the board host, optionally with a path, e.g.
"jobs.bmwgroup.com" or "acme.successfactors.eu/Brand". Example: jobs.bmwgroup.com"""
import re
from datetime import datetime

from features.ai_jobs.boards._util import get_json, get_text, plain, unescape

NAME, KIND, REGIONS = "SuccessFactors", "company", ["global"]
TILE = re.compile(r'<li class="job-tile job-id-(\d+)\b.*?</li>', re.S)


def _job(slug, jid, title, url, where, day=""):
    company = re.sub(r"^(jobs|careers|career)\.", "", slug.split("/")[0]).split(".")[0].capitalize()
    return {"id": f"sf:{slug}:{jid}", "ats": "", "board": slug, "title": title, "company": company,
            "location": where, "url": url, "text": "", "posted": day, "via": NAME}


def _day(value):  # en_US dates: "11/2/26"
    try:
        return datetime.strptime(value or "", "%m/%d/%y").date().isoformat()
    except ValueError:
        return ""


def _api(base, slug):
    """Newer boards: JSON search API, 10 postings a page."""
    out = []
    for page in range(30):
        d = get_json(f"{base}/services/recruiting/v1/jobs", method="POST", allow_redirects=False,
                     json={"keywords": "", "locale": "en_US", "location": "", "pageNumber": page, "sortBy": "date"}) or {}
        rows = [(i or {}).get("response") or {} for i in d.get("jobSearchResult") or []]
        for r in rows:
            title = plain(r.get("unifiedStandardTitle") or r.get("jobTitle") or "")
            if not r.get("id") or not title:
                continue
            where = " / ".join(dict.fromkeys(filter(None, (plain(x).rstrip(", ") for x in r.get("jobLocationShort") or []))))
            url_title = re.sub(r"[?#&]+", "-", unescape(r.get("unifiedUrlTitle") or r.get("urlTitle") or "job"))
            out.append(_job(slug, r["id"], title, f"{base}/job/{url_title}/{r['id']}-en_US", where, _day(r.get("unifiedStandardStart"))))
        if len(rows) < 10 or len(out) >= (d.get("totalJobs") or 0):
            break
    return out


def _tiles(base, slug):
    """Classic boards: HTML job tiles, paged by start row."""
    origin = re.match(r"https://[^/]+", base).group(0)
    out, seen = [], set()
    for _ in range(10):
        tiles = TILE.finditer(get_text(f"{base}/tile-search-results/?startrow={len(seen)}", allow_redirects=False))
        fresh = 0
        for t in tiles:
            block, jid = t.group(0), t.group(1)
            path = re.search(r'data-url="([^"]+)"', block)
            title = re.search(r'class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', block, re.S)
            if jid in seen or not path or not title:
                continue
            seen.add(jid)
            fresh += 1
            city = re.search(r'id="[^"]*-section-city-value">(.*?)</div>', block, re.S)
            href = unescape(path.group(1))
            out.append(_job(slug, jid, plain(title.group(1)), href if href.startswith("http") else origin + href,
                            plain(city.group(1)) if city else ""))
        if not fresh:
            break
    return out


def fetch(slug):
    slug = re.sub(r"^https?://", "", (slug or "").strip()).rstrip("/")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(/[A-Za-z0-9_-]+)*", slug or "", re.I):
        return []
    base = f"https://{slug}"
    return _api(base, slug) or _tiles(base, slug)
