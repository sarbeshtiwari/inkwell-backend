"""WhatsApp API: link status, QR code, cancel and unlink."""

import io
from datetime import datetime, timezone

from flask import jsonify, request, send_file

from core.config import WA_SESSION_HOURS, WHATSAPP_ENABLED
from core.db import M
from core.http import app
from core.security import login_required
from core.sending import send_running
from core.storage import drop_file
from core.validation import Invalid
from core.whatsapp_store import browser_busy, link_view, link_whatsapp, wa_expires_at
from infra import locks, operations


@app.get("/api/whatsapp")
@login_required
def wa_status(ws):
    if not WHATSAPP_ENABLED:
        return jsonify(state="disabled", error="", linked_at="")
    stage, error, _ = link_view(ws.uid)
    st = ws.settings()
    if stage in ("starting", "qr", "saving"):
        state = stage
    else:
        state = "connected" if st.get("wa_connected") else "disconnected"
    expires = wa_expires_at(ws.uid) if state == "connected" else None
    return jsonify(state=state, error=error if stage == "error" else "",
                   linked_at=st.get("wa_linked_at", ""), hours=WA_SESSION_HOURS,
                   expires_at=datetime.fromtimestamp(expires, timezone.utc).isoformat() if expires else "")


@app.before_request
def whatsapp_switch():
    if not WHATSAPP_ENABLED and request.path.startswith("/api/whatsapp/"):
        return jsonify(error="WhatsApp isn't available here."), 404


@app.post("/api/whatsapp/link")
@login_required
def wa_link(ws):
    if operations.running(ws.uid, "wa_link"):
        return jsonify(ok=True)
    if send_running(ws.uid):
        raise Invalid("Wait for the current send to finish.", status=409)
    try:
        op = operations.start(ws.uid, "wa_link", public={"stage": "starting"}, private={"qr": ""})
    except locks.LockTimeout:
        return jsonify(ok=True)  # started a moment ago by another request
    operations.spawn(op, link_whatsapp, ws, name=f"wa-link-{ws.uid[:6]}")
    return jsonify(ok=True)


@app.get("/api/whatsapp/qr")
@login_required
def wa_qr(ws):
    _, _, qr = link_view(ws.uid)
    if not qr:
        return jsonify(error="No QR code yet."), 404
    return send_file(io.BytesIO(qr), mimetype="image/png")


@app.post("/api/whatsapp/cancel")
@login_required
def wa_cancel(ws):
    operations.cancel(ws.uid, "wa_link")
    return jsonify(ok=True)


@app.post("/api/whatsapp/unlink")
@login_required
def wa_unlink(ws):
    if browser_busy(ws.uid):
        raise Invalid("WhatsApp is in use right now. Try again when it has finished.", status=409)
    row = M.wa_sessions.find_one_and_delete({"_id": ws.uid})
    if row:
        drop_file(row["file_id"])
    ws.update_settings(wa_connected=False, wa_linked_at="")
    return jsonify(ok=True)
