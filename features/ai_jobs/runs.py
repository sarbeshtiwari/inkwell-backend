"""Match runs: starting a search, its status, and auto-apply details."""

import re
import time
from datetime import datetime
from flask import jsonify
from bridge import login_required

from features.ai_jobs.apply import COVER_DOC, can_hand_over, cover_file
from features.ai_jobs.common import APPLY_PER_DAY, C, HANDOVER, J, MAX_RESULTS, bp
from infra import operations
from features.ai_jobs import signals
from features.ai_jobs.evaluate import report_brief, stamp_seen
from features.ai_jobs.tracker import STATES, due_followups


def save_match(ws, want, sources, results, jds, errors, engine, considered, error=""):
    """Record a finished job search (run by the agents: features/ai_jobs/agents.py) as the user's match results:
    the user's rules on each job, repost tracking, the saved descriptions, and one notification. Returns how many."""
    results = [r for r in results if isinstance(r, dict) and r.get("id")][:MAX_RESULTS]
    prev = ws.load("ai_match", {}) or {}
    run = {"want": want, "sources": sources, "started": prev.get("started") or time.time(), "finished": time.time(),
           "task_id": prev.get("task_id"), "skills": prev.get("skills", [])}
    try:
        resume = J().resume_text(ws) if not want.get("resume") else pdf_text(ws, want["resume"])
        run["skills"] = [x for x in J().skill_hits(resume, J().SKILLS) if x not in ("C", "R")]
    except Exception:
        pass
    if error:
        run.update(state="error", stage="Error", error=error, results=prev.get("results", []) if prev.get("state") == "done" else [])
    else:
        for r in results:  # what the page shows per job, read from its description
            jd = jds.get(r["id"], "")
            try:
                r["remote"] = signals.remote_mode(r, jd)
                pay = signals.pay_range(r, jd)
                r["pay"] = pay["text"] if pay else ""
            except Exception:
                pass
        with ws.lock:
            ws.save("ai_jds", {k: (v if isinstance(v, str) else "")[:12000] for k, v in jds.items() if k in {r["id"] for r in results}})
        stamp_seen(ws, results)
        run.update(state="done", stage="Done", results=results, errors=errors, engine=engine, considered=considered)
    with ws.lock:
        ws.save("ai_match", run)
    from features.notify import notify
    good = sum(1 for r in results if (r.get("score") or 0) >= 70)
    notify(ws.uid, f"AI job match: {good} strong match{'es' if good != 1 else ''}" if not error else "AI job match stopped",
           f"For {want.get('role', '')}. Open the page to review them." if not error else error, "/app/ai-jobs", "info")
    return len(results)


MIN_RESUME_WORDS = 10  # fewer readable words than this: the PDF has no usable text layer (scan or image)


def pdf_text(ws, name):
    import io
    from pypdf import PdfReader
    data = ws.doc_bytes(name)
    if not data:
        raise C().Invalid("That resume file no longer exists.", "resume")
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        raise C().Invalid("We couldn't read that PDF. Upload a text-based resume (not a scanned image).", "resume")


STAGES = {"job_scout": "Searching", "job_analyst": "Ranking matches"}


def match_state(ws):
    """The running search (its agent tasks) or the last finished one."""
    from infra import tasks
    saved = ws.load("ai_match", None) or {"state": "idle"}
    tid = saved.get("task_id")
    if saved.get("state") != "running" or not tid:
        return saved
    t = tasks.get(ws.uid, tid)
    if t and t["status"] not in tasks.FINAL:
        kids = tasks.children(ws.uid, tid)
        live = [k for k in kids if k["status"] not in tasks.FINAL]
        stage = STAGES.get(live[-1]["agent_id"], "Ranking matches") if live else ("Ranking matches" if kids else "Reading your resume")
        return {**saved, "stage": stage, "results": []}
    if t and t["status"] == "done":
        return ws.load("ai_match", None) or saved
    why = "The search was cancelled." if t and t["status"] == "cancelled" else \
        (t or {}).get("error") or "Something went wrong while matching. Please try again."
    return {**saved, "state": "error", "stage": "Error", "error": str(why)[:300]}


def public_state(ws):
    run = match_state(ws)
    applied = ws.load("ai_applied", {})
    out = {k: v for k, v in run.items() if k != "thread"}
    reports, pipe = ws.load("ai_reports", {}) or {}, ws.load("ai_pipeline", {}) or {}
    for r in out.get("results", []):
        r["apply"] = applied.get(r["id"])
        r["report"] = report_brief(reports[r["id"]]) if r["id"] in reports else None
        r["status"] = (pipe.get(r["id"]) or {}).get("status", "")
        if r["apply"] and r["apply"].get("state") == "applied" and r["status"] in ("", "evaluated"):
            r["status"] = "applied"
    return out | {"resumes": [d["name"] for d in ws.documents() if d["name"].lower().endswith(".pdf")],
                  "details": ws.load("ai_apply_profile", {}), "consent": bool(ws.settings().get("auto_apply_consent")),
                  "applying": operations.batch_view(ws.uid, "ai_apply"), "apply_per_day": APPLY_PER_DAY,
                  "can_hand_over": can_hand_over(), "handover": HANDOVER.get(ws.uid, ""),
                  "documents": [d["name"] for d in ws.documents() if COVER_DOC.search(d["name"])], "cover_letter": cover_file(ws)[0],
                  "evaluating": operations.batch_view(ws.uid, "ai_evaluate"), "statuses": {k: v[0] for k, v in STATES.items()},
                  "followups_due": sum(1 for f in due_followups(ws) if f["kind"] != "cold"),}


@bp.get("/api/ai-jobs")
@login_required
def status(ws):
    return jsonify(public_state(ws))


@bp.post("/api/ai-jobs/match")
@login_required
def start(ws):
    core = C()
    p = core.body()
    want = {"role": core.v_text(p.get("role"), "role", "Job you want", 80, required=True),
            "years": float(core.v_int(p.get("years", 0), "years", "Years of experience", 0, 40)),
            "company": core.v_text(p.get("company"), "company", "Company", 80),
            "location": core.v_text(p.get("location"), "location", "Location", 80),
            "resume": core.v_text(p.get("resume"), "resume", "Resume", 200)}
    if want["resume"] and want["resume"] not in {d["name"] for d in ws.documents()}:
        raise core.Invalid("Pick a resume from your files.", "resume")
    if not want["resume"] and not any(d["name"].lower().endswith(".pdf") for d in ws.documents()):
        raise core.Invalid("Upload your resume as a PDF on the Files page first.", "resume")
    sources = [s for s in (p.get("sources") or ["career_sites"]) if s in ("career_sites", "remote_boards")] or ["career_sites"]
    cur = match_state(ws)
    if cur.get("state") == "running":
        raise core.Invalid("A search is already running.", status=409)
    from infra import runtime
    if runtime.user_mode(ws.uid) == "off":
        raise core.Invalid("Agents are switched off for your account, and the AI job match runs on agents. "
                           "Turn them on in Approvals.", status=403)
    if core.rate_limited(("ai-match", ws.uid), 30, 86400):
        raise core.Invalid("You've run 30 searches today. Try again tomorrow.", status=429)
    # The resume is read by the agents (not here): reading a PDF from file storage can outlast the proxy's time limit.
    # The user asked for this search on this page, so its results are saved whatever the switch (Shadow or On);
    # the run only searches, ranks and saves: nothing is applied to or sent from here.
    tid = runtime.start(ws.uid, "orchestrator", {"criteria": {**want, "count": MAX_RESULTS, "sources": sources, "match": True,
                                                              "review": bool(p.get("use_llm", True)), "prepare": False,
                                                              "research": False, "apply": False}}, mode="on")
    with ws.lock:
        ws.save("ai_match", {**(ws.load("ai_match", {}) or {}), "state": "running", "stage": "Reading your resume",
                             "want": want, "sources": sources, "started": time.time(), "task_id": tid, "skills": []})
    return jsonify(ok=True)


DETAIL_FIELDS = {"linkedin": "LinkedIn", "github": "GitHub", "portfolio": "Portfolio", "current_company": "Current company",
                 "location": "Location", "notice": "Notice period"}


@bp.put("/api/ai-jobs/details")
@login_required
def save_details(ws):
    core, p = C(), C().body()
    out = {}
    for k, label in DETAIL_FIELDS.items():
        v = core.v_text(p.get(k), k, label, 200)
        if v and k in ("linkedin", "github", "portfolio") and not re.match(r"^https://[^\s<>\"']+$", v):
            raise core.Invalid(f"{label} should be a full https:// link.", k)
        out[k] = v
    cover = str(p.get("cover_letter", (ws.load("ai_apply_profile", {}) or {}).get("cover_letter", "auto")))[:200]
    if cover not in ("auto", "none") and cover not in {d["name"] for d in ws.documents()}:
        raise core.Invalid("Pick a cover letter from your files.", "cover_letter")
    out["cover_letter"] = cover
    consent = p.get("consent")
    with ws.lock:
        ws.save("ai_apply_profile", out)
    if consent is not None:
        ws.update_settings(auto_apply_consent=datetime.now().isoformat(timespec="seconds") if consent else "")
    return jsonify(details=out, consent=bool(ws.settings().get("auto_apply_consent")))
