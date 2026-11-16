"""Public pages and static files: landing, legal pages, sitemap, the app shell, assets, health check."""

import html
import re
import time
from datetime import datetime

from features import site as feature_site
from flask import jsonify, make_response, redirect, request, send_from_directory

from core.accounts import current_uid
from core.config import LANDING_URL, SITE_URL, WEB
from core.crypto import lookup_hash
from core.db import M
from core.http import SENTRY_DSN, app
from core.pages import NAV_GROUPS, PIXEL, THEMES, page, set_cookie, site_url
from core.security import body, client_ip, rate_limited
from core.validation import Invalid
from core.workspace import Workspace


@app.get("/api/prefs")
def read_prefs():
    """Saved look (theme, sidebar, folded menu groups) for pages that aren't rendered by this server."""
    theme = request.cookies.get("ro_theme")
    return jsonify(theme=theme if theme in THEMES else "system",
                   sidebar="collapsed" if request.cookies.get("ro_sidebar") == "collapsed" else "open",
                   nav_closed=[x for x in (request.cookies.get("ro_nav") or "").split(".") if x in NAV_GROUPS])


@app.post("/api/prefs")
def save_prefs():
    p = body()
    resp = jsonify(ok=True)
    if "theme" in p:
        if p["theme"] not in THEMES:
            raise Invalid("Unknown theme.", "theme")
        set_cookie(resp, "ro_theme", p["theme"])
    if p.get("popup_seen"):
        set_cookie(resp, "ro_popup", "1")
    if "sidebar" in p:
        if p["sidebar"] not in ("open", "collapsed"):
            raise Invalid("Unknown sidebar state.", "sidebar")
        set_cookie(resp, "ro_sidebar", p["sidebar"])
    if "nav_closed" in p:
        groups = [g for g in (p["nav_closed"] or []) if g in NAV_GROUPS] if isinstance(p["nav_closed"], list) else []
        set_cookie(resp, "ro_nav", ".".join(groups))
    return resp


@app.get("/")
def landing():
    return page("landing.html")


@app.get("/contact")
def contact_page():
    return page("contact.html")


@app.get("/privacy")
def privacy_page():
    return page("privacy.html")


@app.get("/terms")
def terms_page():
    return page("terms.html")


@app.get("/robots.txt")
def robots():
    if LANDING_URL and LANDING_URL != SITE_URL:  # app site: the landing site has its own robots.txt
        text = (f"User-agent: *\nAllow: /signup\nAllow: /p/\nDisallow: /app\nDisallow: /api/\nDisallow: /login\n"
                f"Disallow: /site-preview/\n\nSitemap: {site_url()}/sitemap.xml\n")
        return text, 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=86400"}
    text = (f"User-agent: *\nAllow: /$\nAllow: /signup\nAllow: /contact\nAllow: /assets/\n"
            f"Allow: /p/\nDisallow: /app\nDisallow: /api/\n\nSitemap: {site_url()}/sitemap.xml\n")
    return text, 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=86400"}


@app.get("/sitemap.xml")
def sitemap():
    landing_file = WEB / "landing.html"
    day = (datetime.fromtimestamp(landing_file.stat().st_mtime) if landing_file.exists() else datetime.now()).date().isoformat()
    pages = (("/signup", "0.6"),) if LANDING_URL and LANDING_URL != SITE_URL else (("/", "1.0"), ("/contact", "0.7"), ("/signup", "0.6"), ("/privacy", "0.3"), ("/terms", "0.3"))
    urls = "".join(f"<url><loc>{site_url()}{path}</loc><lastmod>{day}</lastmod><priority>{pri}</priority></url>"
                   for path, pri in pages)
    urls += "".join(f"<url><loc>{html.escape(loc)}</loc><lastmod>{mod}</lastmod><priority>0.8</priority></url>" for loc, mod in feature_site.sitemap_entries())
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "public, max-age=86400"}


@app.get("/site.webmanifest")
def manifest():
    return jsonify(name="Reachout", short_name="Reachout", start_url="/app", display="standalone",
                   background_color="#fbfaf7", theme_color="#0f6b54",
                   icons=[{"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png"}])


@app.errorhandler(404)
def not_found(_):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return page("404.html", 404)


# The workspace and sign-in screens are the React app in web/dist (built from frontend/ with `npm run build`).


@app.get("/login")
@app.get("/signup")
def auth_page():
    if current_uid():
        return redirect("/app")
    signup = request.path == "/signup"
    title, robots = ("Create a free account · Reachout", "index, follow") if signup else ("Log in · Reachout", "noindex, follow")
    return page("dist/index.html", TITLE=title, ROBOTS=robots)


@app.get("/app")
@app.get("/app/<path:subpath>")
def app_page(subpath=""):
    """The workspace. Pages have real paths (/app/campaign, /app/applications/<id>) handled by the React router."""
    if not current_uid():
        return redirect("/login")
    return page("dist/index.html", TITLE="Reachout", ROBOTS="noindex, nofollow")


@app.get("/dist/<path:name>")
def dist_assets(name):
    resp = send_from_directory(WEB / "dist", name)
    if name.startswith("static/"):  # hashed file names: safe to cache for a long time
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.get("/downloads/<path:name>")
def downloads(name):
    """Files people download from the app (the browser extension), same address as on Netlify."""
    return send_from_directory(WEB / "dist" / "downloads", name, as_attachment=True)


@app.get("/assets/<path:name>")
def assets(name):
    return send_from_directory(WEB / "assets", name)


@app.get("/o/<token>.gif")
def open_pixel(token):
    """Record that a tracked email was opened (its images were loaded)."""
    if re.fullmatch(r"[\w-]{16,64}", token):
        h, now = lookup_hash("open:" + token), time.time()
        first = M.send_log.find_one_and_update({"track": h, "first_open": None}, {"$set": {"first_open": now}})
        M.send_log.update_one({"track": h}, {"$inc": {"opens": 1}, "$set": {"last_open": now}})
        if first and first.get("rid"):
            ws = Workspace(first["uid"])
            if ws.mark_opened(first["rid"], datetime.fromtimestamp(now).isoformat(timespec="seconds")):
                secs = int(now - first["ts"])
                ws.add_event(first["rid"], "opened", "Email opened" + (" (within a minute of sending, possibly an automatic security scan)" if secs < 60 else ""))
    resp = make_response(PIXEL)
    resp.headers.update({"Content-Type": "image/gif", "Cache-Control": "no-store, no-cache, must-revalidate, private",
                         "X-Robots-Tag": "noindex"})
    return resp


@app.post("/api/client-errors")
def client_error():
    """Crashes in the browser, reported to the server log (and Sentry when configured)."""
    if rate_limited(("client-error", client_ip()), 20, 3600):
        return jsonify(ok=True)
    p = request.get_json(silent=True) or {}
    msg = str(p.get("message") or "")[:300]
    where = str(p.get("where") or "")[:200]
    stack = str(p.get("stack") or "")[:2000]
    app.logger.warning("Browser error at %s: %s", where, msg)
    if SENTRY_DSN:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("source", "browser")
            scope.set_context("browser", {"where": where, "stack": stack})
            sentry_sdk.capture_message(f"Browser: {msg}", level="error")
    return jsonify(ok=True)


@app.get("/healthz")
def healthz():
    """For uptime monitors: the app is up AND can reach its database."""
    try:
        M.client.admin.command("ping")
    except Exception:
        return "database unreachable", 503
    return "ok"
