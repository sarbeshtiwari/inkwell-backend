"""Match runs: starting a search, its status, search keys and auto-apply details."""

import re
import threading
import time
from datetime import datetime
from flask import jsonify
from bridge import login_required

from features.ai_jobs.apply import COVER_DOC, can_hand_over, cover_file
from features.ai_jobs.common import APPLYING, APPLY_PER_DAY, C, HANDOVER, J, OLLAMA_MODEL, RUNS, bp
from features.ai_jobs.ranking import build_chain
from features.ai_jobs import learning, signals
from features.ai_jobs.evaluate import EVALUATING, get_rules, report_brief, stamp_seen
from features.ai_jobs.tracker import STATES, due_followups, set_status


def run_match(uid, want, sources, use_llm):
    ws = C().Workspace(uid)
    run = RUNS[uid]
    try:
        resume = J().resume_text(ws) if not want.get("resume") else pdf_text(ws, want["resume"])
        profile = {**want, "resume": resume, "skills": [s for s in J().skill_hits(resume, J().SKILLS) if s not in ("C", "R")]}
        run["skills"] = profile["skills"]
        run["stage"] = "Searching"
        keys = ws.load("ai_keys", {})
        from features.ai_jobs import models
        with models.use(models.config(ws) if use_llm else None):
            state = build_chain(keys, sources, ws.load("ai_portals", []) or [], want.get("location", "")).invoke(
                {"profile": profile, "use_llm": use_llm, "taste": learning.model(ws), "taste_hint": learning.prompt_hint(ws)})
        results = state["ranked"]
        mark_rules(ws, results, state.get("jds", {}))
        with ws.lock:
            ws.save("ai_jds", state.get("jds", {}))
        stamp_seen(ws, results)
        run.update(state="done", stage="Done", results=results, errors=state["errors"], engine=state["engine"],
                   considered=state.get("considered", 0))
    except Exception as e:
        print(f"[Inkwell] AI match failed: {e!r}", flush=True)
        run.update(state="error", stage="Error", error="Something went wrong while matching. Please try again.")
    run["finished"] = time.time()
    with ws.lock:
        ws.save("ai_match", {k: v for k, v in run.items() if k != "thread"})
    from features.notify import notify
    good = sum(1 for r in run.get("results", []) if r["score"] >= 70)
    notify(uid, f"AI job match: {good} strong match{'es' if good != 1 else ''}", f"For {want['role']}. Open the page to review them.",
           "/app/ai-jobs", "info")


def mark_rules(ws, results, jds):
    """Check each match against the user's own rules (blacklist and dealbreakers); a job that breaks one is marked Skip."""
    rules = get_rules(ws)
    for r in results:
        jd = jds.get(r["id"], "")
        try:
            r["rules"] = signals.rule_hits(r, jd, rules)
            r["remote"] = signals.remote_mode(r, jd)
            pay = signals.pay_range(r, jd)
            r["pay"] = pay["text"] if pay else ""
        except Exception as e:  # one odd posting never spoils the whole run
            print(f"[Inkwell] rules check skipped for {r.get('company')}: {e!r}", flush=True)
            r.setdefault("rules", [])
        if r["rules"]:
            set_status(ws, r["id"], "skip", only_if_new=True, note=r["rules"][0]["rule"])


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


def model_state(ws):
    from features.ai_jobs import models
    cfg = models.config(ws)
    return {"local_model": bool(cfg), "model_name": cfg["label"] if cfg else OLLAMA_MODEL,
            "model_provider": (ws.load("ai_model", {}) or {}).get("provider") or "ollama"}


def public_state(ws):
    run = RUNS.get(ws.uid) or ws.load("ai_match", None) or {"state": "idle"}
    keys = ws.load("ai_keys", {})
    applied = ws.load("ai_applied", {})
    out = {k: v for k, v in run.items() if k != "thread"}
    reports, pipe = ws.load("ai_reports", {}) or {}, ws.load("ai_pipeline", {}) or {}
    for r in out.get("results", []):
        r["apply"] = applied.get(r["id"])
        r["report"] = report_brief(reports[r["id"]]) if r["id"] in reports else None
        r["status"] = (pipe.get(r["id"]) or {}).get("status", "")
        if r["apply"] and r["apply"].get("state") == "applied" and r["status"] in ("", "evaluated"):
            r["status"] = "applied"
    return out | {"keys": {"tavily": bool(keys.get("tavily")), "brave": bool(keys.get("brave"))}, **model_state(ws), "resumes": [d["name"] for d in ws.documents() if d["name"].lower().endswith(".pdf")],
                  "details": ws.load("ai_apply_profile", {}), "consent": bool(ws.settings().get("auto_apply_consent")),
                  "applying": APPLYING.get(ws.uid, {}), "apply_per_day": APPLY_PER_DAY,
                  "can_hand_over": can_hand_over(), "handover": HANDOVER.get(ws.uid, ""),
                  "documents": [d["name"] for d in ws.documents() if COVER_DOC.search(d["name"])], "cover_letter": cover_file(ws)[0],
                  "evaluating": EVALUATING.get(ws.uid, {}), "statuses": {k: v[0] for k, v in STATES.items()},
                  "rules": get_rules(ws), "followups_due": sum(1 for f in due_followups(ws) if f["kind"] != "cold"),
            "feedback": {k: ("good" if "good" in v["signals"] else "bad") for k, v in (ws.load("ai_feedback", {}) or {}).items()
                         if {"good", "bad"} & set(v.get("signals", {}))},
            "learning": {"lessons": learning.model(ws)["lessons"], "needed": learning.MIN_LESSONS,
                         "suggestions": len(learning.suggestions(ws))}}


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
    sources = [s for s in (p.get("sources") or ["career_sites"]) if s in ("career_sites", "remote_boards", "tavily", "brave")] or ["career_sites"]
    keys = ws.load("ai_keys", {})
    for s in sources:
        if s in ("tavily", "brave") and not keys.get(s):
            raise core.Invalid(f"Add your {s.capitalize()} API key first, or untick it.", "sources")
    if (RUNS.get(ws.uid) or {}).get("state") == "running":
        raise core.Invalid("A search is already running.", status=409)
    if core.rate_limited(("ai-match", ws.uid), 30, 86400):
        raise core.Invalid("You've run 30 searches today. Try again tomorrow.", status=429)
    RUNS[ws.uid] = {"state": "running", "stage": "Reading your resume", "want": want, "sources": sources, "started": time.time(), "results": []}
    threading.Thread(target=run_match, args=(ws.uid, want, sources, bool(p.get("use_llm", True))), daemon=True, name="ai-match").start()
    return jsonify(ok=True)


@bp.put("/api/ai-jobs/keys")
@login_required
def save_keys(ws):
    p = C().body()
    with ws.lock:
        keys = ws.load("ai_keys", {})
        for k in ("tavily", "brave"):
            if k in p:
                v = str(p[k] or "").strip()
                if v and not re.fullmatch(r"[\w\-]{10,200}", v):
                    raise C().Invalid("That doesn't look like an API key.", k)
                keys[k] = v
        ws.save("ai_keys", keys)
    return jsonify(tavily=bool(keys.get("tavily")), brave=bool(keys.get("brave")))


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
