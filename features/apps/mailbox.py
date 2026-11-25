"""Scanning the mailbox and merging what it finds into applications."""

import hashlib
import re
import threading
import time
from datetime import datetime, timedelta
from email.utils import parseaddr
import bridge

from features.apps.common import C
from features.apps.extract import body_text, calendar_start, extract, naukri_events, snippet
from features.apps.noise import ME_NAMES, NOISE_FROM, NOISE_SUBJECT, my_name_parts, norm_company, norm_role, tidy
from features.apps.statuses import ACTIVE, STATUSES, classify
from infra.redact import error_text, secrets


SEARCH_TERMS = ['"your application"', '"application status"', 'applied', 'applying', 'interview', 'assessment',
                '"offer letter"', 'candidature', 'candidacy', 'shortlisted', '"thank you for your interest"', 'regret',
                '"next steps"', '"not selected"', '"other candidates"', '"application received"', '"application for"']


ATS_FROM = ("myworkday.com", "lever.co", "greenhouse-mail.io", "greenhouse.io", "icims.com", "smartrecruiters.com",
            "successfactors.com", "brassring.com", "avature.net", "ashbyhq.com", "rippling.com", "jobvite.com",
            "taleo.net", "oraclecloud.com", "workablemail.com", "naukri.com", "jobs-noreply@linkedin.com")


def gmail_query(since):
    q = "(" + " OR ".join(SEARCH_TERMS) + " OR " + " OR ".join(f"from:{d}" for d in ATS_FROM) + ") -from:me -in:chats"
    if since:
        q += f" after:{since.strftime('%Y/%m/%d')}"
    return q


def search_ids(imap, since, is_gmail):
    if is_gmail:
        typ, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
        if typ == "OK":
            q = gmail_query(since).replace("\\", "\\\\").replace('"', '\\"')
            typ, data = imap.search(None, "X-GM-RAW", f'"{q}"')
            if typ == "OK":
                return data[0].split() if data and data[0] else []
    imap.select("INBOX", readonly=True)
    crit = [f"SINCE {since.strftime('%d-%b-%Y')}"] if since else []
    ids = set()
    for term in ("application", "applying", "applied", "interview", "assessment", "offer", "shortlisted", "candidature"):
        typ, data = imap.search(None, *crit, "SUBJECT", f'"{term}"')
        if typ == "OK" and data and data[0]:
            ids.update(data[0].split())
    for dom in ATS_FROM:
        typ, data = imap.search(None, *crit, "FROM", f'"{dom}"')
        if typ == "OK" and data and data[0]:
            ids.update(data[0].split())
    return sorted(ids, key=int)


def fetch_batch(imap, nums, what):
    """{num: bytes} for a batch of message numbers."""
    out = {}
    typ, data = imap.fetch(b",".join(nums).decode(), what)
    if typ != "OK":
        return out
    for item in data:
        if isinstance(item, tuple) and item[0]:
            m = re.match(rb"(\d+) ", item[0])
            if m:
                out[m.group(1)] = item[1]
    return out


SYNC_LOCK = {}  # uid -> Lock; one sync per account at a time


PROGRESS = {}   # uid -> {"stage", "done", "total", "started"}


def scan(ws, full=False, notify=True):
    """Read application emails and update ws['applications']. Returns a summary dict."""
    core = C()
    lock = SYNC_LOCK.setdefault(ws.uid, threading.Lock())
    if not lock.acquire(blocking=False):
        raise core.Invalid("A sync is already running.", status=409)
    state = ws.load("apps_sync", {})
    PROGRESS[ws.uid] = {"stage": "Connecting to your mailbox…", "done": 0, "total": 0, "started": time.time()}
    try:
        profile = ws.profile()
        imap = core.imap_connect(profile)
        is_gmail = core.imap_host_for(profile) == "imap.gmail.com"
        since = None
        if not full and state.get("full_done") and state.get("last_ok"):
            since = datetime.fromisoformat(state["last_ok"]).date() - timedelta(days=3)
        seen = set(ws.load("apps_seen", []))
        apps = ws.load("applications", {})
        me = (profile.get("email") or "").lower()
        me_names = my_name_parts(profile)
        ME_NAMES.set(me_names)
        before = {k: v.get("status") for k, v in apps.items()}
        added = updated = mails = 0
        changes = []
        try:
            PROGRESS[ws.uid]["stage"] = "Searching your mailbox…"
            ids = search_ids(imap, since, is_gmail)
            PROGRESS[ws.uid].update(stage="Reading emails…", total=len(ids))
            wanted = []
            for i in range(0, len(ids), 200):
                chunk = ids[i:i + 200]
                heads = fetch_batch(imap, chunk, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])")
                for num in chunk:
                    h = core.email_lib.message_from_bytes(heads.get(num, b""), policy=core.email_policy)
                    mid = str(h.get("Message-ID") or f"{num}-{h.get('Date')}").strip()
                    key = hashlib.sha256(("appmail:" + mid).encode()).hexdigest()[:24]
                    if key in seen:
                        continue
                    subject, sender = str(h.get("Subject") or ""), str(h.get("From") or "")
                    if me and me in sender.lower():
                        seen.add(key)
                        continue
                    if NOISE_SUBJECT.search(subject) or NOISE_FROM.search(sender):
                        seen.add(key)
                        continue
                    wanted.append((num, key, mid, subject, sender, str(h.get("Date") or "")))
            PROGRESS[ws.uid].update(total=len(wanted), done=0)
            for i in range(0, len(wanted), 25):
                chunk = wanted[i:i + 25]
                bodies = fetch_batch(imap, [c[0] for c in chunk], "(BODY.PEEK[]<0.400000>)")
                for num, key, mid, subject, sender, date_h in chunk:
                    seen.add(key)
                    raw = bodies.get(num)
                    if not raw:
                        continue
                    mails += 1
                    try:
                        msg = core.email_lib.message_from_bytes(raw, policy=core.email_policy)
                        a, u, ch = ingest(apps, msg, mid, subject, sender, date_h, me_names)
                    except Exception as e:  # one odd email mustn't stop the scan
                        print(f"[Inkwell] apps: skipped a message ({e})", flush=True)
                        continue
                    added += a
                    updated += u
                    changes += ch
                PROGRESS[ws.uid]["done"] = min(len(wanted), i + 25)
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        with ws.lock:  # merge into what's saved now: edits you made while the scan ran are kept
            apps = merge_scanned(ws.load("applications", {}), apps, set(before))
            ws.save("applications", apps)
            ws.save("apps_seen", sorted(seen)[-40000:])
        now = datetime.now().isoformat(timespec="seconds")
        state.update(last_run=now, last_ok=now, last_error="", full_done=state.get("full_done") or since is None,
                     emails=mails, added=added, updated=updated)
        ws.save("apps_sync", state)
        moved = [(k, before.get(k), apps[k]["status"]) for k in apps if k in before and before[k] != apps[k]["status"]]
        if notify:
            announce(ws, apps, moved, added, full=since is None)
        return {"emails": mails, "added": added, "updated": updated, "changed": len(moved), "total": len(apps)}
    except Exception as e:
        state.update(last_run=datetime.now().isoformat(timespec="seconds"),
                     last_error=secrets(str(e)) if isinstance(e, core.Invalid) else f"Sync failed: {error_text(e, 300)}")
        ws.save("apps_sync", state)
        raise
    finally:
        PROGRESS.pop(ws.uid, None)
        lock.release()


def ingest(apps, msg, mid, subject, sender, date_h, me_names=()):
    """Turn one email into events on the applications dict. Returns (added, updated, changes)."""
    core = C()
    name, addr = parseaddr(sender)
    body = body_text(msg)
    try:
        when = core.parsedate_to_datetime(date_h).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        when = datetime.now()
    base = {"mid": mid, "subject": tidy(subject, 200), "from": tidy(name or addr, 80), "from_addr": addr.lower(),
            "at": when.isoformat(timespec="seconds")}
    if addr.lower().endswith("naukri.com") and (evs := naukri_events(subject, body, when)):
        events = [{**base, **e, "portal": "Naukri", "job_id": "", "snippet": e["phrase"]} for e in evs]
    else:
        status, phrase = classify(subject, body[:6000])
        if not status:
            return 0, 0, []
        info = extract(subject, name, addr, body, me_names)
        if not info["company"]:
            return 0, 0, []
        ev = {**base, **info, "status": status, "snippet": snippet(body, phrase)}
        if status == "interview":
            start = calendar_start(msg)
            if start:
                ev["interview_at"] = start.isoformat(timespec="minutes")
            if re.search(r"\bmissed interview\b|\bno[- ]show\b", subject, re.I):
                ev["note"] = "Missed interview"
        if addr.lower().endswith("linkedin.com"):
            ev["portal"] = "LinkedIn"
        events = [ev]
    added = updated = 0
    for ev in events:
        app, new = match(apps, ev)
        if any(h.get("mid") == ev["mid"] and h["status"] == ev["status"] for h in app["history"]):
            continue
        app["history"].append({k: ev.get(k) for k in ("status", "at", "subject", "from", "snippet", "mid", "interview_at", "note")
                               if ev.get(k)})
        for k in ("role", "job_id", "portal"):
            if ev.get(k) and not app.get(k):
                app[k] = ev[k]
        added += new
        updated += not new
    return added, updated, []


def app_id(company, role, jid=""):
    return hashlib.sha256(f"{norm_company(company)}|{norm_role(role)}|{jid}".encode()).hexdigest()[:16]


def match(apps, ev):
    """Find the application an event belongs to, or create it. Returns (app, created)."""
    nc, nr, jid = norm_company(ev["company"]), norm_role(ev.get("role")), (ev.get("job_id") or "").upper()
    same = [a for a in apps.values() if norm_company(a["company"]) == nc and not a.get("merged_into")]
    pick = None
    if jid:
        pick = next((a for a in same if (a.get("job_id") or "").upper() == jid), None)
    if not pick and nr:
        pick = next((a for a in same if norm_role(a.get("role")) == nr), None) or \
            next((a for a in same if a.get("role") and (nr in norm_role(a["role"]) or norm_role(a["role"]) in nr)), None)
        if not pick and ev["status"] != "applied":
            pick = next((a for a in sorted(same, key=lambda a: a.get("updated_at", ""), reverse=True) if not a.get("role")), None)
    if not pick and not nr and not jid and same:
        ordered = sorted(same, key=lambda a: max((h["at"] for h in a["history"]), default=""), reverse=True)
        if ev["status"] == "applied":
            # A confirmation without a role is its own application, unless it repeats one from the same day.
            day = ev["at"][:10]
            pick = next((a for a in ordered if any(h["status"] == "applied" and h["at"][:10] == day for h in a["history"])), None)
        else:
            # An update that doesn't say which role: the most recent one still open, else the most recent.
            open_ = [a for a in ordered if (a["history"] and a["history"][-1]["status"]) in ACTIVE]
            pick = open_[0] if open_ else None
    if pick:
        return pick, False
    aid = app_id(ev["company"], ev.get("role", ""), jid if not nr else "")
    while aid in apps:
        aid = hashlib.sha256((aid + ev["mid"]).encode()).hexdigest()[:16]
    apps[aid] = {"id": aid, "company": ev["company"], "role": ev.get("role", ""), "job_id": ev.get("job_id", ""),
                 "portal": ev.get("portal", ""), "history": [], "created": datetime.now().isoformat(timespec="seconds")}
    return apps[aid], True


HISTORY_MAX = 200  # events kept per application


def merge_scanned(current, scanned, before):
    """Fold a finished mailbox scan into the latest saved applications.
    The scan only adds applications and history events (and fills blanks). What you did meanwhile wins:
    edits, hides, manual statuses; an application you deleted or merged during the scan stays gone."""
    for aid, a in scanned.items():
        cur = current.get(aid)
        if cur is None:
            if aid not in before:  # genuinely new from this scan
                current[aid] = a
            continue
        have = {(h.get("at"), h.get("status"), h.get("mid")) for h in cur.get("history", [])}
        cur.setdefault("history", []).extend(h for h in a.get("history", []) if (h.get("at"), h.get("status"), h.get("mid")) not in have)
        for k, v in a.items():
            if k not in ("history", "status", "prev_status", "hidden", "manual_status", "notes") and v and not cur.get(k):
                cur[k] = v
    for a in current.values():
        if len(a.get("history", [])) > HISTORY_MAX:
            a["history"] = sorted(a["history"], key=lambda h: h.get("at", ""))[-HISTORY_MAX:]
        recompute(a)
    return current


def recompute(a):
    """Derive applied_at / status / previous status / interview time from the history."""
    hist = sorted(a["history"], key=lambda h: (h["at"], STATUSES.get(h["status"], ("", 0))[1]))
    a["history"] = hist
    if not hist:
        return a
    applied = [h for h in hist if h["status"] == "applied"]
    a["applied_at"] = (applied[0] if applied else hist[0])["at"]
    a["updated_at"] = hist[-1]["at"]
    manual = a.get("manual_status")
    # Current status: the latest event; a later plain "applied" confirmation doesn't undo real progress.
    statuses = []
    for h in hist:
        s = h["status"]
        if statuses and s == "applied" and statuses[-1] != "applied" and STATUSES[statuses[-1]][1] > 1:
            continue
        if not statuses or statuses[-1] != s:
            statuses.append(s)
    a["status"] = manual or statuses[-1]
    a["prev_status"] = (statuses[-1] if manual and manual != statuses[-1] else statuses[-2] if len(statuses) > 1 else "")
    iv = [h for h in hist if h["status"] == "interview"]
    a["interview_at"] = next((h["interview_at"] for h in reversed(iv) if h.get("interview_at")), "")
    a["events"] = len(hist)
    a["contacted"] = any(h["status"] in ("shortlisted", "interview", "assessment", "offer") for h in hist)
    return a


def announce(ws, apps, moved, added, full):
    notify = getattr(bridge, "notify", None)
    if not notify:
        return
    if full and added:
        notify(ws.uid, f"Found {added} job application{'s' if added != 1 else ''}",
               "Your inbox scan is done. See them all under Applications.", "#applications", "application")
        return
    for aid, old, new in moved[:8]:
        a = apps[aid]
        label = STATUSES.get(new, (new,))[0]
        title = f"{a['company']}: {label}"
        body = (a.get("role") or "Your application") + (f" · was {STATUSES.get(old, (old,))[0]}" if old else "")
        notify(ws.uid, title, body, f"#applications/{aid}", "offer" if new == "offer" else "application")
    if len(moved) > 8:
        notify(ws.uid, f"{len(moved) - 8} more application updates", "Open Applications to see them.", "#applications", "application")
    if added and not full:
        notify(ws.uid, f"{added} new application{'s' if added != 1 else ''} found", "From today's mail sync.", "#applications", "application")
