"""Watch mode: a visible browser on the user's computer that pauses for sign-ins, questions and CAPTCHAs."""

import os
import re
import tempfile
import time

from features.ai_jobs.apply import BLOCKED, BLOCKED_RESULT, CODE_STEP, CONFIRMED, closed_result, apply_url, apply_values, is_blocked_board, mark_blocked, open_form, record_application, resume_file, write_cover
from features.ai_jobs.common import APPLY_LOCK, C, HANDOVER, Live
from features.ai_jobs import siteapply
from features.ai_jobs.forms import challenge_visible, fill_form, learn_from_page, questions_left


OVERLAY_JS = r"""
(() => {
  if (window.__ro_show) return;
  window.__ro_action = "";
  window.__ro_show = (msg, buttons) => {
    const mount = () => {
      let bar = document.getElementById("__reachout_bar");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "__reachout_bar";
        bar.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:2147483647;display:flex;gap:10px;"
          + "align-items:center;max-width:min(760px,94vw);padding:10px 12px 10px 16px;border-radius:14px;color:#fff;"
          + "font:600 14px/1.35 -apple-system,system-ui,sans-serif;background:linear-gradient(120deg,#7a4b8f,#0f6b54);box-shadow:0 10px 30px rgba(0,0,0,.25)";
        document.documentElement.appendChild(bar);
      }
      bar.innerHTML = "";
      const t = document.createElement("span"); t.textContent = "✦ Inkwell AI · " + msg; t.style.flex = "1"; bar.appendChild(t);
      for (const [id, label] of buttons || []) {
        const b = document.createElement("button"); b.textContent = label; b.type = "button";
        b.style.cssText = "border:0;border-radius:9px;padding:7px 12px;font:600 13px system-ui;cursor:pointer;"
          + (id === "continue" ? "background:#fff;color:#0f6b54" : "background:rgba(255,255,255,.18);color:#fff");
        b.onclick = e => { e.preventDefault(); window.__ro_action = id; };
        bar.appendChild(b);
      }
    };
    document.body ? mount() : addEventListener("DOMContentLoaded", mount);
  };
})();
"""


LOGIN_WALL = re.compile(r"\b(sign in|log ?in|create (an |your )?account|sign up|register to apply|verify your email)\b", re.I)


class Watch:
    """A visible browser on the user's computer: each job opens in a new tab, Inkwell fills and submits, and pauses
    with a bar at the bottom of the page whenever the user is needed (sign-in, account creation, questions, CAPTCHA)."""

    def __init__(self, pw, uid):
        self.uid = uid
        profile = C().DATA / "apply-browser"  # remembers sign-ins between runs, on this computer only
        profile.mkdir(parents=True, exist_ok=True)
        self.ctx = pw.chromium.launch_persistent_context(str(profile), headless=False, slow_mo=140, viewport=None,
                                                         user_agent=C().USER_AGENT, args=["--start-maximized"])
        self.ctx.add_init_script(OVERLAY_JS)
        self.stop = False

    def bar(self, page, msg, buttons=()):
        try:
            page.evaluate("([m, b]) => { window.__ro_show && window.__ro_show(m, b) }", [msg, list(buttons)])
        except Exception:
            pass

    def action(self, page):
        try:
            a = page.evaluate("() => { const a = window.__ro_action || ''; window.__ro_action = ''; return a }")
        except Exception:
            return ""
        return a

    def wait_for_user(self, page, live, msg, done=None, minutes=30):
        """Show the bar and wait until the user presses Continue/Skip, `done(page)` becomes true, or the tab closes."""
        live(page, "Waiting for you: " + msg)
        end = time.time() + minutes * 60
        while time.time() < end:
            if page.is_closed():
                return "closed"
            self.bar(page, msg, [("continue", "Continue"), ("skip", "Skip this job")])
            a = self.action(page)
            if a in ("continue", "skip"):
                return a
            try:
                if done and done(page):
                    return "done"
            except Exception:
                pass
            page.wait_for_timeout(1000)
            if int(time.time()) % 2 == 0:  # keep the live view in Inkwell close to what the browser shows
                live(page, "")
        return "timeout"


def body_text(page):
    """The page's text, or "" while it is navigating (e.g. right after the user pressed Submit themselves)."""
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def quiet(page, js):
    try:
        return page.evaluate(js)
    except Exception:
        return None


def login_wall(page):
    """A sign-in or sign-up step stands between us and the form (a password box, no upload field)."""
    try:
        if page.locator("input[type=password]:visible").count():
            return True
        return not page.locator("input[type=file]").count() and bool(LOGIN_WALL.search(page.locator("body").inner_text(timeout=3000)[:4000]))
    except Exception:
        return False


def watch_apply(w, ws, job, values, path):
    """Apply to one job in a new visible tab, pausing for the user where needed. Returns the result dict."""
    if is_blocked_board(ws, job):
        return {**BLOCKED_RESULT, "url": job["url"]}
    live = Live(ws.uid, job)
    url = apply_url(job)
    page = w.ctx.new_page()
    page.bring_to_front()
    try:
        if not open_form(page, job, url, live):
            live(page, "This job is no longer open")
            page.wait_for_timeout(2500)
            return {"state": "closed", "url": url, "detail": "This job is no longer open."}
        w.bar(page, "Filling this application for you…")
        if siteapply.mode(job) in ("site", "account"):  # the company's own site: find the form behind its Apply button
            found, button = siteapply.find_form(page, live, ws)
            if found == "none" and not siteapply.has_form(page):
                a = w.wait_for_user(page, live, "Couldn't find the application form. Open it on this page (press its Apply button), "
                                    "then press Continue.", done=siteapply.has_form)
                if a in ("skip", "closed", "timeout"):
                    siteapply.remember(ws, url, "no_form")
                    return {"state": "needs_you", "url": url, "detail": "Couldn't find the application form."}
                found = "form"
            siteapply.remember(ws, url, "needs_account" if found == "account" else "form", button)
            url = page.url
        if login_wall(page):
            a = w.wait_for_user(page, live, "This site needs you to sign in or create an account. Do it here, then press Continue.",
                                done=lambda p: not login_wall(p) and p.locator("input[type=file]").count() > 0)
            if a in ("skip", "closed", "timeout"):
                return {"state": "needs_you", "url": url, "detail": "Skipped at the sign-in step."}
            if not page.locator("input[type=file]").count():
                open_form(page, job, url, live)
        filled, missing = fill_form(page, values, path, live, ws.load("ai_answers", {}), job.get("company", ""),
                                    values.get("_covers", {}).get(job["id"]) or values.get("_cover"))
        used = [f for f in filled if f.startswith("answer: ")]
        if used:
            w.bar(page, f"Used {len(used)} of your saved answers")
        if "Resume" not in filled:
            a = w.wait_for_user(page, live, "Attach your resume in this form, then press Continue.")
            if a in ("skip", "closed", "timeout"):
                return {"state": "needs_you", "url": url, "detail": "Couldn't find where to attach your resume."}
        while True:
            if CONFIRMED.search(body_text(page)):  # the user already submitted it themselves
                live(page, "Submitted ✓")
                return {"state": "applied", "url": url, "detail": "You submitted it in the Inkwell window."}
            quiet(page, "() => document.querySelectorAll('[style*=\"e5484d\"]').forEach(e => e.style.outline = '')")
            try:
                left = len(questions_left(page, mark=True))
            except Exception:  # the page is changing; look again in a moment
                page.wait_for_timeout(1500)
                continue
            if left == 0 and not challenge_visible(page):
                break
            quiet(page, "() => document.querySelector('[style*=\"e5484d\"]')?.scrollIntoView({behavior: 'smooth', block: 'center'})")
            msg = (f"{left} question{'s' if left != 1 else ''} only you can answer (outlined in red). Answer them, then press Continue."
                   if left else "Solve the CAPTCHA, then press Continue.")
            snap = {"n": 0}

            def submitted(p, snap=snap):
                body = body_text(p)
                if CONFIRMED.search(body) or BLOCKED.search(body):
                    return True
                snap["n"] += 1
                if snap["n"] % 5 == 0:  # keep a recent copy of what the user typed, in case they submit themselves
                    learn_from_page(ws, p, job)
                return False
            a = w.wait_for_user(page, live, msg, done=submitted)
            if a == "continue":
                n = learn_from_page(ws, page, job)
                if n:
                    live(page, f"Saved {n} of your answers for review")
            if a == "done":
                if BLOCKED.search(body_text(page)):
                    mark_blocked(ws, job)
                    live(page, "Stopped: the site flagged the automated submission")
                    return {**BLOCKED_RESULT, "url": job["url"]}
                live(page, "Submitted ✓")
                return {"state": "applied", "url": url, "detail": "You submitted it in the Inkwell window."}
            if a in ("skip", "closed", "timeout"):
                return {"state": "needs_you", "url": url, "questions": missing[:40], "detail": "Skipped: questions were left unanswered."}
        btn = page.locator("button[type=submit]:visible, input[type=submit]:visible, button:visible:has-text('Submit application'), "
                           "button:visible:has-text('Submit')").first
        if not btn.count():
            a = w.wait_for_user(page, live, "Couldn't find the Submit button. Submit the form yourself, or press Continue to move on.",
                                done=lambda p: bool(CONFIRMED.search(body_text(p))))
            if a == "done":
                return {"state": "applied", "url": url, "detail": "You submitted it in the Inkwell window."}
            return {"state": "needs_you", "url": url, "detail": "Couldn't find the Submit button. Submit it on the site."}
        w.bar(page, "Submitting your application…")
        live(page, "Submitting…")
        btn.scroll_into_view_if_needed(timeout=5000)
        btn.click(timeout=10000)
        for _ in range(12):  # wait for the confirmation (or an error) instead of a fixed pause
            page.wait_for_timeout(1000)
            body = body_text(page)
            if CONFIRMED.search(body) or BLOCKED.search(body) or CODE_STEP.search(body):
                break
        if CODE_STEP.search(body) and not CONFIRMED.search(body):  # e.g. Greenhouse emails a security code to the applicant
            a = w.wait_for_user(page, live, "The site emailed you a security code. Enter it here and submit, then Inkwell moves on.",
                                done=lambda p: bool(CONFIRMED.search(body_text(p))))
            if a == "done":
                live(page, "Submitted ✓")
                return {"state": "applied", "url": url, "detail": "Application submitted (you entered the emailed code)."}
            return {"state": "needs_you", "url": url, "detail": "The site asked for the security code it emailed you. Finish it on the site."}
        if BLOCKED.search(body):
            mark_blocked(ws, job)
            w.bar(page, "This site flagged the automated submission. Inkwell stopped; apply from your own browser.")
            live(page, "Stopped: the site flagged the automated submission")
            page.wait_for_timeout(4000)
            return {**BLOCKED_RESULT, "url": job["url"]}
        if CONFIRMED.search(body):
            w.bar(page, "Submitted ✓ Moving on…")
            live(page, "Submitted ✓")
            page.wait_for_timeout(2000)
            return {"state": "applied", "url": url, "detail": "Application submitted."}
        a = w.wait_for_user(page, live, "No confirmation yet. Fix anything the form points out and submit, or press Continue to move on.",
                            done=lambda p: bool(CONFIRMED.search(body_text(p))))
        if a == "done":
            return {"state": "applied", "url": url, "detail": "Application submitted."}
        return {"state": "needs_you", "url": url, "detail": "Submitted, but no confirmation appeared. Check the page."}
    finally:
        try:
            page.close()
        except Exception:
            pass


def run_watch(op, uid, picks):
    """Visible mode on the user's computer: one browser window, a tab per job, pausing for the user when needed."""
    from playwright.sync_api import sync_playwright
    ws = C().Workspace(uid)
    values = apply_values(ws)
    resume, data = resume_file(ws)
    try:
        with tempfile.TemporaryDirectory(prefix="reachout-apply-") as tmp, APPLY_LOCK, sync_playwright() as pw:
            path = os.path.join(tmp, re.sub(r"[^\w.\-]", "_", resume) or "resume.pdf")
            with open(path, "wb") as f:
                f.write(data or b"")
            values["_cover"] = write_cover(ws, tmp)
            values["_covers"] = {}
            for job in picks:  # letters written for one job
                from features.ai_jobs.evaluate import job_cover_doc
                if job_cover_doc(ws, job):
                    sub = os.path.join(tmp, f"cover-{len(values['_covers'])}")
                    os.makedirs(sub, exist_ok=True)
                    values["_covers"][job["id"]] = write_cover(ws, sub, job)
            paths = {}
            for job in picks:  # resumes tailored for one job
                name, own = resume_file(ws, job)
                if own and name != resume:
                    paths[job["id"]] = os.path.join(tmp, f"r{len(paths)}-" + (re.sub(r"[^\w.\-]", "_", name) or "resume.pdf"))
                    with open(paths[job["id"]], "wb") as f:
                        f.write(own)
            w = Watch(pw, uid)
            try:
                for job in picks:
                    op.checkpoint()  # before the next form: stops here when cancelled
                    op.update(private={"current": job["title"]})
                    try:
                        res = closed_result(job) or watch_apply(w, ws, job, values, paths.get(job["id"], path))
                    except Exception as e:
                        print(f"[Inkwell] watch apply error ({job.get('company')}): {e!r}", flush=True)
                        from infra.redact import error_text
                        res = {"state": "needs_you", "url": job["url"], "error": error_text(e, 300),
                               "detail": "The window was closed or the page changed. Apply on the site."}
                    res["at"] = time.time()
                    with ws.lock:
                        done = ws.load("ai_applied", {})
                        done[job["id"]] = res
                        ws.save("ai_applied", done)
                    if siteapply.mode(job) == "site" and res["state"] == "applied":
                        siteapply.remember(ws, res.get("url") or job["url"], "applied")
                    if res["state"] == "applied":
                        record_application(ws, job)
                    if res["state"] in ("applied", "closed"):
                        from features.ai_jobs.tracker import set_status
                        set_status(ws, job["id"], "applied" if res["state"] == "applied" else "discarded",
                                   note="" if res["state"] == "applied" else "Posting closed")
                    op.update(inc={"done": 1})
            finally:
                try:
                    w.ctx.close()
                except Exception:
                    pass
    finally:
        HANDOVER.pop(uid, None)
        from features.notify import notify
        ok = sum(1 for j in picks if (ws.load("ai_applied", {}).get(j["id"]) or {}).get("state") == "applied")
        notify(uid, f"Inkwell AI applied to {ok} job{'s' if ok != 1 else ''}", f"{len(picks) - ok} still need you.",
               "/app/ai-jobs", "application")
