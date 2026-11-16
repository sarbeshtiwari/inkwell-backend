"""Auto-apply helpers: resume and cover letter, supported sites, opening a form, applying in the background."""

import os
import re
import tempfile
import time
from datetime import datetime
from urllib.parse import urlsplit

from features.ai_jobs.common import APPLY_LOCK, C, Live
from features.ai_jobs import siteapply
from features.ai_jobs.forms import COUNTRIES, challenge_visible, fill_form


APPLY_HOSTS = {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.greenhouse.io", "boards.eu.greenhouse.io",
               "jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}


def apply_url(job):
    """The job board's own application form. Many companies show their Greenhouse jobs on their own careers site
    (coinbase.com/careers/…), so Greenhouse jobs always go to Greenhouse's hosted form for that job instead."""
    if job["ats"] == "greenhouse":
        _, board, jid = job["id"].split(":", 2)
        eu = ".eu" if ".eu.greenhouse.io" in job.get("url", "") else ""
        return f"https://job-boards{eu}.greenhouse.io/embed/job_app?for={board}&token={jid}"
    if job["ats"] == "lever":
        return job.get("apply_url") or job["url"].rstrip("/") + "/apply"
    if job["ats"] == "ashby":
        return job["url"].rstrip("/") + "/application"
    return job.get("apply_url") or job["url"]


def apply_values(ws):
    prof, extra = ws.profile(), ws.load("ai_apply_profile", {})
    name = prof.get("name", "").strip()
    first, _, last = name.partition(" ")
    dial = str(ws.settings().get("country_code") or "").lstrip("+")
    return {"name": name, "first": first, "last": last or first, "email": prof.get("email") or C().find_user(uid=ws.uid)["email"],
            "phone": prof.get("phone", ""), "dial": dial, "country": COUNTRIES.get(dial, ""), **extra}


COVER_DOC = re.compile(r"\.(pdf|docx?|txt|rtf)$", re.I)


def cover_file(ws, job=None):
    """(name, bytes) of the cover letter to attach, or ("", None). A letter written for this job wins; otherwise
    'auto' = a file in Files with "cover" in its name."""
    names = [d["name"] for d in ws.documents()]
    if job:
        from features.ai_jobs.evaluate import job_cover_doc
        mine = job_cover_doc(ws, job)
        if mine in names:
            return mine, ws.doc_bytes(mine)
    choice = (ws.load("ai_apply_profile", {}) or {}).get("cover_letter", "auto")
    if choice == "none":
        return "", None
    name = choice if choice in names else next((n for n in names if "cover" in n.lower() and COVER_DOC.search(n)), "")
    return (name, ws.doc_bytes(name)) if name else ("", None)


def cover_text(name, data):
    """The letter's text, for forms that ask you to paste it instead of attaching a file."""
    import io
    if not data:
        return ""
    try:
        if name.lower().endswith(".pdf"):
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
        elif name.lower().endswith(".txt"):
            text = data.decode("utf-8", errors="replace")
        else:
            return ""
    except Exception:
        return ""
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()[:6000]


def write_cover(ws, tmp, job=None):
    name, data = cover_file(ws, job)
    if not data:
        return None
    path = os.path.join(tmp, re.sub(r"[^\w.\-]", "_", name) or "cover-letter.pdf")
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path, "text": cover_text(name, data), "name": name}


def resume_file(ws, job=None):
    """(name, bytes) of the resume to send: the one tailored for this job when you chose to use it, else your resume."""
    if job:
        from features.ai_jobs.extras import job_resume_doc
        mine = job_resume_doc(ws, job)
        data = ws.doc_bytes(mine) if mine else None
        if data:
            return mine, data
    resume = ws.load("ai_match", {}).get("want", {}).get("resume") or next(
        (d["name"] for d in ws.documents() if d["name"].lower().endswith(".pdf") and "cover" not in d["name"].lower()), "")
    return resume, (ws.doc_bytes(resume) if resume else None)


def can_hand_over():
    """A visible browser window can only be opened when Reachout runs on the user's own computer."""
    import platform
    return not C().PRODUCTION and (platform.system() in ("Darwin", "Windows") or bool(os.environ.get("DISPLAY")))


# The site decided the submission came from an automated browser. Reachout respects that: it stops, never retries or
# hides itself, remembers the site, and hands the user a kit to apply from their own browser.
BLOCKED = re.compile(r"flagged as (possible )?spam|couldn.t submit your application|unusual (activity|traffic)|"
                     r"automated (requests|traffic|submission)|are you a (robot|human)|verify you are human|access denied", re.I)


BLOCKED_RESULT = {"state": "blocked", "detail": "This site flagged the automated submission. Apply from your own browser; "
                                                "Reachout has your answers ready to copy."}


def board_key(job):
    return f"{job.get('ats', '')}:{job.get('board', '')}"


def mark_blocked(ws, job):
    with ws.lock:
        b = ws.load("ai_blocked_boards", {})
        b[board_key(job)] = time.time()
        ws.save("ai_blocked_boards", b)


def is_blocked_board(ws, job):
    return board_key(job) in ws.load("ai_blocked_boards", {})


CONFIRMED = re.compile(r"thank you for applying|thanks for applying|thank(s| you) for your application|"
                       r"application (was |has been )?(successfully )?(received|submitted)|"
                       r"we('ve| have) received your application|successfully submitted", re.I)


CODE_STEP = re.compile(r"(security|verification|confirmation) code|code (was |we )?sent to|enter the code", re.I)


def open_form(page, job, url, live):
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    live(page, "Opened the application form")
    try:  # the form only reacts to a file or typing once its scripts have loaded
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_selector("input[type=file]", state="attached", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1000)
    if re.search(r"page not found|no longer (open|available|accepting)|position has been filled|job (is )?closed",
                 page.locator("body").inner_text()[:3000], re.I):
        return False
    if job["ats"] == "greenhouse" and page.locator("button:has-text('Apply')").count() and not page.locator("input[type=file]").count():
        page.locator("button:has-text('Apply')").first.click(); page.wait_for_timeout(1500)
    return True


def apply_one(ws, job, submit=True, visible=False):
    """Open the job's public application form, fill it and (when nothing is left to answer) submit it.

    visible=True (only when Reachout runs on the user's computer) does it in a browser window they can watch.
    On a company's own site the outcome is remembered for that site (siteapply.py)."""
    seen = {}
    res = _apply_one(ws, job, submit, visible, seen)
    if siteapply.mode(job) == "site" and seen.get("url"):
        outcome = {"applied": "applied", "blocked": "blocked", "closed": "closed"}.get(res.get("state"), seen.get("outcome", "needs_you"))
        siteapply.remember(ws, seen["url"], outcome, seen.get("button", ""))
    return res


ACCOUNT_RESULT = ("This site makes you sign in or create an account before applying. Use Watch mode to sign in yourself "
                  "and Reachout fills the rest, or apply on the site.")


def _apply_one(ws, job, submit, visible, seen):
    from playwright.sync_api import sync_playwright
    if is_blocked_board(ws, job):
        return {**BLOCKED_RESULT, "url": job["url"]}
    values = apply_values(ws)
    resume, data = resume_file(ws, job)
    if not data:
        return {"state": "needs_you", "detail": "Upload your resume as a PDF first."}
    url = apply_url(job)
    from features.finder import host_is_public
    how = siteapply.mode(job)
    if not how or not host_is_public(urlsplit(url).hostname or "") or (how == "board" and (urlsplit(url).hostname or "") not in APPLY_HOSTS):
        return {"state": "needs_you", "detail": "Reachout can't apply on this site. Apply on the site itself.", "url": url}
    if how == "account" or (how == "site" and siteapply.known_account_site(ws, url)):
        return {"state": "needs_you", "url": url, "detail": ACCOUNT_RESULT}
    live = Live(ws.uid, job)
    with tempfile.TemporaryDirectory(prefix="reachout-apply-") as tmp:
        path = os.path.join(tmp, re.sub(r"[^\w.\-]", "_", resume) or "resume.pdf")
        with open(path, "wb") as f:
            f.write(data)
        cover = write_cover(ws, tmp, job)
        with APPLY_LOCK, sync_playwright() as pw:
            args = ["--no-sandbox"] if C().NO_SANDBOX else []
            browser = pw.chromium.launch(headless=not (visible and can_hand_over()), args=args, slow_mo=120 if visible else 0)
            try:
                page = browser.new_page(user_agent=C().USER_AGENT, viewport={"width": 1100, "height": 860})
                if not open_form(page, job, url, live):
                    live(page, "This job is no longer open")
                    return {"state": "closed", "url": url, "detail": "This job is no longer open."}
                if how == "site":
                    seen["url"] = url
                    found, seen["button"] = siteapply.find_form(page, live, ws)
                    url = page.url
                    if found == "account":
                        seen["outcome"] = "needs_account"
                        live(page, "Stopped: this site wants you to sign in")
                        return {"state": "needs_you", "url": url, "detail": ACCOUNT_RESULT}
                    if found == "none":
                        seen["outcome"] = "no_form"
                        live(page, "Couldn't find the application form")
                        return {"state": "needs_you", "url": url, "detail": "Couldn't find an application form on this job's page. Apply on the site."}
                filled, missing = fill_form(page, values, path, live, ws.load("ai_answers", {}), job.get("company", ""), cover)
                filled = list(dict.fromkeys(re.sub(r"\s+", " ", f).strip() for f in filled))
                if "Resume" not in filled:
                    live(page, "Couldn't find where to attach your resume")
                    return {"state": "needs_you", "detail": "Couldn't find where to attach your resume.", "url": url}
                if missing:
                    more = f" and {len(missing) - 4} more" if len(missing) > 4 else ""
                    live(page, f"Stopped: {len(missing)} question{'s' if len(missing) != 1 else ''} only you can answer")
                    return {"state": "needs_you", "url": url, "questions": missing[:40], "filled": filled,
                            "detail": f"Filled {len(filled)} fields. Questions only you can answer: " + "; ".join(missing[:4]) + more}
                if challenge_visible(page):
                    live(page, "Stopped: the form shows a CAPTCHA")
                    return {"state": "needs_you", "url": url, "detail": "The form shows a CAPTCHA, so finish it yourself."}
                if not submit:
                    live(page, "Everything is filled")
                    return {"state": "ready", "url": url, "detail": f"Filled: {', '.join(filled)}."}
                btn = page.locator("button[type=submit]:visible, input[type=submit]:visible, button:visible:has-text('Submit application'), "
                                   "button:visible:has-text('Submit')").first
                if not btn.count():
                    return {"state": "needs_you", "url": url, "detail": "Everything is filled, but there's no Submit button to press. Submit it on the site."}
                btn.scroll_into_view_if_needed(timeout=5000)
                live(page, "Submitting…")
                btn.click(timeout=10000)
                body = ""
                for _ in range(12):  # wait for the confirmation (or an error) instead of a fixed pause
                    page.wait_for_timeout(1000)
                    try:
                        body = page.locator("body").inner_text(timeout=5000)
                    except Exception:
                        continue
                    if CONFIRMED.search(body) or BLOCKED.search(body) or CODE_STEP.search(body):
                        break
                live(page, "")
                if CODE_STEP.search(body) and not CONFIRMED.search(body):
                    live(page, "Stopped: the site emailed you a security code")
                    return {"state": "needs_you", "url": url, "detail": "The site emailed you a security code to finish. Use Watch mode, "
                            "or apply on the site."}
                if CONFIRMED.search(body):
                    live(page, "Submitted ✓")
                    return {"state": "applied", "url": url, "detail": "Application submitted."}
                if BLOCKED.search(body):
                    live(page, "Stopped: the site flagged the automated submission")
                    mark_blocked(ws, job)
                    return {**BLOCKED_RESULT, "url": job["url"]}
                if challenge_visible(page):
                    live(page, "Stopped: a CAPTCHA appeared on submit")
                    return {"state": "needs_you", "url": url, "detail": "A CAPTCHA appeared on submit, so finish it yourself."}
                return {"state": "needs_you", "url": url, "detail": "Submitted, but no confirmation appeared. Check the page."}
            finally:
                browser.close()


def closed_result(job):
    """Liveness gate (career-ops): never spend an application on a posting that has closed. None when it looks open."""
    from features.ai_jobs.signals import liveness
    try:
        live = liveness(job)
    except Exception:
        return None
    if live["state"] == "expired":
        return {"state": "closed", "url": job["url"], "detail": f"This job is no longer open: {live['reason'].lower()}."}
    return None


def record_application(ws, job):
    from features import apps
    with ws.lock:
        rows = ws.load("applications", {})
        aid = apps.app_id(job["company"], job["title"], job["id"])
        now = datetime.now().isoformat(timespec="seconds")
        rows[aid] = {"id": aid, "company": job["company"], "role": job["title"], "job_id": "", "url": job["url"],
                     "portal": f"Auto-apply ({job['ats'].capitalize() or job.get('via') or siteapply.host(job['url'])})", "created": now,
                     "history": [{"status": "applied", "at": now, "source": "you", "subject": "Applied by Reachout AI"}]}
        apps.recompute(rows[aid])
        ws.save("applications", rows)
