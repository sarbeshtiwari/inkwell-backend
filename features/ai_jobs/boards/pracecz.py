"""Prace.cz: jobs in the Czech Republic, from the result list its search page streams to the browser."""
from features.ai_jobs.boards._flight import flight_value
from features.ai_jobs.boards._util import get_text, iso_day

NAME, KIND, REGIONS = "Prace.cz", "search", ["cz"]


def items(html):
    """The page's currentPageItems list, out of its streamed Next.js data."""
    rows = flight_value(html, "currentPageItems")
    return rows if isinstance(rows, list) else []


def fetch(role):
    out = []
    for page in (1, 2, 3):
        rows = items(get_text("https://www.prace.cz/nabidky/", params={"q": role, **({"page": page} if page > 1 else {})}))
        for it in rows:
            ad, meta = it.get("advert") or {}, it.get("metadata") or {}
            if not ad.get("title") or not meta.get("advertId"):
                continue
            work = ad.get("workLocation") or {}
            places = (["Remote"] if work.get("type") == "FULL_REMOTE" else []) + [p.get("city") for p in work.get("places") or [] if p.get("city")]
            page_url = ((it.get("extended") or {}).get("careerPage") or {}).get("url") or ""
            s = ad.get("salary") or {}
            pay = f"{s.get('min', '')}–{s.get('max', '')} {s.get('currency', 'CZK')} / {str(s.get('period', '')).lower()}" if s.get("max") and s.get("displayMode") != "NO" else ""
            out.append({"id": f"prc:{meta['advertId']}", "ats": "", "board": "pracecz", "title": ad["title"],
                        "company": (it.get("company") or {}).get("publicName", ""), "location": ", ".join(places) or "Czechia",
                        "url": page_url.split("?")[0] if ".jobs.cz" in page_url else f"https://www.prace.cz/nabidka/{meta['advertId']}/",
                        "text": "", "posted": iso_day(meta.get("publishedAt")), "salary": pay, "via": NAME})
        if len(rows) < 40:
            break
    return out
