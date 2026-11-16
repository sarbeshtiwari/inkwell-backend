"""WhatsApp API: link status, QR code, cancel and unlink."""

import io
import threading
from datetime import datetime, timezone

from flask import jsonify, request, send_file

from core.config import WA_SESSION_HOURS, WHATSAPP_ENABLED
from core.db import M
from core.http import app
from core.security import login_required
from core.sending import JOBS
from core.storage import drop_file
from core.validation import Invalid
from core.whatsapp_store import BUSY, WA_LINKS, WALink, link_whatsapp, wa_expires_at


@app.get("/api/whatsapp")
@login_required
def wa_status(ws):
    if not WHATSAPP_ENABLED:
        return jsonify(state="disabled", error="", linked_at="")
    link = WA_LINKS.get(ws.uid)
    st = ws.settings()
    if link and link.state in ("starting", "qr", "saving"):
        state = link.state
    else:
        state = "connected" if st.get("wa_connected") else "disconnected"
    expires = wa_expires_at(ws.uid) if state == "connected" else None
    return jsonify(state=state, error=link.error if link and link.state == "error" else "",
                   linked_at=st.get("wa_linked_at", ""), hours=WA_SESSION_HOURS,
                   expires_at=datetime.fromtimestamp(expires, timezone.utc).isoformat() if expires else "")


@app.before_request
def whatsapp_switch():
    if not WHATSAPP_ENABLED and request.path.startswith("/api/whatsapp/"):
        return jsonify(error="WhatsApp isn't available here."), 404


@app.post("/api/whatsapp/link")
@login_required
def wa_link(ws):
    current = WA_LINKS.get(ws.uid)
    if current and current.state in ("starting", "qr", "saving"):
        return jsonify(ok=True)
    if JOBS.get(ws.uid) and JOBS[ws.uid].running:
        raise Invalid("Wait for the current send to finish.", status=409)
    link = WA_LINKS[ws.uid] = WALink()
    threading.Thread(target=link_whatsapp, args=(ws, link), daemon=True).start()
    return jsonify(ok=True)


@app.get("/api/whatsapp/qr")
@login_required
def wa_qr(ws):
    link = WA_LINKS.get(ws.uid)
    if not link or not link.qr:
        return jsonify(error="No QR code yet."), 404
    return send_file(io.BytesIO(link.qr), mimetype="image/png")


@app.post("/api/whatsapp/cancel")
@login_required
def wa_cancel(ws):
    if ws.uid in WA_LINKS:
        WA_LINKS[ws.uid].cancel.set()
    return jsonify(ok=True)


@app.post("/api/whatsapp/unlink")
@login_required
def wa_unlink(ws):
    if ws.uid in BUSY:
        raise Invalid("WhatsApp is in use right now. Try again when it has finished.", status=409)
    row = M.wa_sessions.find_one_and_delete({"_id": ws.uid})
    if row:
        drop_file(row["file_id"])
    ws.update_settings(wa_connected=False, wa_linked_at="")
    WA_LINKS.pop(ws.uid, None)
    return jsonify(ok=True)
