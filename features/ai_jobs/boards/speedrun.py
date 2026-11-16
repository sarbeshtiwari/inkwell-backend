"""a16z Speedrun Talent Network: jobs at a16z portfolio companies (public feed)."""
from features.ai_jobs.boards._util import get_json, iso_day, title_matches

NAME, KIND, REGIONS = "a16z Talent Network", "search", ["global"]


def fetch(role):
    out = []
    for page in range(1, 11):
        d = get_json(f"https://speedrun-talent-network.com/api/v1/jobs?page={page}&source=reachout") or {}
        rows = d.get("jobs") or []
        for j in rows:
            if not (j.get("title") and str(j.get("url", "")).startswith("https://")) or not title_matches(j["title"], role):
                continue
            pay = f"{j.get('comp_currency') or ''} {j['comp_min']}–{j.get('comp_max') or ''}".strip(" –") if j.get("comp_min") else ""
            out.append({"id": f"a16z:{j.get('id')}", "ats": "", "board": "speedrun", "title": j["title"], "company": j.get("company", ""),
                        "location": ", ".join(filter(None, [j.get("location"), "Remote" if j.get("remote") else ""])), "url": j["url"],
                        "text": "", "posted": iso_day(j.get("published_at")), "salary": pay, "via": NAME})
        if not rows or page >= (d.get("total_pages") or 1):
            break
    return out[:100]
