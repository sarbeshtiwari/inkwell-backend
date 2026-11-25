"""The daily briefing (idea and sections from Morrow's pipeline/briefing.py and common/report.py, MIT):
today's goal and lesson, the job-search report, two recent papers, tech news, new GitHub repositories and
companies worth a look.

Items come from public feeds (arXiv, Hacker News, GitHub search) so every link is real; when Claude Code / Kiro
is the AI, it adds a short "why it matters" and today's focus, written only from those items. Built once a day
(after 07:00 in the user's mail-sync time zone) or on demand; the last 14 days are kept."""

import datetime as dt
import re
import threading
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from flask import jsonify

from bridge import login_required
from features.ai_jobs.boards._util import get_json, get_text
from features.learning import course
from features.learning.common import C, bp

KEEP_DAYS = 14
BUILDING = set()
AI_WORDS = re.compile(r"\b(ai|llm|gpt|claude|gemini|model|agent|ml|machine learning|neural|rag|transformer|openai|anthropic|"
                      r"inference|diffusion|python|database|postgres|kubernetes|rust|react)\b", re.I)


def papers(seen):
    """Two newest arXiv papers in AI / ML / NLP not shown in the last 30 days (Morrow: 2 papers, last 30 days)."""
    xml = get_text("https://export.arxiv.org/api/query", params={
        "search_query": "cat:cs.AI OR cat:cs.LG OR cat:cs.CL", "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 25})
    out = []
    try:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in ET.fromstring(xml or "<x/>").findall("a:entry", ns):
            url = (e.findtext("a:id", "", ns) or "").replace("http://", "https://")
            if not url or url in seen:
                continue
            out.append({"title": " ".join((e.findtext("a:title", "", ns) or "").split()), "url": url,
                        "published": (e.findtext("a:published", "", ns) or "")[:10],
                        "authors": ", ".join(a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)[:3]),
                        "summary": " ".join((e.findtext("a:summary", "", ns) or "").split())[:420]})
            if len(out) == 2:
                break
    except ET.ParseError:
        pass
    return out


def news():
    """Tech stories from the Hacker News front page in the last day, AI and engineering first (Morrow: 6+ links)."""
    since = int((dt.datetime.now() - dt.timedelta(days=1)).timestamp())
    d = get_json("https://hn.algolia.com/api/v1/search", params={"tags": "story", "numericFilters": f"created_at_i>{since},points>60",
                                                               "hitsPerPage": 50}) or {}
    rows = [{"title": h.get("title", ""), "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
             "points": h.get("points", 0), "comments": f"https://news.ycombinator.com/item?id={h['objectID']}"}
            for h in d.get("hits", []) if h.get("title")]
    rows.sort(key=lambda r: (not AI_WORDS.search(r["title"]), -r["points"]))
    return rows[:8]


def repositories(seen):
    """3–5 GitHub repositories created in the last week, most stars first, about AI or developer tools."""
    since = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    d = get_json("https://api.github.com/search/repositories", params={
        "q": f"created:>{since} stars:>100", "sort": "stars", "order": "desc", "per_page": 30},
        headers={"Accept": "application/vnd.github+json"}) or {}
    rows = [{"name": r["full_name"], "url": r["html_url"], "description": (r.get("description") or "")[:240],
             "stars": r.get("stargazers_count", 0), "language": r.get("language") or ""}
            for r in d.get("items", []) if r.get("html_url") not in seen]
    rows.sort(key=lambda r: (not AI_WORDS.search(r["name"] + " " + r["description"]), -r["stars"]))
    return rows[:5]


def job_report(ws):
    """Inkwell's own report (Morrow's report.md, built from data only): yesterday's applications, replies,
    follow-ups due and strong matches you haven't applied to."""
    from features.ai_jobs.tracker import due_followups, insights
    out = {"applied": 0, "heard": 0, "interviews": 0, "followups": [], "matches": []}
    try:
        t = insights(ws, 1)["total"]
        out.update(applied=t["total"], heard=t["heard"], interviews=t["interviews"])
    except Exception:
        pass
    try:
        out["followups"] = [{"company": f["company"], "role": f["role"], "kind": f["kind"], "overdue": f.get("overdue", 0)}
                            for f in due_followups(ws) if f["kind"] != "cold"][:6]
    except Exception:
        pass
    applied = {(a.get("company") or "").lower() for a in (ws.load("applications", {}) or {}).values()}
    strong = [r for r in (ws.load("ai_match", {}) or {}).get("results", []) if r.get("score", 0) >= 70
              and (r.get("company") or "").lower() not in applied]
    out["matches"] = [{"company": r.get("company"), "title": r.get("title"), "score": r.get("score"), "url": r.get("url", "")}
                      for r in sorted(strong, key=lambda r: -r.get("score", 0))[:5]]
    return out


def ai_notes(ws, b):
    """Today's focus and one line per item, from Claude Code / Kiro (or any configured model); none without one."""
    from features.ai_jobs import models
    cfg = models.config(ws)
    if not cfg or cfg["provider"] == "ollama":
        return {}
    items = {"lesson": b["goal"]["title"], "dsa": b["goal"]["dsa"],
             "papers": [p["title"] + ": " + p["summary"][:300] for p in b["papers"]],
             "news": [n["title"] for n in b["news"]], "repos": [r["name"] + ": " + r["description"] for r in b["repos"]],
             "report": b["report"]}
    try:
        return models.chat(cfg, "You write a developer's morning briefing. Use only the items given; never add facts, numbers or links.",
                           "Items:\n" + str(items) + '\n\nReturn JSON {"focus": "<3 short sentences: what to do today>", '
                           '"papers": ["<why it matters, 1 sentence>", ...], "repos": ["<why it matters, 1 sentence>", ...]}',
                           as_json=True, max_tokens=700, timeout=180)
    except Exception as e:
        from infra.redact import error_text
        return {"error": error_text(e, 200)}


def build(ws):
    hist = ws.load("briefings", {}) or {}
    seen_papers = {p["url"] for b in hist.values() for p in b.get("papers", [])}
    seen_repos = {r["url"] for b in hist.values() for r in b.get("repos", [])}
    s = course.state(ws)
    day = s["completed_today"] or s["current"] or course.TOTAL
    spec = course.interview_course.day_spec(day)
    b = {"date": dt.date.today().isoformat(), "built": C().now_iso(),
         "goal": {"day": day, "title": spec["title"], "objectives": spec.get("objectives", []), "dsa": spec["dsa"],
                  "system_design": spec["system_design"], "due_reviews": s["due_count"], "streak": s["activity"].get("current_streak")},
         "report": job_report(ws), "papers": papers(seen_papers), "news": news(), "repos": repositories(seen_repos)}
    b["notes"] = ai_notes(ws, b)
    with ws.lock:
        hist = ws.load("briefings", {}) or {}
        hist[b["date"]] = b
        ws.save("briefings", dict(sorted(hist.items())[-KEEP_DAYS:]))
    return b


def build_async(uid, notify_user=False):
    if uid in BUILDING:
        return
    BUILDING.add(uid)

    def run():
        try:
            ws = C().Workspace(uid)
            b = build(ws)
            if notify_user:
                from features.notify import notify
                notify(uid, "Your morning briefing is ready", f"Day {b['goal']['day']}: {b['goal']['title']} · "
                       f"{len(b['report']['followups'])} follow-ups · {len(b['report']['matches'])} strong matches", "/app/learning", "info")
        except Exception as e:
            print(f"[Inkwell] briefing: {e!r}", flush=True)
        finally:
            BUILDING.discard(uid)
    threading.Thread(target=run, daemon=True, name=f"briefing-{uid[:6]}").start()


def daily_tick():
    """Every few minutes: each user who has used Learning gets today's briefing once, after 07:00 their time."""
    from features.apps.sync import sync_prefs
    core = C()
    for row in core.M.users.find({}, {"_id": 1}):
        ws = core.Workspace(str(row["_id"]))
        if not ws.load("learning", None):
            continue
        try:
            tz = ZoneInfo(sync_prefs(ws).get("tz") or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")
        now = dt.datetime.now(tz)
        if now.hour >= 7 and now.date().isoformat() not in (ws.load("briefings", {}) or {}):
            build_async(ws.uid, notify_user=True)


@bp.get("/api/learning/briefing")
@login_required
def get_briefing(ws):
    hist = ws.load("briefings", {}) or {}
    today = dt.date.today().isoformat()
    if today not in hist:
        build_async(ws.uid)
    return jsonify(briefing=hist.get(today) or (hist[max(hist)] if hist else None), building=ws.uid in BUILDING,
                   dates=sorted(hist, reverse=True))


@bp.post("/api/learning/briefing")
@login_required
def rebuild_briefing(ws):
    core = C()
    if core.rate_limited(("briefing", ws.uid), 6, 3600):
        raise core.Invalid("You've rebuilt the briefing a few times this hour. Try again later.", status=429)
    build_async(ws.uid)
    return jsonify(building=True)
