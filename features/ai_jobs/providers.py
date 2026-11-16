"""More public, no-login job sources (ideas from career-ops' providers): Workable, SmartRecruiters and Recruitee
company boards, plus remote job boards (Remotive, Remote OK, Himalayas, Arbeitnow, Jobicy).

Every source here is a documented public feed that asks only for a link back, so each job keeps its own URL
and the page links to it. Jobs from these sources are shown and evaluated; auto-apply stays on Greenhouse,
Lever and Ashby, where the form is known."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from features.ai_jobs.common import get_json, plain


def iso_day(value):
    """'2026-10-29T18:22:41', 1793594956 or '1793594956' -> '2026-10-29' ('' when unknown)."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) or str(value).isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    m = re.match(r"\d{4}-\d{2}-\d{2}", str(value))
    return m.group(0) if m else ""


def norm_workable(board, company, j):
    where = ", ".join(filter(None, [j.get("city", ""), j.get("country", ""), "Remote" if j.get("telecommuting") else ""]))
    return {"id": f"wk:{board}:{j.get('shortcode')}", "ats": "workable", "board": board, "title": j.get("title", ""),
            "company": company or board.capitalize(), "location": where, "url": j.get("url") or j.get("shortlink", ""),
            "apply_url": j.get("application_url", ""), "text": plain(j.get("description", "")), "posted": iso_day(j.get("published_on"))}


def norm_smartrecruiters(board, j):
    loc = j.get("location") or {}
    where = ", ".join(filter(None, [loc.get("city", ""), loc.get("country", "").upper(), "Remote" if loc.get("remote") else ""]))
    return {"id": f"sr:{board}:{j.get('id')}", "ats": "smartrecruiters", "board": board, "title": j.get("name", ""),
            "company": (j.get("company") or {}).get("name") or board, "location": where,
            "url": f"https://jobs.smartrecruiters.com/{board}/{j.get('id')}", "text": "", "posted": iso_day(j.get("releasedDate"))}


def norm_recruitee(board, j):
    where = ", ".join(filter(None, [j.get("city", ""), j.get("country", ""), "Remote" if j.get("remote") else ""]))
    return {"id": f"rc:{board}:{j.get('id')}", "ats": "recruitee", "board": board, "title": j.get("title", ""),
            "company": j.get("company_name") or board.capitalize(), "location": where, "url": j.get("careers_url", ""),
            "apply_url": j.get("careers_apply_url", ""), "text": plain(f"{j.get('description', '')} {j.get('requirements', '')}"),
            "posted": iso_day(j.get("published_at") or j.get("created_at"))}


def extra_board_jobs(ats, board):
    """Open jobs on one company board on Workable, SmartRecruiters or Recruitee ([] when the board doesn't exist)."""
    if ats == "workable":
        d = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{board}", params={"details": "true"}) or {}
        return [norm_workable(board, d.get("name", ""), j) for j in d.get("jobs", [])]
    if ats == "smartrecruiters":
        d = get_json(f"https://api.smartrecruiters.com/v1/companies/{board}/postings", params={"limit": 100}) or {}
        return [norm_smartrecruiters(board, j) for j in d.get("content", [])]
    if ats == "recruitee":
        d = get_json(f"https://{board}.recruitee.com/api/offers/") or {}
        return [norm_recruitee(board, j) for j in d.get("offers", []) if j.get("status", "published") == "published"]
    return []


def smartrecruiters_text(job):
    """SmartRecruiters lists postings without their description; read it for the jobs that get ranked."""
    _, board, jid = job["id"].split(":", 2)
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{jid}") or {}
    sections = (d.get("jobAd") or {}).get("sections") or {}
    return plain(" ".join((s or {}).get("text", "") for s in sections.values()))


# --- remote job boards -------------------------------------------------------------------------------------

def remotive(query):
    d = get_json("https://remotive.com/api/remote-jobs", params={"search": query, "limit": 60}) or {}
    return [{"id": f"rm:{j['id']}", "ats": "", "board": "remotive", "title": j.get("title", ""), "company": j.get("company_name", ""),
             "location": "Remote" + (f" ({j['candidate_required_location']})" if j.get("candidate_required_location") else ""),
             "url": j.get("url", ""), "text": plain(j.get("description", "")), "posted": iso_day(j.get("publication_date")),
             "salary": j.get("salary", ""), "via": "Remotive"} for j in d.get("jobs", []) if j.get("id")]


def remoteok(query):
    d = get_json("https://remoteok.com/api")
    words = [w for w in re.findall(r"[a-z0-9+#]+", query.lower()) if len(w) > 2]
    out = []
    for j in d if isinstance(d, list) else []:
        if not j.get("id") or not j.get("position"):
            continue
        hay = f"{j['position']} {' '.join(j.get('tags') or [])}".lower()
        if words and not any(w in hay for w in words):
            continue
        pay = f"${j['salary_min']:,}–${j['salary_max']:,}" if j.get("salary_min") and j.get("salary_max") else ""
        out.append({"id": f"ro:{j['id']}", "ats": "", "board": "remoteok", "title": j["position"], "company": j.get("company", ""),
                    "location": "Remote" + (f" ({j['location']})" if j.get("location") else ""), "url": j.get("url", ""),
                    "text": plain(j.get("description", "")), "posted": iso_day(j.get("date")), "salary": pay, "via": "Remote OK"})
    return out


def himalayas(query):
    d = get_json("https://himalayas.app/jobs/api/search", params={"q": query}) or {}
    out = []
    for j in d.get("jobs", []):
        pay = ""
        if j.get("minSalary") and j.get("maxSalary"):
            pay = f"{j.get('currency') or ''} {int(j['minSalary']):,}–{int(j['maxSalary']):,} / {j.get('salaryPeriod') or 'year'}".strip()
        where = ", ".join(j.get("locationRestrictions") or []) or "Anywhere"
        out.append({"id": "hm:" + (j.get("guid") or j.get("applicationLink", "")).rsplit("/", 1)[-1], "ats": "", "board": "himalayas",
                    "title": j.get("title", ""), "company": j.get("companyName", ""), "location": f"Remote ({where})",
                    "url": j.get("applicationLink") or j.get("guid", ""), "text": plain(j.get("description") or j.get("excerpt", "")),
                    "posted": iso_day(j.get("pubDate")), "salary": pay, "via": "Himalayas"})
    return out


def arbeitnow(query):
    d = get_json("https://www.arbeitnow.com/api/job-board-api") or {}
    words = [w for w in re.findall(r"[a-z0-9+#]+", query.lower()) if len(w) > 2]
    return [{"id": f"an:{j['slug']}", "ats": "", "board": "arbeitnow", "title": j.get("title", ""), "company": j.get("company_name", ""),
             "location": ", ".join(filter(None, [j.get("location", ""), "Remote" if j.get("remote") else ""])), "url": j.get("url", ""),
             "text": plain(j.get("description", "")), "posted": iso_day(j.get("created_at")), "via": "Arbeitnow"}
            for j in d.get("data", []) if j.get("slug") and (not words or any(w in j.get("title", "").lower() for w in words))]


def jobicy(query):
    """Jobicy filters by one tag: try the whole role, then its words one by one, keeping titles that mention them."""
    words = [w for w in re.findall(r"[a-z0-9+#]+", query.lower()) if len(w) > 2]
    d = {}
    for tag in [re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")] + sorted(words, key=len, reverse=True)[:2]:
        d = get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": tag}) or {}
        if d.get("jobs"):
            break
    return [{"id": f"jc:{j['id']}", "ats": "", "board": "jobicy", "title": plain(j.get("jobTitle", "")), "company": j.get("companyName", ""),
             "location": "Remote" + (f" ({j['jobGeo']})" if j.get("jobGeo") else ""), "url": j.get("url", ""),
             "text": plain(j.get("jobDescription") or j.get("jobExcerpt", "")), "posted": iso_day(j.get("pubDate")), "via": "Jobicy"}
            for j in d.get("jobs", []) if j.get("id") and (not words or any(w in j.get("jobTitle", "").lower() for w in words))]


REMOTE_BOARDS = {"Remotive": remotive, "Remote OK": remoteok, "Himalayas": himalayas, "Arbeitnow": arbeitnow, "Jobicy": jobicy}


def remote_board_jobs(role):
    """Jobs for a role from every remote board, side by side; a board that doesn't answer is skipped."""
    def one(fn):
        try:
            return fn(role)
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=len(REMOTE_BOARDS)) as pool:
        return [j for js in pool.map(one, REMOTE_BOARDS.values()) for j in js if j.get("url") and j.get("title")]
