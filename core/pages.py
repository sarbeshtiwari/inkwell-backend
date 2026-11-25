"""Serving HTML pages: cookies, visitor source tracking, the page template filler."""

import base64
import html
import re

from flask import jsonify, make_response, request

from core.config import JURISDICTION, OPERATOR_NAME, PRODUCTION, SITE_URL, WEB
from core.crypto import seal, unseal
from core.security import add_nonce

PREF_MAX_AGE = 365 * 86400


THEMES = ("system", "light", "dark")


def secure_cookies():
    return PRODUCTION or request.is_secure


def set_cookie(resp, name, value, max_age=PREF_MAX_AGE):
    # Every cookie is HttpOnly (scripts can't read it) and Secure whenever the site is served over HTTPS.
    resp.set_cookie(name, value, max_age=max_age, path="/", secure=secure_cookies(), httponly=True, samesite="Lax")


def read_source():
    """Where this visitor first came from (stored encrypted in an HttpOnly cookie)."""
    token = request.cookies.get("ro_src", "")
    return unseal(token.encode(), {}) if token else {}


def remember_source(resp):
    """On public pages: record the first external referrer and any utm_* tags, once per visitor."""
    utm = {k: v[:100] for k, v in request.args.items() if k.startswith("utm_") and k in (
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")}
    ref = request.referrer or ""
    external = ref and not ref.startswith(request.host_url)
    if (utm or external) and not request.cookies.get("ro_src"):
        set_cookie(resp, "ro_src", seal({"utm": utm, "referrer": ref[:300] if external else "",
                                         "landing": request.path}).decode(), max_age=90 * 86400)


def can_track_opens():
    """Recipients' mail apps must be able to reach the tracking image, so the app needs a public HTTPS address."""
    host = re.sub(r"^https?://", "", site_url()).split("/")[0].split(":")[0].lower()
    return site_url().startswith("https://") and host not in ("localhost", "127.0.0.1", "0.0.0.0") \
        and not host.endswith(".local") and not re.match(r"^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.", host)


def site_url():
    return SITE_URL or request.host_url.rstrip("/")


def asset_version(match):
    path = WEB / "assets" / match.group(1)
    v = int(path.stat().st_mtime) if path.exists() else 0
    return f'/assets/{match.group(1)}?v={v:x}"'


def page(name, status=200, **values):
    if not (WEB / name).exists():
        # API-only deployment (the backend repo on its own; pages live on Netlify): no HTML pages here.
        if status == 404 or name == "404.html":
            return jsonify(error="Not found."), 404
        return jsonify(error="This server only provides the Inkwell API."), 404
    doc = (WEB / name).read_text(encoding="utf-8").replace("{{SITE_URL}}", site_url())
    doc = doc.replace("{{OPERATOR}}", html.escape(OPERATOR_NAME)).replace("{{JURISDICTION}}", html.escape(JURISDICTION))
    # Preferences come from HttpOnly cookies and are handed to the page as attributes on <html>.
    theme = request.cookies.get("ro_theme")
    theme = theme if theme in THEMES else "system"
    flags = " data-popup=\"done\"" if request.cookies.get("ro_popup") else ""
    if request.cookies.get("ro_sidebar") == "collapsed":
        flags += " data-sidebar=\"collapsed\""
    closed = [g for g in (request.cookies.get("ro_nav") or "").split(".") if g in NAV_GROUPS]
    if closed:
        flags += f' data-nav-closed="{" ".join(closed)}"'
    doc = doc.replace('<html lang="en">', f'<html lang="en" data-theme-choice="{theme}"{flags}>', 1)
    # Versioned asset URLs, so browsers can cache them for a week yet pick up changes immediately.
    doc = re.sub(r'/assets/([\w.-]+\.(?:css|js))"', asset_version, doc)
    for key, value in values.items():
        doc = doc.replace("{{" + key + "}}", value)
    doc = add_nonce(doc)
    resp = make_response(doc, status)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"  # pages carry per-visitor preferences
    if name in ("landing.html", "contact.html"):
        remember_source(resp)
    return resp


NAV_GROUPS = ("outreach", "career", "website")  # sidebar sections that can be folded away


PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
