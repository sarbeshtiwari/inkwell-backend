"""Workspace API: state, templates, profile, documents and settings."""

import io
import re
import smtplib
import threading
from mimetypes import guess_type

from flask import jsonify, request, send_file
from werkzeug.utils import secure_filename

from core.config import ALLOWED_DOCS, MAX_FILE_MB, MAX_FILES, MAX_FILES_MB
from core.http import app
from core.mail import MAIL_PORTS, build_email, public_host, smtp_connect
from core.security import body, login_required, rate_limited
from core.sending import JOBS, Job
from core.validation import Invalid, v_email, v_int, v_phone, v_template_text, v_text
from core.workspace import new_id, public_profile


@app.get("/api/state")
@login_required
def state(ws):
    job = JOBS.get(ws.uid) or Job()
    return jsonify({
        "recipients": ws.load("recipients", []),
        "templates": ws.load("templates", []),
        "profile": public_profile(ws.profile()),
        "documents": ws.documents(),
        "settings": ws.settings(),
        "job": job.snapshot(),
    })


@app.post("/api/templates")
@login_required
def save_template(ws):
    p = body()
    item = {"name": v_text(p.get("name"), "name", "Template name", 80, required=True),
            "subject": v_template_text(p.get("subject"), "subject", "Email subject", 200, False),
            "body": v_template_text(p.get("body"), "body", "Message", 5000, True)}
    with ws.lock:
        items = ws.load("templates", [])
        if any(t["name"].lower() == item["name"].lower() and t["id"] != p.get("id") for t in items):
            raise Invalid("You already have a template with this name.", "name")
        existing = next((t for t in items if p.get("id") and t["id"] == p["id"]), None)
        if existing:
            existing.update(item)
            item = existing
        else:
            if len(items) >= 50:
                raise Invalid("You can keep up to 50 templates. Delete one first.")
            item["id"] = new_id()
            items.append(item)
        ws.save("templates", items)
    return jsonify(item)


@app.delete("/api/templates/<tid>")
@login_required
def delete_template(ws, tid):
    with ws.lock:
        items = ws.load("templates", [])
        if len(items) <= 1:
            raise Invalid("Keep at least one template.")
        ws.save("templates", [t for t in items if t["id"] != tid])
    return jsonify(ok=True)


@app.put("/api/profile")
@login_required
def save_profile(ws):
    p = body()
    with ws.lock:
        profile = ws.profile()
        profile.update({
            "name": v_text(p.get("name"), "name", "Your name", 80, required=True, min_len=2),
            "phone": v_phone(p.get("phone")),
            "email": v_email(p.get("email"), label="Email"),
            "smtp_host": v_text(p.get("smtp_host"), "smtp_host", "Mail server", 120),
            "smtp_port": v_int(p.get("smtp_port") or 465, "smtp_port", "Port", 1, 65535),
            "smtp_user": v_text(p.get("smtp_user"), "smtp_user", "Login", 254),
        })
        if profile["smtp_host"] and not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", profile["smtp_host"]):
            raise Invalid("Enter a mail server like smtp.gmail.com.", "smtp_host")
        if profile["smtp_port"] not in MAIL_PORTS:
            raise Invalid("Use port 465 (SSL) or 587 (STARTTLS). Your provider's help page lists the right one.", "smtp_port")
        if profile["smtp_host"]:
            public_host(profile["smtp_host"])
        old_password = profile.get("smtp_password", "")
        password = re.sub(r"\s", "", str(p.get("smtp_password") or ""))
        if password:  # blank = keep the saved password
            if len(password) > 200:
                raise Invalid("That password is too long.", "smtp_password")
            profile["smtp_password"] = password
        if p.get("clear_password"):
            profile["smtp_password"] = ""
        first_password = bool(password) and not old_password
        ws.save("profile", profile)
    if first_password:  # mailbox just connected: read it once so every page has data
        from features import apps as feature_apps
        threading.Thread(target=feature_apps.sync_all, args=(ws.uid, False, "first"), daemon=True).start()
    return jsonify(public_profile(profile))


@app.post("/api/profile/test")
@login_required
def test_profile(ws):
    if rate_limited(("smtp-test", ws.uid), 10, 3600):
        raise Invalid("Too many test emails. Try again later.", status=429)
    profile = ws.profile()
    try:
        server = smtp_connect(profile)
        server.send_message(build_email(profile, profile["email"], "Reachout – test email",
                                        "Your email settings work. You can now send outreach emails.", []))
        server.quit()
    except smtplib.SMTPAuthenticationError:
        raise Invalid("The mail server rejected the login. Check the login and app password.", "smtp_password")
    except (OSError, smtplib.SMTPException) as e:
        # A friendly category only: echoing the raw error would leak what other servers/ports answer.
        kind = ("the server's security certificate isn't valid" if "CERTIFICATE" in str(e).upper()
                else "the connection timed out" if isinstance(e, TimeoutError)
                else "the server refused the connection" if isinstance(e, ConnectionRefusedError)
                else "the server didn't accept the connection")
        raise Invalid(f"Couldn't connect to the mail server: {kind}. Check the server address and port.", "smtp_host")
    except RuntimeError as e:
        raise Invalid(str(e), "smtp_password")
    return jsonify(ok=True)


@app.post("/api/documents")
@login_required
def upload_documents(ws):
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        raise Invalid("Choose at least one file.", "files")
    existing = {d["name"]: d["size"] for d in ws.documents()}
    prepared, total = [], sum(existing.values())
    for f in uploads:
        name = secure_filename(f.filename) or "document"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_DOCS:
            raise Invalid(f"“{f.filename}” isn't a supported file type. Use PDF, Word, Excel, PowerPoint, LaTeX, "
                          f"text or image files.", "files")
        data = f.read()
        if not data:
            raise Invalid(f"“{f.filename}” is empty.", "files")
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise Invalid(f"“{f.filename}” is larger than {MAX_FILE_MB} MB.", "files")
        total += len(data) - existing.get(name, 0)
        prepared.append((name, data))
    if total > MAX_FILES_MB * 1024 * 1024:
        raise Invalid(f"That would go over your {MAX_FILES_MB} MB storage. Delete some files first.", "files")
    if len(set(existing) | {n for n, _ in prepared}) > MAX_FILES:
        raise Invalid(f"You can keep up to {MAX_FILES} files. Delete some first.", "files")
    for name, data in prepared:
        ws.save_doc(name, data)
    return jsonify(ok=True, replaced=[n for n, _ in prepared if n in existing])


@app.get("/api/documents/<name>")
@login_required
def view_document(ws, name):
    data = ws.doc_bytes(name)
    if data is None:
        return jsonify(error="File not found."), 404
    return send_file(io.BytesIO(data), download_name=name, mimetype=guess_type(name)[0] or "application/octet-stream")


@app.delete("/api/documents/<name>")
@login_required
def delete_document(ws, name):
    ws.delete_doc(name)
    return jsonify(ok=True)


@app.post("/api/settings")
@login_required
def save_settings(ws):
    p = body()
    cc = re.sub(r"\D", "", str(p.get("country_code", "")))
    if not 1 <= len(cc) <= 3:
        raise Invalid("Country code must be 1–3 digits, like 91 for India or 1 for the US.", "country_code")
    ws.update_settings(country_code=cc)
    return jsonify(ws.settings())
