"""Request guards: rate limits, Netlify-signed proxy, CSRF header, security headers/CSP, login_required, error handlers."""

import base64
import functools
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone

from flask import g, jsonify, request, session
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from core.accounts import current_uid
from core.config import (
    MAX_FILE_MB,
    NETLIFY_PROXY_SECRET,
    OTP_FAILS_PER_DAY,
    SECURE_COOKIES,
)
from core.crypto import Undecryptable, lookup_hash
from core.db import M
from core.http import app
from core.validation import Invalid
from core.workspace import Workspace


@app.errorhandler(Invalid)
def on_invalid(e):
    return jsonify(error=e.message, field=e.field), e.status


@app.errorhandler(Undecryptable)
def on_undecryptable(e):
    app.logger.error("Undecryptable data for %s %s", request.method, request.path)
    return jsonify(error="Some of your saved data couldn't be read (the encryption key may have changed). "
                         "Nothing was changed. Restore the original data/secret.key and try again."), 500


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=e.description or e.name), e.code
        return e
    app.logger.exception("Unhandled error on %s %s", request.method, request.path)
    if request.path.startswith("/api/"):
        return jsonify(error="Something went wrong on our side. Please try again."), 500
    return "Something went wrong. Please try again.", 500


@app.errorhandler(413)
def on_too_large(_):
    return jsonify(error=f"That upload is too large. Each file can be up to {MAX_FILE_MB} MB."), 413


def rate_limited(key, limit, window, peek=False):
    """Fixed-window counter kept in MongoDB (shared by every worker, survives restarts, expires on its own).
    Keys are hashed, so no emails or IPs are stored in the clear. peek=True only checks whether the counter
    is already over OTP_FAILS_PER_DAY-style limits without counting."""
    now = time.time()
    bucket = int(now // window)
    kid = lookup_hash(f"rl:{json.dumps(key, default=str)}:{window}:{bucket}")
    if peek:
        row = M.ratelimits.find_one({"_id": kid})
        return bool(row) and row["n"] > (limit or OTP_FAILS_PER_DAY)
    try:
        row = M.ratelimits.find_one_and_update(
            {"_id": kid}, {"$inc": {"n": 1}, "$setOnInsert": {"expire_at": datetime.fromtimestamp((bucket + 2) * window, tz=timezone.utc)}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        row = M.ratelimits.find_one_and_update({"_id": kid}, {"$inc": {"n": 1}}, return_document=ReturnDocument.AFTER)
    return row["n"] > limit


def netlify_signed():
    """True when this request carries a valid Netlify proxy signature (X-Nf-Sign: an HS256 JWT)."""
    cached = g.get("nf_ok")
    if cached is not None:
        return cached
    ok = False
    token = request.headers.get("X-Nf-Sign", "")
    parts = token.split(".")
    if NETLIFY_PROXY_SECRET and len(parts) == 3:
        def b64(x):
            return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
        try:
            header, claims = json.loads(b64(parts[0])), json.loads(b64(parts[1]))
            want = hmac.new(NETLIFY_PROXY_SECRET.encode(), f"{parts[0]}.{parts[1]}".encode(), "sha256").digest()
            ok = (header.get("alg") == "HS256" and hmac.compare_digest(want, b64(parts[2]))
                  and float(claims.get("exp", time.time() + 1)) > time.time() - 30)
        except (ValueError, TypeError):
            ok = False
    g.nf_ok = ok
    return ok


def client_ip():
    """The caller's address. Behind Netlify (signed) it's the visitor's address Netlify reports; otherwise,
    with TRUST_PROXY, only the proxy's X-Forwarded-For is honoured (ProxyFix)."""
    ip = request.remote_addr or ""
    if NETLIFY_PROXY_SECRET and netlify_signed():
        ip = request.headers.get("X-Nf-Client-Connection-Ip") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or ip
    return ":".join(ip.split(":")[:4]) if ":" in ip else ip  # group IPv6 by /64 so rotating addresses doesn't help


NF_REFUSED_LOGGED = []


@app.before_request
def only_via_netlify():
    if NETLIFY_PROXY_SECRET and request.path != "/healthz" and not netlify_signed():
        if not NF_REFUSED_LOGGED:  # once per process: enough to spot a misconfigured secret, no log flooding
            NF_REFUSED_LOGGED.append(1)
            app.logger.warning("Refused %s %s: not a signed Netlify request (check NETLIFY_PROXY_SECRET on both sides)",
                               request.method, request.path)
        return jsonify(error="Not found."), 404


@app.before_request
def guard():
    # State-changing API calls must come from our own pages (blocks cross-site form posts).
    if request.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
        if request.headers.get("X-Requested-With") != "fetch":
            return jsonify(error="Bad request."), 400


def app_csp(nonce):
    """Policy for Reachout's own pages: scripts only from this server (or by per-request nonce) plus the
    editor's CDN; no plugins; can't be framed by other sites."""
    return ("default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: blob: https:; media-src 'self' data:; "
            "connect-src 'self' https://cdn.jsdelivr.net; worker-src 'self' blob:; "
            "frame-src 'self' https: blob:; child-src 'self' https: blob:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def add_nonce(html):
    """Stamp inline <script> tags in our own pages with this request's nonce (used by the CSP header)."""
    nonce = g.get("csp_nonce") or secrets.token_urlsafe(16)
    g.csp_nonce = nonce
    return re.sub(r"<script(?![^>]*\bsrc=)(?![^>]*\bnonce=)", f'<script nonce="{nonce}"', html)


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(self), payment=(), usb=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
    if SECURE_COOKIES or request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    nonce = g.get("csp_nonce")
    if nonce and resp.mimetype == "text/html":
        resp.headers.setdefault("Content-Security-Policy", app_csp(nonce))
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    if request.path.startswith(("/api/", "/app")):
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    if request.path.startswith("/assets/") and resp.status_code == 200:
        resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        uid = current_uid()
        if not uid:
            session.clear()
            return jsonify(error="Your session has ended. Please log in again."), 401
        return fn(Workspace(uid), *args, **kwargs)
    return wrapper


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise Invalid("Bad request.")
    return data
