"""The Flask application object, its session cookie settings and error reporting (Sentry)."""

import os

from flask import Flask

from core.config import BACKEND, MAX_FILE_MB, SECURE_COOKIES
from core.crypto import _derive

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")


if SENTRY_DSN:
    # Error reports only: no request bodies, cookies or personal data are sent (send_default_pii=False),
    # and headers that could carry secrets are scrubbed before anything leaves the server.
    import sentry_sdk

    def _scrub(event, _hint):
        req = event.get("request") or {}
        for k in list((req.get("headers") or {})):
            if k.lower() in ("cookie", "authorization", "x-nf-sign", "x-nf-client-connection-ip", "x-forwarded-for"):
                req["headers"][k] = "[removed]"
        req.pop("data", None)
        req.pop("cookies", None)
        from infra.redact import secrets
        for key in ("message", "logentry", "exception", "extra", "breadcrumbs"):
            if key in event:
                event[key] = secrets(event[key])
        return event
    sentry_sdk.init(dsn=SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.0, before_send=_scrub,
                    environment="production" if os.environ.get("APP_ENV") == "production" else "development")


app = Flask("app", root_path=str(BACKEND), static_folder=None)


def _install_redaction():
    """Every log record (Flask, werkzeug, libraries) passes through secret redaction before it is written."""
    import logging

    from infra.redact import RedactingFilter
    f = RedactingFilter()
    for name in ("", "app", "werkzeug", "gunicorn.error", "gunicorn.access"):
        logging.getLogger(name).addFilter(f)
    for h in logging.getLogger().handlers + app.logger.handlers:
        h.addFilter(f)
    from infra.redact import install_stream_redaction
    install_stream_redaction()  # print() logs and tracebacks too


_install_redaction()


app.config.update(
    SECRET_KEY=_derive("reachout-session-v1").hex(),
    MAX_CONTENT_LENGTH=(MAX_FILE_MB * 3) * 1024 * 1024,
    SESSION_COOKIE_NAME="__Host-ro_session" if SECURE_COOKIES else "ro_session",  # __Host-: HTTPS only, this host only
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)


if os.environ.get("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
