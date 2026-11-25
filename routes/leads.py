"""Website enquiries (leads) for the site owners."""

import threading
import time
import uuid
from datetime import datetime

from flask import jsonify, request
from pymongo import DESCENDING

from core.accounts import find_user, is_admin
from core.config import ADMIN_EMAILS, DEV_OTP, LEAD_STATUSES, LEAD_TOPICS, MAIL_FROM
from core.crypto import lookup_hash, seal, unseal
from core.db import M
from core.http import app
from core.mail import send_system_email
from core.pages import read_source, set_cookie
from core.security import body, client_ip, login_required, rate_limited
from core.tables import xlsx_response
from core.validation import Invalid, safe_int, v_email, v_phone, v_text
from core.workspace import new_id


def lead_out(row):
    return {"id": row["_id"], "created": row["created"], "status": row["status"], **unseal(row["data"], {})}


def notify_admins(lead):
    to = sorted(ADMIN_EMAILS) or ([MAIL_FROM] if MAIL_FROM else [])
    if not to and not DEV_OTP:
        return
    text = (f"New enquiry on Inkwell\n\nName: {lead['name']}\nEmail: {lead['email']}\n"
            f"Phone: {lead.get('phone') or '-'}\nCompany: {lead.get('company') or '-'}\nTopic: {lead['topic']}\n"
            f"From: {lead.get('source') or '-'}\n\n{lead['message']}\n\nOpen the Leads inbox in Inkwell to reply.")
    for addr in to or ["(site owner)"]:
        try:
            send_system_email(addr, f"New enquiry: {lead['topic']} from {lead['name']}", text)
        except Exception as e:
            print(f"[Inkwell] Couldn't email enquiry alert: {e}", flush=True)


@app.post("/api/leads")
def create_lead():
    p = body()
    # Bots fill the hidden "website" field or submit instantly; accept quietly and store nothing.
    if p.get("website") or safe_int(p.get("elapsed_ms")) < 2500:
        return jsonify(ok=True)
    if rate_limited(("lead-ip", client_ip()), 5, 3600):
        raise Invalid("You've sent several messages already. Please try again in an hour.", status=429)
    lead = {
        "name": v_text(p.get("name"), "name", "Your name", 80, required=True, min_len=2),
        "email": v_email(p.get("email")),
        "phone": v_phone(p.get("phone")),
        "company": v_text(p.get("company"), "company", "Company", 120),
        "topic": p.get("topic") if p.get("topic") in LEAD_TOPICS else "",
        "message": v_text(p.get("message"), "message", "Message", 2000, required=True, min_len=10),
    }
    if not lead["topic"]:
        raise Invalid("Choose what your message is about.", "topic")
    if not p.get("consent"):
        raise Invalid("Please agree to be contacted about your enquiry.", "consent")
    if rate_limited(("lead-email", lookup_hash(lead["email"])), 3, 3600):
        raise Invalid("We've already received your messages. We'll be in touch soon.", status=429)
    origin = read_source()
    page_path = (request.referrer or "").removeprefix(request.host_url.rstrip("/")) if (request.referrer or "").startswith(request.host_url) else ""
    lead.update({
        "source": p.get("source") if p.get("source") in ("popup", "contact_page") else "",
        "page": page_path.split("?")[0][:300],
        "referrer": origin.get("referrer", ""),
        "landing": origin.get("landing", ""),
        "utm": origin.get("utm", {}),
        "notes": [],
    })
    lid = uuid.uuid4().hex[:12]
    M.leads.insert_one({"_id": lid, "created": time.time(), "status": "new", "data": seal(lead)})
    threading.Thread(target=notify_admins, args=(lead,), daemon=True).start()
    resp = jsonify(ok=True)
    set_cookie(resp, "ro_popup", "1")  # don't pop the form open again for someone who has written to us
    return resp


def lead_scope(ws):
    """Whose enquiries a request is about: your own website's (default), or Inkwell's own site (admins only)."""
    if request.args.get("scope") == "site":
        from infra import rbac
        rbac.check(ws.uid, "admin.site_leads", target="leads:site", message="Only the site owner can see this.")
        return {"uid": {"$exists": False}}
    return {"uid": ws.uid}


@app.get("/api/leads")
@login_required
def list_leads(ws):
    rows = M.leads.find(lead_scope(ws)).sort("created", DESCENDING).limit(5000)
    return jsonify(leads=[lead_out(r) for r in rows], statuses=LEAD_STATUSES, is_admin=is_admin(ws.uid),
                   site_new=M.leads.count_documents({"uid": {"$exists": False}, "status": "new"}) if is_admin(ws.uid) else 0)


@app.put("/api/leads/<lid>")
@login_required
def update_lead(ws, lid):
    p = body()
    row = M.leads.find_one({"_id": lid, **lead_scope(ws)})
    if not row:
        raise Invalid("That enquiry no longer exists.", status=404)
    lead = lead_out(row)
    status = p.get("status", lead["status"])
    if status not in LEAD_STATUSES:
        raise Invalid("Choose a valid status.", "status")
    data = {k: v for k, v in lead.items() if k not in ("id", "created", "status")}
    who = find_user(uid=ws.uid)["name"]
    now = datetime.now().isoformat(timespec="seconds")
    if status != lead["status"]:
        data["notes"].insert(0, {"id": new_id(), "type": "status", "by": who, "at": now,
                                 "text": f"Status changed from {LEAD_STATUSES[lead['status']]} to {LEAD_STATUSES[status]}"})
    if p.get("note") is not None:
        data["notes"].insert(0, {"id": new_id(), "type": "note", "by": who, "at": now,
                                 "text": v_text(p.get("note"), "note", "Note", 2000, required=True)})
    M.leads.update_one({"_id": lid}, {"$set": {"status": status, "data": seal(data)}})
    return jsonify({"id": lid, "created": lead["created"], "status": status, **data})


@app.delete("/api/leads/<lid>")
@login_required
def delete_lead(ws, lid):
    M.leads.delete_one({"_id": lid, **lead_scope(ws)})
    return jsonify(ok=True)


@app.get("/api/leads/export")
@login_required
def export_leads(ws):
    rows = M.leads.find(lead_scope(ws)).sort("created", DESCENDING)
    headers = ["received", "status", "name", "email", "phone", "company", "topic", "message", "source", "page",
               "referrer", "utm_source", "utm_medium", "utm_campaign"]
    out = []
    for r in rows:
        d = unseal(r["data"], {})
        out.append([datetime.fromtimestamp(r["created"]).isoformat(timespec="minutes"), LEAD_STATUSES.get(r["status"], ""),
                    *[d.get(k, "") for k in headers[2:11]], *[d.get("utm", {}).get(k, "") for k in headers[11:]]])
    return xlsx_response(headers, out, "enquiries.xlsx")
