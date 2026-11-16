"""Get on Board (Latin American tech jobs; public JSON:API search)."""
from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Get on Board", "search", ["cl", "co", "mx", "ar", "br", "global"]


def fetch(role):
    out = []
    for page in (1, 2):
        d = get_json("https://www.getonbrd.com/api/v0/search/jobs",
                     params={"query": role, "per_page": 50, "page": page, "expand[]": "company"}) or {}
        for j in d.get("data") or []:
            a, url = j.get("attributes") or {}, (j.get("links") or {}).get("public_url", "")
            if not j.get("id") or not url or not a.get("title"):
                continue
            company = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name", "")
            where = ", ".join(a.get("countries") or []) + (" (Remote)" if a.get("remote") and "Remote" not in (a.get("countries") or []) else "")
            pay = f"USD {a['min_salary']:,}–{a['max_salary']:,} / month" if a.get("min_salary") and a.get("max_salary") else ""
            out.append({"id": f"gob:{j['id']}", "ats": "", "board": "getonbrd", "title": a["title"], "company": company,
                        "location": where, "url": url, "text": plain(f"{a.get('functions') or ''} {a.get('description') or ''}"),
                        "posted": iso_day(a.get("published_at")), "salary": pay, "via": NAME})
        if page >= ((d.get("meta") or {}).get("total_pages") or 0):
            break
    return out[:100]
