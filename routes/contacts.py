"""Contacts API: add/edit/delete, notes, import/export, Sent-folder import."""

from datetime import date, datetime

from flask import jsonify, request

from core.bounces import check_bounces
from core.config import CORE_FIELDS, CRM_FIELDS, MAX_CONTACTS, STAGES, STATUS_FIELDS
from core.crypto import lookup_hash
from core.http import app
from core.security import body, login_required, rate_limited
from core.sentmail import already_logged, scan_sent
from core.tables import phone_key, read_table, xlsx_response
from core.validation import Invalid, clean_contact, v_email, v_int, v_text
from core.workspace import new_id, now_iso


@app.post("/api/sentmail/scan")
@login_required
def sentmail_scan(ws):
    days = v_int(body().get("days", 0), "days", "Period", 0, 3650)  # 0 = whole Sent folder
    if rate_limited(("sent-scan", ws.uid), 10, 3600):
        raise Invalid("You've scanned several times recently. Try again in a few minutes.", status=429)
    items = scan_sent(ws, days)
    return jsonify(items=items, days=days)


@app.post("/api/sentmail/import")
@login_required
def sentmail_import(ws):
    p = body()
    items = p.get("items")
    # Off: people are added as fresh contacts that campaigns will email; earlier emails are kept as a note only.
    mark_contacted = bool(p.get("mark_contacted"))
    if not isinstance(items, list) or not items:
        raise Invalid("Select at least one person to import.")
    if len(items) > 2000:
        raise Invalid("Import at most 2,000 people at a time.")
    imported = set(ws.load("sent_imported", []))
    added = updated = history = 0
    with ws.lock:
        rows = ws.load("recipients", [])
        by_email = {r.get("email", "").lower(): r for r in rows if r.get("email")}
        touched = []
        for it in items:
            addr = v_email(it.get("email"))
            sends = [s for s in (it.get("sends") or [])[:20] if isinstance(s, dict)]
            if not sends:
                continue
            last = max(s.get("date", "") for s in sends)
            row = by_email.get(addr)
            if not row:
                if len(rows) >= MAX_CONTACTS:
                    raise Invalid(f"You've reached the limit of {MAX_CONTACTS} contacts.")
                row = {"id": new_id(), **clean_contact({"name": str(it.get("name") or "")[:80],
                                                         "company": str(it.get("company") or "")[:120], "email": addr}),
                       "stage": "contacted" if mark_contacted else "new", "list": "Imported from Sent mail", "added_at": now_iso()}
                rows.append(row); by_email[addr] = row; added += 1
            else:
                updated += 1
                if mark_contacted and row.get("stage", "new") == "new":
                    row["stage"] = "contacted"
            if mark_contacted:
                if row.get("email_status") not in ("bounced", "invalid"):
                    row["email_status"] = "sent"
                if last > (row.get("email_last") or ""):
                    row["email_last"] = last
            touched.append((row, sends))
        ws.save("recipients", rows)
    if not mark_contacted:
        # Keep a record of the earlier emails on each contact, without counting them as sends.
        for row, sends in touched:
            fresh = [s for s in sends if not s.get("mid") or lookup_hash("sent:" + str(s["mid"]).lower()) not in imported]
            if not fresh:
                continue
            lines = "; ".join(f"{str(s.get('date'))[:10]} \u201c{s.get('subject') or 'no subject'}\u201d" for s in fresh[:5])
            ws.add_event(row["id"], "note", f"You emailed this address {len(fresh)} time{'s' if len(fresh) != 1 else ''} "
                         f"before Reachout (from your Sent folder): {lines}.")
            imported.update(lookup_hash("sent:" + str(s["mid"]).lower()) for s in fresh if s.get("mid"))
        ws.save("sent_imported", sorted(imported)[-20000:])
        return jsonify(added=added, updated=updated, history=0, bounced=0)
    for row, sends in touched:
        new = []
        for s in sends:
            key = lookup_hash("sent:" + str(s.get("mid", "")).lower()) if s.get("mid") else None
            if key and key in imported:
                continue
            try:
                when = datetime.fromisoformat(str(s.get("date")))
            except ValueError:
                continue
            if already_logged(ws, row["email"].lower(), when.timestamp()):
                continue
            ws.log("email", row["email"], row.get("name", ""), "sent", "Sent from your own mailbox",
                   rid=row["id"], preview=str(s.get("subject", ""))[:200], message_id=s.get("mid") or None,
                   when=when, source="mailbox")
            if key:
                imported.add(key)
            new.append(s); history += 1
        if new:
            ws.add_event(row["id"], "note", f"Found {len(new)} earlier email{'s' if len(new) != 1 else ''} in your Sent folder "
                         f"(latest: \u201c{new[0].get('subject') or 'no subject'}\u201d, {str(new[0].get('date'))[:10]}).")
    ws.save("sent_imported", sorted(imported)[-20000:])
    # Earlier sends may have bounced too: look for their bounce notices over the same period.
    oldest = min((s.get("date", "") for _, ss in touched for s in ss), default="")
    bounced = 0
    if oldest:
        try:
            days = max(14, (datetime.now() - datetime.fromisoformat(oldest)).days + 2)
            bounced = len(check_bounces(ws, days=min(days, 3650))["bounced"])
        except Invalid:
            pass
    return jsonify(added=added, updated=updated, history=history, bounced=bounced)


@app.post("/api/recipients")
@login_required
def add_recipient(ws):
    fields = clean_contact(body())
    cc = ws.settings()["country_code"]
    with ws.lock:
        rows = ws.load("recipients", [])
        if len(rows) >= MAX_CONTACTS:
            raise Invalid(f"You've reached the limit of {MAX_CONTACTS} contacts.")
        pk, ek = phone_key(fields, cc), fields.get("email", "")
        for r in rows:
            if pk and phone_key(r, cc) == pk:
                raise Invalid("A contact with this phone number already exists.", "phone")
            if ek and r.get("email", "").lower() == ek:
                raise Invalid("A contact with this email already exists.", "email")
        row = {"id": new_id(), **fields, "added_at": now_iso()}
        rows.append(row)
        ws.save("recipients", rows)
    return jsonify(row)


@app.put("/api/recipients/<rid>")
@login_required
def update_recipient(ws, rid):
    changes = clean_contact(body(), partial=True)
    with ws.lock:
        rows = ws.load("recipients", [])
        row = next((r for r in rows if r["id"] == rid), None)
        if not row:
            raise Invalid("That contact no longer exists.", status=404)
        merged = {**row, **changes}
        if not merged.get("phone") and not merged.get("email"):
            raise Invalid("A contact needs a phone number or an email address.", next(iter(changes), "phone"))
        old_stage, old_follow = row.get("stage", "new"), row.get("follow_up", "")
        row.update(changes)
        ws.save("recipients", rows)
    if "stage" in changes and changes["stage"] != old_stage:
        ws.add_event(rid, "stage", f"Stage changed from {STAGES[old_stage]} to {STAGES[changes['stage']]}")
    if "follow_up" in changes and changes["follow_up"] != old_follow:
        d = date.fromisoformat(changes["follow_up"]) if changes["follow_up"] else None
        ws.add_event(rid, "follow_up", f"Follow-up set for {d.day} {d.strftime('%b %Y')}" if d else "Follow-up cleared")
    return jsonify(row)


@app.get("/api/contacts/<rid>")
@login_required
def contact_detail(ws, rid):
    row = next((r for r in ws.load("recipients", []) if r["id"] == rid), None)
    if not row:
        raise Invalid("That contact no longer exists.", status=404)
    return jsonify(contact=row, events=ws.events(rid), messages=ws.contact_log(row))


@app.post("/api/contacts/<rid>/notes")
@login_required
def add_note(ws, rid):
    if not any(r["id"] == rid for r in ws.load("recipients", [])):
        raise Invalid("That contact no longer exists.", status=404)
    text = v_text(body().get("text"), "text", "Note", 2000, required=True)
    return jsonify(ws.add_event(rid, "note", text))


@app.delete("/api/contacts/<rid>/notes/<nid>")
@login_required
def delete_note(ws, rid, nid):
    with ws.lock:
        ws.save(f"contact:{rid}", [e for e in ws.events(rid) if not (e["id"] == nid and e["type"] == "note")])
    return jsonify(ok=True)


def id_list():
    ids = body().get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise Invalid("Select at least one contact.")
    return set(ids)


@app.post("/api/recipients/delete")
@login_required
def delete_recipients(ws):
    ids = id_list()
    with ws.lock:
        ws.save("recipients", [r for r in ws.load("recipients", []) if r["id"] not in ids])
    ws.drop_events(ids)
    return jsonify(ok=True)


@app.post("/api/recipients/reset")
@login_required
def reset_recipients(ws):
    ids = id_list()
    with ws.lock:
        rows = ws.load("recipients", [])
        for r in rows:
            if r["id"] in ids:
                for k in STATUS_FIELDS:
                    r[k] = ""
        ws.save("recipients", rows)
    return jsonify(ok=True)


@app.post("/api/recipients/import")
@login_required
def import_recipients(ws):
    f = request.files.get("file")
    if not f or not f.filename:
        raise Invalid("Choose a file to import.", "file")
    data = f.read()
    if len(data) > 5 * 1024 * 1024:
        raise Invalid("That file is larger than 5 MB. Split it into smaller files.", "file")
    incoming = read_table(f.filename, data)
    cc = ws.settings()["country_code"]
    added = duplicates = invalid = 0
    replace = request.form.get("replace") == "1"
    with ws.lock:
        old_ids = [r["id"] for r in ws.load("recipients", [])] if replace else []
        rows = [] if replace else ws.load("recipients", [])
        keys = {phone_key(r, cc) for r in rows} | {r.get("email", "").lower() for r in rows}
        keys.discard("")
        for r in incoming:
            try:
                r = clean_contact(r)
            except Invalid:
                invalid += 1
                continue
            pk, ek = phone_key(r, cc), r.get("email", "")
            if (pk and pk in keys) or (ek and ek in keys):
                duplicates += 1
                continue
            if len(rows) >= MAX_CONTACTS:
                invalid += 1
                continue
            keys |= {pk, ek} - {""}
            rows.append({"id": new_id(), **r, "added_at": now_iso()})
            added += 1
        ws.save("recipients", rows)
        if old_ids:  # replaced contacts: their notes/history go with them
            ws.drop_events(old_ids)
    return jsonify(added=added, duplicates=duplicates, invalid=invalid)


@app.get("/api/recipients/export")
@login_required
def export_recipients(ws):
    rows = ws.load("recipients", [])
    extra = sorted({k for r in rows for k in r} - set(CORE_FIELDS) - set(STATUS_FIELDS) - set(CRM_FIELDS) - {"id"})
    headers = CORE_FIELDS + extra + CRM_FIELDS + STATUS_FIELDS
    return xlsx_response(headers, [[r.get(h, "") for h in headers] for r in rows], "contacts.xlsx")


@app.get("/api/recipients/sample")
def sample_sheet():
    return xlsx_response(["name", "company", "phone", "email"],
                         [["Priya Sharma", "Acme Pvt Ltd", "9876543210", "priya@acme.com"],
                          ["", "Globex", "+91 98123 45678", ""]], "contacts_sample.xlsx")
