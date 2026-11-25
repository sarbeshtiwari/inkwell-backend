"""Importing past sends from the user's Sent folder."""

import email as email_lib
import re
from datetime import date, datetime, timedelta
from email.policy import default as email_policy
from email.utils import getaddresses, parsedate_to_datetime

from core.crypto import lookup_hash, unseal
from core.db import M
from core.mail import imap_connect
from core.validation import EMAIL_RE, Invalid

PERSONAL_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com",
                    "outlook.com", "outlook.in", "hotmail.com", "hotmail.co.in", "live.com", "live.in", "msn.com",
                    "icloud.com", "me.com", "mac.com", "aol.com", "rediffmail.com", "proton.me", "protonmail.com",
                    "zoho.com", "zohomail.in", "gmx.com", "mail.com", "yandex.com"}


AUTOMATED = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?|bounce|alerts?|support|"
                       r"help|feedback|billing|invoices?)(\+[^@]*)?@|alert|\.coach\.|"
                       r"@(.+\.)?(greenhouse|lever|workday|myworkday|smartrecruiters|icims|taleo|naukri|linkedin|indeed|"
                       r"mail-tester|zendesk|freshdesk|helpscout|intercom-mail|mailchimp|sendgrid)\.", re.I)


def find_sent_folder(imap):
    typ, folders = imap.list()
    names = []
    for raw in folders or []:
        line = raw.decode(errors="ignore")
        m = re.search(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\Sent" in m.group("flags"):
            return name
        names.append(name)
    for guess in ("[Gmail]/Sent Mail", "Sent Items", "Sent", "INBOX.Sent", "Sent Messages"):
        if guess in names:
            return guess
    raise Invalid("Couldn't find your Sent folder.")


def clean_display_name(name):
    """'Sangani, Mahesh' -> 'Mahesh Sangani'; drops '(Company)' suffixes and names that are just addresses."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", (name or "").strip().strip('"\'')).strip()
    if "@" in name or not re.search(r"[A-Za-z]", name):
        return ""
    if re.fullmatch(r"[^,]+,\s*[^,]+", name):
        last, first = [x.strip() for x in name.split(",", 1)]
        name = f"{first} {last}"
    return name[:80]


def company_from_domain(domain):
    label = domain.lower().split(".")
    core = label[-3] if len(label) >= 3 and label[-2] in ("co", "com", "net", "org", "ac", "gov") else label[-2] if len(label) >= 2 else label[0]
    return core.replace("-", " ").title()


def already_logged(ws, addr, when_ts, rid=None):
    """True if history already has an email to this address within 15 minutes (e.g. a Inkwell campaign send)."""
    q = {"uid": ws.uid, "ts": {"$gte": when_ts - 900, "$lte": when_ts + 900}}
    if rid:
        q["rid"] = rid
    for d in M.send_log.find(q, {"data": 1}):
        e = unseal(d["data"], {}) or {}
        if e.get("channel") == "email" and str(e.get("to", "")).lower() == addr:
            return True
    return False


def scan_sent(ws, days):
    """Group every work address you emailed (headers only: To, Cc, Date, Subject, Message-ID).

    days=0 scans the whole Sent folder. Personal mailboxes (gmail.com, yahoo.com, …) are left out entirely."""
    profile = ws.profile()
    me = {profile.get("email", "").lower(), (profile.get("smtp_user") or "").lower()}
    imap = imap_connect(profile)
    people = {}
    try:
        folder = find_sent_folder(imap)
        typ, _ = imap.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            raise Invalid("Couldn't open your Sent folder.")
        crit = f"(SINCE {(date.today() - timedelta(days=days)).strftime('%d-%b-%Y')})" if days else "ALL"
        typ, data = imap.search(None, crit)
        ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-20000:]
        for i in range(0, len(ids), 200):
            chunk = b",".join(ids[i:i + 200]).decode()
            typ, rows = imap.fetch(chunk, "(BODY.PEEK[HEADER.FIELDS (TO CC DATE SUBJECT MESSAGE-ID)])")
            for item in rows or []:
                if not isinstance(item, tuple):
                    continue
                h = email_lib.message_from_bytes(item[1], policy=email_policy)
                mid = str(h.get("Message-ID", "")).strip()
                if re.search(r"\.ro@", mid):
                    continue  # sent by Inkwell itself: already in your history
                try:
                    when = parsedate_to_datetime(str(h.get("Date"))).astimezone().replace(tzinfo=None)
                except (TypeError, ValueError):
                    continue
                subject = re.sub(r"\s+", " ", str(h.get("Subject", "")))[:200]
                addrs = getaddresses([str(h.get("To", "")), str(h.get("Cc", ""))])
                for name, addr in addrs:
                    addr = addr.strip().lower()
                    if not EMAIL_RE.match(addr) or addr in me or AUTOMATED.search(addr) \
                            or addr.split("@")[1] in PERSONAL_DOMAINS:
                        continue
                    name = clean_display_name(name)
                    p = people.setdefault(addr, {"email": addr, "name": name, "count": 0, "sends": []})
                    p["count"] += 1
                    p["sends"].append({"date": when.isoformat(timespec="seconds"), "subject": subject, "mid": mid})
                    if name and not p["name"]:
                        p["name"] = name
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    known = {r.get("email", "").lower(): r for r in ws.load("recipients", []) if r.get("email")}
    imported = set(ws.load("sent_imported", []))
    out = []
    for p in people.values():
        p["sends"] = [x for x in p["sends"] if not already_logged(ws, p["email"], datetime.fromisoformat(x["date"]).timestamp())]
        if not p["sends"]:
            continue  # every email to this person is already in Inkwell's history
        p["count"] = len(p["sends"])
        p["sends"].sort(key=lambda x: x["date"], reverse=True)
        domain = p["email"].split("@")[1]
        contact = known.get(p["email"])
        out.append({**p, "sends": p["sends"][:20], "last": p["sends"][0]["date"], "last_subject": p["sends"][0]["subject"],
                    "domain": domain, "personal": domain in PERSONAL_DOMAINS,
                    "company": (contact or {}).get("company") or ("" if domain in PERSONAL_DOMAINS else company_from_domain(domain)),
                    "contact": bool(contact),
                    "imported": all(lookup_hash("sent:" + s["mid"].lower()) in imported for s in p["sends"] if s["mid"])})
    out.sort(key=lambda x: x["last"], reverse=True)   # newest first…
    out.sort(key=lambda x: x["personal"])              # …with work addresses before personal ones (stable sort)
    return out
