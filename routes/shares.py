"""Sharing contacts with another Reachout account: the sender picks contacts and a friend's email; the friend
sees the offer and, only if they accept, gets a copy of those contacts. Contact details are sealed until then,
and an offer nobody answers lapses after SHARE_DAYS."""

import time
from datetime import datetime, timedelta, timezone

from flask import jsonify
from pymongo import DESCENDING

from core.accounts import find_user
from core.config import CORE_FIELDS, CRM_FIELDS, MAX_CONTACTS, STATUS_FIELDS
from core.crypto import seal, unseal
from core.db import M
from core.http import app
from core.security import body, login_required, rate_limited
from core.tables import phone_key
from core.validation import Invalid, v_email, v_text
from core.workspace import bridge_mod, new_id, now_iso

SHARE_DAYS = 30
MAX_SHARE = 2000  # contacts in one share
MAX_PENDING = 20  # unanswered shares one account can have out at once
# What a friend gets: the contact and its extra columns, never your history with them (sends, replies, stage, notes).
PRIVATE = set(STATUS_FIELDS) | set(CRM_FIELDS) | {"id", "list", "email_type"}


def _out(row, mine):
    d = unseal(row["data"], {})
    return {"id": row["_id"], "status": row["status"], "created": row["created"], "count": d.get("count", 0),
            "note": d.get("note", ""), "list": d.get("list", ""),
            **({"to": d.get("to_name"), "to_email": d.get("to_email")} if mine else
               {"from": d.get("from_name"), "from_email": d.get("from_email"),
                "preview": [c.get("name") or c.get("company") or c.get("email") for c in d.get("contacts", [])[:5]]})}


@app.post("/api/shares")
@login_required
def share_contacts(ws):
    p = body()
    email = v_email(p.get("email"), required=True)
    note = v_text(p.get("note"), "note", "Message", 300)
    ids = p.get("ids")
    if not isinstance(ids, list) or not ids:
        raise Invalid("Select the contacts you want to share.")
    if len(ids) > MAX_SHARE:
        raise Invalid(f"You can share up to {MAX_SHARE} contacts at a time.")
    if rate_limited(("share", ws.uid), 30, 86400):
        raise Invalid("You've shared a lot today. Try again tomorrow.")
    friend = find_user(email=email)
    if not friend:
        raise Invalid("No one has a Reachout account with that email. Ask them to sign up first.", "email")
    if friend["id"] == ws.uid:
        raise Invalid("That's your own account. Enter a friend's email.", "email")
    if M.contact_shares.count_documents({"from_uid": ws.uid, "status": "pending"}) >= MAX_PENDING:
        raise Invalid("You have too many shares waiting for an answer. Cancel some first.")
    wanted = set(map(str, ids))
    contacts = [{k: v for k, v in r.items() if k not in PRIVATE and v not in ("", None)}
                for r in ws.load("recipients", []) if r["id"] in wanted]
    if not contacts:
        raise Invalid("Those contacts no longer exist. Refresh the page.")
    me = {**(find_user(uid=ws.uid) or {}), **{k: v for k, v in ws.profile().items() if v}}
    sender = me.get("name") or me.get("email") or "A Reachout user"
    row = {"_id": new_id(), "from_uid": ws.uid, "to_uid": friend["id"], "status": "pending", "created": time.time(),
           "expire_at": datetime.now(timezone.utc) + timedelta(days=SHARE_DAYS),
           "data": seal({"from_name": sender, "from_email": me.get("email", ""), "to_name": friend.get("name") or email,
                         "to_email": email, "note": note, "count": len(contacts), "contacts": contacts,
                         "list": f"Shared by {sender}"[:80]})}
    M.contact_shares.insert_one(row)
    bridge_mod().notify(friend["id"], f"{sender} shared {len(contacts)} contact{'s' if len(contacts) != 1 else ''} with you",
                        "Open Contacts to accept or decline.", "/app/contacts", "share")
    return jsonify(_out(row, True))


@app.get("/api/shares")
@login_required
def list_shares(ws):
    cutoff = time.time() - SHARE_DAYS * 86400
    incoming = M.contact_shares.find({"to_uid": ws.uid, "status": "pending", "created": {"$gt": cutoff}}).sort("created", DESCENDING)
    outgoing = M.contact_shares.find({"from_uid": ws.uid}).sort("created", DESCENDING).limit(50)
    return jsonify(incoming=[_out(r, False) for r in incoming], outgoing=[_out(r, True) for r in outgoing])


def _pending_for(ws, sid):
    row = M.contact_shares.find_one({"_id": sid, "to_uid": ws.uid, "status": "pending"})
    if not row:
        raise Invalid("This share was cancelled or already answered.")
    return row


@app.post("/api/shares/<sid>/accept")
@login_required
def accept_share(ws, sid):
    row = _pending_for(ws, sid)
    d = unseal(row["data"], {})
    cc = ws.settings()["country_code"]
    added = duplicates = 0
    with ws.lock:
        rows = ws.load("recipients", [])
        keys = ({phone_key(r, cc) for r in rows} | {r.get("email", "").lower() for r in rows}) - {""}
        for c in d.get("contacts", []):
            pk, ek = phone_key(c, cc), c.get("email", "").lower()
            if (pk and pk in keys) or (ek and ek in keys):
                duplicates += 1
                continue
            if len(rows) >= MAX_CONTACTS:
                break
            keys |= {pk, ek} - {""}
            rows.append({"id": new_id(), **{k: c[k] for k in CORE_FIELDS if c.get(k)},
                         **{k: v for k, v in c.items() if k not in CORE_FIELDS},
                         "list": d.get("list", "Shared with me"), "stage": "new", "added_at": now_iso()})
            added += 1
        # Claim the share before saving, so two clicks can't import it twice.
        if not M.contact_shares.find_one_and_update({"_id": sid, "status": "pending"},
                                                    {"$set": {"status": "accepted", "answered": time.time(),
                                                              "data": seal({**d, "contacts": []})},
                                                     "$unset": {"expire_at": ""}}):
            raise Invalid("This share was already answered.")
        try:
            ws.save("recipients", rows)
        except Exception:  # put the offer back as it was, so the contacts aren't lost
            M.contact_shares.replace_one({"_id": sid}, row)
            raise
    me = {**(find_user(uid=ws.uid) or {}), **{k: v for k, v in ws.profile().items() if v}}
    bridge_mod().notify(_owner(sid), f"{me.get('name') or me.get('email')} accepted your {d.get('count', 0)} contacts", "", "/app/contacts", "share")
    return jsonify(added=added, duplicates=duplicates, skipped=d.get("count", 0) - added - duplicates, list=d.get("list"))


def _owner(sid):
    return (M.contact_shares.find_one({"_id": sid}, {"from_uid": 1}) or {}).get("from_uid")


@app.post("/api/shares/<sid>/decline")
@login_required
def decline_share(ws, sid):
    d = unseal(_pending_for(ws, sid)["data"], {})
    M.contact_shares.update_one({"_id": sid, "status": "pending"},
                                {"$set": {"status": "declined", "answered": time.time(), "data": seal({**d, "contacts": []})},
                                 "$unset": {"expire_at": ""}})
    return jsonify(ok=True)


@app.delete("/api/shares/<sid>")
@login_required
def cancel_share(ws, sid):
    """The sender takes back a share the friend hasn't answered yet, or clears an answered one from the list."""
    row = M.contact_shares.find_one({"_id": sid, "from_uid": ws.uid})
    if not row:
        raise Invalid("Share not found.")
    M.contact_shares.delete_one({"_id": sid, "from_uid": ws.uid})
    return jsonify(ok=True)
