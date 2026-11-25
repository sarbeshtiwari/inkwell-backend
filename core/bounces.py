"""Deliverability: domain checks before sending and bounce detection afterwards."""

import email as email_lib
import hashlib
import re
import time
from datetime import date, datetime, timedelta
from email.policy import default as email_policy

import dns.exception
import dns.resolver

from core.crypto import lookup_hash, seal, unseal
from core.db import M
from core.mail import imap_connect
from core.validation import Invalid
from core.workspace import Workspace, bridge_mod


def domain_problem(domain, cache):
    """Return a reason if the domain can't receive mail at all (checked once per domain per run)."""
    domain = domain.lower().strip(".")
    if domain in cache:
        return cache[domain]
    reason = ""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=6)
        if all(str(a.exchange).strip(".") == "" for a in answers):
            reason = "This domain says it doesn't accept email (null MX)."
    except dns.resolver.NXDOMAIN:
        reason = "This email's domain doesn't exist."
    except dns.resolver.NoAnswer:
        try:  # no MX: mail may still go to the domain's own address
            dns.resolver.resolve(domain, "A", lifetime=6)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            reason = "This email's domain has no mail server."
        except dns.exception.DNSException:
            pass
    except dns.exception.DNSException:
        pass  # DNS hiccup: don't block the send on a lookup failure
    cache[domain] = reason
    return reason


BOUNCE_REASONS = (  # (pattern, friendly reason); first match wins, most specific first
    # Microsoft 365 answers "5.4.1 Recipient address rejected: Access denied" when the address isn't in its directory.
    (r"5\.1\.1\b|5\.1\.10\b|5\.4\.1\b.*recipient address rejected|recipient address rejected: access denied|"
     r"does ?n.t exist|does not exist|no such user|user unknown|unknown user|recipient not found|address not found|"
     r"invalid recipient|mailbox unavailable|mailbox not found|not a valid|unrouteable", "Address doesn't exist"),
    (r"5\.7\.133|5\.7\.134|5\.7\.135|5\.7\.136|notauthenticatedforgroup|restricted to internal|only accepts mail from|"
     r"internal senders|not allowed to send to this (group|address)", "Internal-only address (accepts mail from their staff only)"),
    (r"5\.1\.2\b|domain not found|host not found|no mx|couldn.t be found", "Domain can't receive email"),
    (r"5\.2\.2\b|mailbox full|over quota|quota exceeded|insufficient storage", "Mailbox full"),
    (r"5\.4\.14|hop count exceeded|mail loop", "Mail loop at the recipient's server"),
    (r"5\.7\.\d+|blocked|block list|blacklist|spam|policy|denied|not authori[sz]ed|rejected|refused|access denied|restricted",
     "Blocked by the recipient's server"),
)


def bounce_reason(text):
    t = (text or "").lower()
    for pattern, label in BOUNCE_REASONS:
        if re.search(pattern, t):
            return label
    return "Couldn't be delivered"


def parse_bounce(raw):
    """From one DSN email: [(failed recipient, status code, diagnostic, original Message-ID or '')]."""
    msg = email_lib.message_from_bytes(raw, policy=email_policy)
    original_mid, failures, text_bits = "", [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            blocks = part.get_payload()
            for block in blocks[1:] if isinstance(blocks, list) else []:
                action = str(block.get("Action", "")).lower()
                rcpt = str(block.get("Final-Recipient") or block.get("Original-Recipient") or "")
                rcpt = rcpt.split(";", 1)[-1].strip().strip("<>").lower()
                if rcpt and action.startswith("failed"):
                    failures.append([rcpt, str(block.get("Status", "")), str(block.get("Diagnostic-Code", ""))])
        elif ctype in ("message/rfc822", "text/rfc822-headers") and not original_mid:
            inner = part.get_payload()
            if isinstance(inner, list) and inner:
                original_mid = str(inner[0].get("Message-ID", ""))
            else:
                m = re.search(r"^Message-ID:\s*(<[^>]+>)", part.get_content() if ctype == "text/rfc822-headers" else "",
                              re.I | re.M)
                original_mid = m.group(1) if m else ""
        elif ctype == "text/plain" and not failures:
            try:
                text_bits.append(part.get_content()[:4000])
            except Exception:
                pass
    if not failures:  # some servers only use X-Failed-Recipients plus a plain-text explanation
        text = " ".join(text_bits)
        for rcpt in re.split(r"[,\s]+", str(msg.get("X-Failed-Recipients", ""))):
            if "@" in rcpt:
                failures.append([rcpt.strip("<>").lower(), "", text[:600]])
    if not original_mid:
        m = re.search(rb"^Message-ID:\s*(<[^>]+>)", raw.split(b"\r\n\r\n", 1)[-1], re.I | re.M)
        original_mid = m.group(1).decode(errors="ignore") if m else ""
    return [(r, st, diag, original_mid) for r, st, diag in failures]


def check_bounces(ws, days=14):
    """Read bounce notices from the sender's inbox (read-only) and mark matching sends as bounced."""
    profile = ws.profile()
    if not profile.get("smtp_password"):
        raise Invalid("Set up email on the Profile page first.")
    seen = set(ws.load("bounces_seen", []))
    found, checked = [], 0
    imap = imap_connect(profile)
    try:
        imap.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        ids = set()
        for crit in ('(SINCE {d} FROM "mailer-daemon")', '(SINCE {d} FROM "postmaster")',
                     '(SINCE {d} SUBJECT "Undeliverable")', '(SINCE {d} SUBJECT "Delivery Status Notification")',
                     '(SINCE {d} SUBJECT "Mail delivery failed")', '(SINCE {d} SUBJECT "Undelivered Mail")'):
            typ, data = imap.search(None, crit.format(d=since))
            if typ == "OK" and data and data[0]:
                ids.update(data[0].split())
        for num in sorted(ids, key=int):
            typ, data = imap.fetch(num, "(BODY.PEEK[]<0.400000>)")  # PEEK: doesn't mark the notice as read; first 400 KB only
            raw = next((d[1] for d in data if isinstance(d, tuple)), None)
            if not raw:
                continue
            key = lookup_hash("dsn:" + hashlib.sha256(raw).hexdigest())
            if key in seen:
                continue
            seen.add(key)
            checked += 1
            for rcpt, status_code, diag, mid in parse_bounce(raw):
                if status_code.startswith("4"):
                    continue  # temporary: the mail server is still retrying
                row = None
                if mid:
                    row = M.send_log.find_one({"uid": ws.uid, "mid": lookup_hash("mid:" + mid.strip("<> ").lower())})
                if not row:  # older sends without our Message-ID: match the most recent email to that address
                    row = M.send_log.find_one({"uid": ws.uid, "to_hash": lookup_hash("to:" + rcpt)}, sort=[("ts", -1)]) \
                        or next((d for d in M.send_log.find({"uid": ws.uid, "rid": {"$ne": None}}).sort("ts", -1).limit(3000)
                                 if (unseal(d["data"], {}) or {}).get("to", "").lower() == rcpt
                                 and (unseal(d["data"], {}) or {}).get("status") == "sent"), None)
                if not row:
                    continue
                entry = unseal(row["data"], {})
                if entry.get("status") == "bounced":
                    continue
                reason = bounce_reason(f"{status_code} {diag}")
                detail = f"{reason}. {re.sub(r'\\s+', ' ', diag).strip()[:220]}".strip()
                entry.update(status="bounced", detail=detail)
                M.send_log.update_one({"_id": row["_id"]}, {"$set": {"data": seal(entry)}})
                if row.get("rid"):
                    ws.set_status(row["rid"], "email", "bounced")
                    ws.add_event(row["rid"], "bounced", f"Email bounced: {reason}")
                found.append({"to": rcpt, "reason": reason})
    finally:
        try:
            imap.logout()
        except Exception:
            pass
        with ws.lock:  # union with what another check may have saved meanwhile
            seen |= set(ws.load("bounces_seen", []))
            ws.save("bounces_seen", sorted(seen)[-5000:])
        ws.update_settings(bounces_checked_at=datetime.now().isoformat(timespec="seconds"))
    if found and getattr(bridge_mod(), "notify", None):
        bridge_mod().notify(ws.uid, f"{len(found)} email{'s' if len(found) != 1 else ''} bounced",
                            ", ".join(f["to"] for f in found[:3]) + (" and more" if len(found) > 3 else ""), "#activity", "bounce")
    return {"checked": checked, "bounced": found}


BOUNCE_CHECK_SOON = {}   # uid -> when to run an extra check after a campaign


def bounce_watcher():
    """Background loop: checks accounts that sent email recently, every 10 minutes (sooner right after a run)."""
    last = {}
    while True:
        time.sleep(30)
        try:
            now = time.time()
            recent = M.send_log.distinct("uid", {"ts": {"$gt": now - 3 * 86400}, "mid": {"$exists": True}})
            for uid in recent:
                due = BOUNCE_CHECK_SOON.get(uid, 0)
                if (due and now >= due) or now - last.get(uid, 0) > 600:
                    from core.sending import (
                        JOBS,  # (late: sending also uses this module)
                    )
                    if uid in JOBS and JOBS[uid].running:
                        continue
                    BOUNCE_CHECK_SOON.pop(uid, None)
                    last[uid] = now
                    try:
                        bridge_mod().with_deadline(240, check_bounces, Workspace(uid), days=4)
                    except Exception as e:
                        print(f"[Inkwell] bounce check skipped for an account: {getattr(e, 'message', e)}", flush=True)
        except Exception as e:
            print(f"[Inkwell] bounce watcher error: {e}", flush=True)
