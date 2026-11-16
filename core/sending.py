"""Send jobs: planning and running WhatsApp/email campaigns in the background."""

import random
import secrets
import shutil
import smtplib
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import whatsapp as wa

from core.bounces import BOUNCE_CHECK_SOON, domain_problem
from core.config import DAILY_LIMIT, LOGGED_IN, MIN_WA_DELAY, WHATSAPP_ENABLED
from core.crypto import lookup_hash
from core.mail import build_email, smtp_connect
from core.pages import can_track_opens, site_url
from core.tables import phone_key, render
from core.validation import EMAIL_RE, Invalid, v_int, v_template_text
from core.whatsapp_store import browser_slot, open_whatsapp, wa_profile
from core.workspace import DELETED, bridge_mod


class Job:
    def __init__(self):
        self.stop = threading.Event()
        self.lines = []
        self.done = self.total = 0
        self.running = False
        self.sent = self.failed = self.skipped = 0
        self.current = ""        # who is being sent to right now
        self.phase = "starting"  # starting | sending | waiting | stopping | done | stopped | error
        self.next_at = None      # epoch seconds of the next send while waiting
        self.started = time.time()
        self.finished = None
        self.dry_run = False
        self.channels = []

    def say(self, text):
        self.lines.append(f"{datetime.now():%H:%M:%S}  {text}")
        self.lines = self.lines[-500:]

    def snapshot(self):
        return {"running": self.running, "done": self.done, "total": self.total, "lines": self.lines,
                "sent": self.sent, "failed": self.failed, "skipped": self.skipped, "current": self.current,
                "phase": self.phase, "next_at": self.next_at, "started": self.started, "finished": self.finished,
                "now": time.time(), "dry_run": self.dry_run, "channels": self.channels}


JOBS = {}


JOBS_LOCK = threading.Lock()


class Cleanup:
    """Runs cleanups in reverse order, ignoring their errors (a dead browser shouldn't mask the real error)."""

    def __init__(self):
        self.fns = []

    def enter(self, cm):
        value = cm.__enter__()
        self.fns.append(lambda: cm.__exit__(None, None, None))
        return value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for fn in reversed(self.fns):
            try:
                fn()
            except Exception:
                pass
        return False


def run_job(ws, job, cfg):
    try:
        _run_job(ws, job, cfg)
        job.phase = "stopped" if job.stop.is_set() else "done"
    except Exception as e:
        job.say(f"Error: {e}")
        job.phase, job.current = "error", str(e)
    finally:
        job.next_at, job.finished = None, time.time()
        job.running = False
        job.say("Finished.")
        if getattr(bridge_mod(), "notify", None) and not cfg.get("dry_run"):
            title = {"done": "Campaign finished", "stopped": "Campaign stopped", "error": "Campaign stopped with an error"}.get(job.phase, "Campaign finished")
            bridge_mod().notify(ws.uid, title, f"{job.done} of {job.total} sent." + (f" {job.current}" if job.phase == "error" else ""),
                                "#activity", "campaign")


def plan_job(ws, cfg):
    """Work list: (recipient, [channels still to do]); skips done ones, duplicates, and the daily cap."""
    cc = ws.settings()["country_code"]
    channels = ["whatsapp", "email"] if cfg["mode"] == "both" else [cfg["mode"]]
    rows = ws.load("recipients", [])
    if cfg["who"] == "selected":
        wanted = set(cfg["recipient_ids"])
        rows = [r for r in rows if r["id"] in wanted]
    todo, seen = [], set()
    for r in rows:
        chans = []
        for ch in channels:
            if ch == "whatsapp":
                key = phone_key(r, cc)
                if len(key) < 8 or ("wa", key) in seen:
                    continue
                if not cfg["resend"] and r.get("wa_status") in ("sent", "not_on_whatsapp"):
                    continue
                seen.add(("wa", key))
            else:
                key = r.get("email", "").strip().lower()
                if not EMAIL_RE.match(key) or ("em", key) in seen:
                    continue
                if r.get("email_status") in ("bounced", "invalid"):
                    continue  # address known to be undeliverable: never retried
                if not cfg["resend"] and r.get("email_status") == "sent":
                    continue
                if r.get("replied_at") and cfg["who"] != "selected":
                    continue  # they answered: follow up personally, not with a bulk send
                seen.add(("em", key))
            chans.append(ch)
        if chans:
            todo.append((r, chans))
    return channels, todo[: cfg["limit"]]


def _run_job(ws, job, cfg):
    cc = ws.settings()["country_code"]
    profile = ws.profile()
    channels, todo = plan_job(ws, cfg)
    if not todo:
        job.say("Nothing to send – everyone matching has been contacted already, or has no phone/email.")
        return
    if not cfg["dry_run"]:
        budget, capped, used = DAILY_LIMIT - ws.sent_today(), [], 0
        for r, chans in todo:
            if used + len(chans) > budget:
                break
            capped.append((r, chans))
            used += len(chans)
        if not capped:
            job.say(f"You've reached today's limit of {DAILY_LIMIT} messages. Try again tomorrow.")
            return
        todo = capped
    job.total = len(todo)
    job.dry_run, job.channels = cfg["dry_run"], channels
    min_delay = max(cfg["min_delay"], MIN_WA_DELAY) if "whatsapp" in channels else cfg["min_delay"]
    max_delay = max(min_delay, cfg["max_delay"])
    job.say(f"{len(todo)} people · {' + '.join(channels)} · {len(cfg['documents'])} attachment(s)"
            + (" · TEST RUN (nothing is sent)" if cfg["dry_run"] else ""))

    if cfg["dry_run"]:
        for r, chans in todo:
            job.say(f"— {r.get('name') or '(no name)'} · {r.get('company') or ''} → {', '.join(chans)}")
            if "email" in chans:
                job.say(f"   Subject: {render(cfg['subject'], r, profile)}")
            for line in render(cfg["body"], r, profile).splitlines():
                job.say(f"   {line}")
            job.done += 1
            job.sent += 1
        return

    with Cleanup() as cleanup:
        # Attachments are decrypted into a private temp folder only for the length of the run.
        tmp = Path(tempfile.mkdtemp(prefix="reachout-files-"))
        tmp.chmod(0o700)
        cleanup.fns.append(lambda: shutil.rmtree(tmp, ignore_errors=True))
        files = []
        for name in cfg["documents"]:
            data = ws.doc_bytes(name)
            if data is not None:
                (tmp / name).write_bytes(data)
                files.append(tmp / name)

        page = smtp = None
        if any("whatsapp" in c for _, c in todo):
            from playwright.sync_api import sync_playwright
            cleanup.enter(browser_slot(ws.uid, job.say))
            folder = cleanup.enter(wa_profile(ws))
            pw = cleanup.enter(sync_playwright())
            job.say("Opening WhatsApp…")
            ctx, page = open_whatsapp(pw, folder)
            cleanup.fns.append(ctx.close)
            try:
                page.wait_for_selector(LOGGED_IN, timeout=90_000)
            except Exception:
                ws.update_settings(wa_connected=False)
                raise RuntimeError("WhatsApp is logged out. Link it again on the WhatsApp page.")
            job.say("WhatsApp ready.")
        domain_cache = {}
        if any("email" in c for _, c in todo):
            smtp = smtp_connect(profile)
            cleanup.fns.append(lambda: smtp.quit())
            job.say(f"Connected to email as {profile['email']}.")

        for i, (r, chans) in enumerate(todo, 1):
            if job.stop.is_set():
                job.say("Stopped.")
                break
            if DELETED.get(ws.uid):
                break
            # Re-check the day's limit before every person: replies and scheduled emails sent while the
            # campaign runs count too.
            if ws.sent_today() + len(chans) > DAILY_LIMIT:
                job.say(f"Reached today's limit of {DAILY_LIMIT} messages. The rest can go tomorrow.")
                break
            who = r.get("name") or r.get("company") or r.get("phone") or r.get("email")
            job.phase, job.current, job.next_at = "sending", who, None
            text = render(cfg["body"], r, profile)
            for ch in chans:
                if ch == "whatsapp":
                    phone = phone_key(r, cc)
                    to = "+" + phone
                    try:
                        wa.send_one(page, phone, text, files)
                        status, detail = "sent", ""
                    except wa.NotOnWhatsApp:
                        status, detail = "not_on_whatsapp", ""
                    except Exception as e:
                        status, detail = "failed", str(e).splitlines()[0]
                else:
                    to = r["email"].strip()
                    token = secrets.token_urlsafe(18) if cfg.get("track_opens") else None
                    pixel = f"{cfg['public_url']}/o/{token}.gif" if token else None
                    msg = None
                    status, detail = "invalid", domain_problem(to.split("@")[-1], domain_cache)
                    if not detail:
                        try:
                            msg = build_email(profile, to, render(cfg["subject"], r, profile), text, files, pixel)
                            try:
                                smtp.send_message(msg)
                            except smtplib.SMTPServerDisconnected:
                                smtp = smtp_connect(profile)
                                smtp.send_message(msg)
                            status, detail = "sent", ""
                        except smtplib.SMTPRecipientsRefused as e:
                            code, why = next(iter(e.recipients.values()), (0, b""))
                            status = "bounced"
                            detail = f"Rejected by the mail server: {code} {why.decode(errors='replace')[:160]}"
                        except Exception as e:
                            status, detail = "failed", str(e).splitlines()[0]
                ws.set_status(r["id"], ch, status)
                ws.log(ch, to, r.get("name", ""), status, detail, rid=r["id"],
                       preview=render(cfg["subject"], r, profile) if ch == "email" else text,
                       track=lookup_hash("open:" + token) if ch == "email" and status == "sent" and token else None,
                       message_id=msg["Message-ID"] if ch == "email" and status == "sent" and msg else None)
                job.say(f"[{i}/{len(todo)}] {who} · {ch} → {status}" + (f" ({detail})" if detail else ""))
                if status == "sent":
                    job.sent += 1
                elif status in ("not_on_whatsapp", "invalid"):
                    job.skipped += 1
                else:
                    job.failed += 1
            job.done = i
            if i == len(todo) and any("email" in c for _, c in todo):
                BOUNCE_CHECK_SOON[ws.uid] = time.time() + 120  # look for bounce notices shortly after the run
            if i < len(todo) and not job.stop.is_set():
                wait = random.randint(min_delay, max_delay)
                job.say(f"Waiting {wait}s…")
                job.phase, job.current, job.next_at = "waiting", "", time.time() + wait
                job.stop.wait(wait)


def validate_send(ws, p):
    cfg = {
        "mode": p.get("mode"), "who": p.get("who"),
        "subject": "", "body": v_template_text(p.get("body"), "body", "Message", 5000, True),
        "limit": v_int(p.get("limit"), "limit", "Max people", 1, 500),
        "min_delay": v_int(p.get("min_delay"), "min_delay", "Min gap", 0, 3600),
        "max_delay": v_int(p.get("max_delay"), "max_delay", "Max gap", 0, 3600),
        "resend": bool(p.get("resend")), "dry_run": bool(p.get("dry_run")),
        "track_opens": bool(p.get("track_opens")), "public_url": site_url(),
        "template_id": str(p.get("template_id") or ""), "recipient_ids": [], "documents": [],
    }
    if cfg["mode"] not in ("whatsapp", "email", "both"):
        raise Invalid("Choose WhatsApp, email or both.", "mode")
    if cfg["mode"] != "email" and not WHATSAPP_ENABLED:
        raise Invalid("WhatsApp sending isn't available here. Send by email instead.", "mode")
    if cfg["who"] not in ("all", "selected"):
        raise Invalid("Choose who to send to.", "who")
    if cfg["track_opens"] and cfg["mode"] != "whatsapp" and not can_track_opens():
        raise Invalid("Open tracking only works once the app is online at a public address (set SITE_URL).", "track_opens")
    if cfg["max_delay"] < cfg["min_delay"]:
        raise Invalid("Max gap must be the same as or more than the min gap.", "max_delay")
    if cfg["mode"] != "whatsapp":
        cfg["subject"] = v_template_text(p.get("subject"), "subject", "Email subject", 200, True)
    if cfg["who"] == "selected":
        ids = p.get("recipient_ids")
        if not isinstance(ids, list) or not ids:
            raise Invalid("Select contacts on the Contacts page first.", "who")
        cfg["recipient_ids"] = [str(i) for i in ids]
    docs = p.get("documents") or []
    names = {d["name"] for d in ws.documents()}
    if not isinstance(docs, list) or any(d not in names for d in docs):
        raise Invalid("One of the selected files no longer exists. Refresh the page.", "documents")
    cfg["documents"] = docs
    if not cfg["dry_run"]:
        if cfg["mode"] != "whatsapp" and not ws.profile().get("smtp_password"):
            raise Invalid("Set up email on the Profile page before sending email.", "mode")
        if cfg["mode"] != "email" and not ws.settings().get("wa_connected"):
            raise Invalid("Link your WhatsApp first.", "mode")
    if not plan_job(ws, cfg)[1]:
        raise Invalid("Nobody to send to: everyone matching has been contacted already, or has no phone/email.",
                      "who")
    return cfg
