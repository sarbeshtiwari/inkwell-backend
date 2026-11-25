"""Campaign API: preview, start/stop sends, job status, history, bounce checks."""


from flask import jsonify

from core.bounces import check_bounces
from core.http import app
from core.security import body, login_required, rate_limited
from core.sending import job_view, start_job, validate_send
from core.tables import render
from core.validation import Invalid
from infra import operations


@app.post("/api/preview")
@login_required
def preview(ws):
    p = body()
    rows = ws.load("recipients", [])
    r = next((x for x in rows if x["id"] == p.get("recipient_id")), rows[0] if rows else {})
    profile = ws.profile()
    return jsonify(subject=render(str(p.get("subject", ""))[:200], r, profile),
                   body=render(str(p.get("body", ""))[:5000], r, profile),
                   recipient=r.get("name") or r.get("company") or r.get("phone") or r.get("email") or "")


@app.post("/api/send")
@login_required
def start_send(ws):
    cfg = validate_send(ws, body())
    ws.update_settings(**{k: cfg[k] for k in ("mode", "template_id", "documents", "min_delay", "max_delay", "limit")})
    start_job(ws, cfg)  # one campaign per account, across every process (shared lease); 409 if one is running
    return jsonify(ok=True)


@app.post("/api/bounces/check")
@login_required
def bounces_check(ws):
    if rate_limited(("bounce-check", ws.uid), 12, 3600):
        raise Invalid("You've checked a lot recently. Try again in a few minutes.", status=429)
    return jsonify(check_bounces(ws))


@app.get("/api/job")
@login_required
def job_status(ws):
    return jsonify(job_view(ws.uid))


@app.post("/api/job/stop")
@login_required
def stop_job(ws):
    operations.cancel(ws.uid, "send")  # seen by the campaign between messages, in whichever process runs it
    return jsonify(ok=True)


@app.get("/api/history")
@login_required
def history(ws):
    return jsonify(ws.history())
