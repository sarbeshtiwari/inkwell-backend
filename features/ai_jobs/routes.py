"""Auto-apply API: start applying, the live view, Fill & finish, and the Apply-in-my-browser kit."""

import threading
import time
from flask import jsonify
from bridge import login_required

from features.ai_jobs.answers import answer_for, norm_q, q_score, same_company
from features.ai_jobs.apply import apply_one, closed_result, apply_url, apply_values, can_hand_over, record_application, resume_file
from features.ai_jobs.common import APPLYING, APPLY_PER_DAY, C, HANDOVER, LIVE, bp
from features.ai_jobs.tracker import set_status
from features.ai_jobs.watch import run_watch


def run_apply(uid, picks, visible=False):
    if visible:
        return run_watch(uid, picks)
    ws = C().Workspace(uid)
    st = APPLYING[uid]
    try:
        for job in picks:
            st["current"] = job["title"]
            try:
                res = closed_result(job) or apply_one(ws, job, submit=True, visible=visible)
            except Exception as e:
                print(f"[Reachout] auto-apply error: {e}", flush=True)
                res = {"state": "needs_you", "detail": "The application page didn't load as expected. Apply on the site.", "url": job["url"]}
            res["at"] = time.time()
            with ws.lock:
                done = ws.load("ai_applied", {})
                done[job["id"]] = res
                ws.save("ai_applied", done)
            if res["state"] == "applied":
                record_application(ws, job)
            if res["state"] in ("applied", "closed"):
                set_status(ws, job["id"], "applied" if res["state"] == "applied" else "discarded",
                           note="" if res["state"] == "applied" else "Posting closed")
            st["done"] += 1
    finally:
        st["state"] = "done"
        from features.notify import notify
        ok = sum(1 for j in picks if (ws.load("ai_applied", {}).get(j["id"]) or {}).get("state") == "applied")
        notify(uid, f"Reachout AI applied to {ok} job{'s' if ok != 1 else ''}", f"{len(picks) - ok} need you to finish them.",
               "/app/ai-jobs", "application")


def gate(ws, picks, anyway):
    """career-ops' gates before applying: the user's own rules (blacklist, dealbreakers) and their minimum score.
    The user's call always wins: "apply anyway" skips the gate for jobs they picked."""
    from features.ai_jobs.evaluate import get_rules
    rules = get_rules(ws)
    reports = ws.load("ai_reports", {}) or {}
    floor = rules.get("min_score_to_apply") or 0
    stopped = []
    for r in picks:
        why = r.get("rules") or []
        rep = reports.get(r["id"])
        if why:
            stopped.append(f"{r['company']}: {why[0]['rule']}")
        elif floor and rep and rep["score5"] < floor:
            stopped.append(f"{r['company']}: scored {rep['score5']}/5, below your minimum of {floor}")
    if stopped and not anyway:
        raise C().Invalid("Some picks break your rules: " + "; ".join(stopped[:4]) + (" …" if len(stopped) > 4 else "") +
                          ". Untick them, or apply anyway.", "rules", status=409)
    return picks


@bp.post("/api/ai-jobs/apply")
@login_required
def apply(ws):
    core = C()
    if not ws.settings().get("auto_apply_consent"):
        raise core.Invalid("Turn on auto-apply and confirm first.", "consent")
    ids = [str(x) for x in (core.body().get("ids") or [])][:APPLY_PER_DAY]
    run = ws.load("ai_match", {})
    done = ws.load("ai_applied", {})
    picks = [r for r in run.get("results", []) if r["id"] in ids and r.get("can_apply") and (done.get(r["id"]) or {}).get("state") != "applied"]
    if not picks:
        raise core.Invalid("Pick at least one job that can be applied to automatically.")
    picks = gate(ws, picks, bool(core.body().get("anyway")))
    if not ws.profile().get("phone"):
        raise core.Invalid("Add your phone number on the Profile page first; application forms ask for it.", "phone")
    if (APPLYING.get(ws.uid) or {}).get("state") == "running":
        raise core.Invalid("Already applying. Wait for it to finish.", status=409)
    for _ in picks:
        if core.rate_limited(("ai-apply", ws.uid), APPLY_PER_DAY, 86400):
            raise core.Invalid(f"Reachout AI applies to at most {APPLY_PER_DAY} jobs a day.", status=429)
    visible = bool(core.body().get("watch")) and can_hand_over()
    APPLYING[ws.uid] = {"state": "running", "total": len(picks), "done": 0, "current": "", "visible": visible}
    threading.Thread(target=run_apply, args=(ws.uid, picks, visible), daemon=True, name="ai-apply").start()
    return jsonify(ok=True, total=len(picks))


@bp.get("/api/ai-jobs/live")
@login_required
def live_view(ws):
    """The latest picture of the application browser and what it just did (polled by the page while applying)."""
    import base64
    st = LIVE.get(ws.uid)
    if not st:
        return jsonify(active=False)
    busy = (APPLYING.get(ws.uid) or {}).get("state") == "running" or ws.uid in HANDOVER
    return jsonify(active=busy or time.time() - st["at"] < 120, job=st["job"], log=st["log"], at=st["at"],
                   frame=("data:image/jpeg;base64," + base64.b64encode(st["frame"]).decode()) if st["frame"] else "")


@bp.post("/api/ai-jobs/finish/<jid>")
@login_required
def finish(ws, jid):
    """Open a browser window on this computer with the form filled in, for the user to answer the rest and submit."""
    core = C()
    if not can_hand_over():
        raise core.Invalid("This works when Reachout runs on your own computer. Open the form on the site instead.", status=400)
    if not ws.settings().get("auto_apply_consent"):
        raise core.Invalid("Turn on auto-apply and confirm first.", "consent")
    job = next((r for r in ws.load("ai_match", {}).get("results", []) if r["id"] == jid and r.get("can_apply")), None)
    if not job:
        raise core.Invalid("That job isn't in your matches any more.", status=404)
    if ws.uid in HANDOVER or (APPLYING.get(ws.uid) or {}).get("state") == "running":
        raise core.Invalid("A form is already open. Finish or close that window first.", status=409)
    if not resume_file(ws)[1]:
        raise core.Invalid("Upload your resume as a PDF first.", "resume")
    HANDOVER[ws.uid] = jid
    APPLYING[ws.uid] = {"state": "running", "total": 1, "done": 0, "current": job["title"], "visible": True}
    threading.Thread(target=run_watch, args=(ws.uid, [job]), daemon=True, name="ai-watch").start()
    return jsonify(ok=True)


@bp.get("/api/ai-jobs/kit/<jid>")
@login_required
def apply_kit(ws, jid):
    """Everything needed to apply by hand: the link, contact details and the answers for this job's questions."""
    core = C()
    job = next((r for r in ws.load("ai_match", {}).get("results", []) if r["id"] == jid), None)
    if not job:
        raise core.Invalid("That job isn't in your matches any more.", status=404)
    v = apply_values(ws)
    labels = [("First name", "first"), ("Last name", "last"), ("Email", "email"), ("Phone", "phone"), ("Location", "location"),
              ("LinkedIn", "linkedin"), ("GitHub", "github"), ("Portfolio", "portfolio"), ("Current company", "current_company"),
              ("Notice period", "notice")]
    fields = [{"label": l, "value": v[k]} for l, k in labels if v.get(k)]
    bank = ws.load("ai_answers", {})
    company = job.get("company", "")
    questions = (ws.load("ai_applied", {}).get(jid) or {}).get("questions") or []
    answers, seen = [], set()
    for q in questions:
        q = q.removesuffix(" (choose from the list)")
        hit = answer_for(bank, q, company)
        draft = None
        if not hit:  # show a draft too, clearly marked, so the user can check it before using it
            for a in bank.values():
                if a.get("status") == "review" and (not a.get("company") or same_company(a["company"], company)) \
                        and q_score(norm_q(q, company), a.get("norm") or "") >= 0.8:
                    draft = a["answer"].replace("{company}", company)
        answers.append({"question": q, "answer": hit or draft or "", "draft": bool(draft and not hit)})
        seen.add(norm_q(q, company))
    for a in bank.values():  # other answers written for this company
        if a.get("company") and same_company(a["company"], company) and a.get("norm") not in seen:
            answers.append({"question": a["question"], "answer": a["answer"].replace("{company}", company), "draft": a.get("status") != "approved"})
    resume = resume_file(ws)[0]
    return jsonify(url=job["url"], apply_url=apply_url(job), title=job["title"], company=company, resume=resume, fields=fields, answers=answers)
