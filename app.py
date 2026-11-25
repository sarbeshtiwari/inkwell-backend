#!/usr/bin/env python3
"""
Reachout – multi-user web app for personalised outreach over WhatsApp and email.

Storage: everything lives in MongoDB (MONGODB_URI, database MONGODB_DB). Every
user record, contact list, template, profile, setting, history entry, enquiry,
uploaded file and WhatsApp login is encrypted (Fernet / AES-128-CBC + HMAC)
before it is written; files go to Cloudinary (CLOUDINARY_URL) as private,
still-encrypted objects, with only a reference kept in MongoDB (GridFS when
Cloudinary isn't configured). Email addresses are looked up through a
keyed HMAC, never stored in the clear.

Auth: passwordless. Every sign-up and log-in is confirmed with a 6-digit code
sent to the user's email (expires in 10 minutes, 5 attempts, 60 s resend
cooldown). Configure SMTP_* env vars for sending; without them, local runs
print the code in the terminal.

Layout: core/ holds the building blocks (settings, encryption, database, file storage, workspace, request
security, email, sending...), routes/ the HTTP API and pages, features/ the larger product areas. This file
only wires them together and starts the background workers.

Local:       .venv/bin/python backend/app.py    -> http://127.0.0.1:5050
Production:  see README.md (Docker + gunicorn, one worker process).
"""

import os
import sys
import threading

import migrations
from core import (accounts, bounces, config, crypto, db, http, mail, pages, security, sending, sentmail, storage,
                  tables, validation, whatsapp_store, workspace)
from core.accounts import init_storage
from core.bounces import bounce_watcher
from core.config import DEV_OTP, env_int
from core.http import app
from core.storage import move_files_to_cloudinary
from core.whatsapp_store import wa_expiry_watcher
# Importing a route module registers its URLs on `app` (same order as before the split).
from routes import auth, contacts, site_pages, workspace_api, send, whatsapp, leads

_MODULES = (config, crypto, http, db, storage, validation, workspace, accounts, security, mail, tables, whatsapp_store,
            bounces, sentmail, sending, pages, migrations, auth, contacts, site_pages, workspace_api, send, whatsapp, leads)


def __getattr__(name):
    """Feature modules reach shared helpers through bridge.C (this module), e.g. C().M or C().Invalid.
    Look each name up live in the module that defines it, so it's always the real, current object."""
    for mod in _MODULES:
        if name in vars(mod):
            return vars(mod)[name]
    raise AttributeError(f"module 'app' has no attribute {name!r}")


init_storage()

import bridge  # noqa: E402
from features import apps as feature_apps  # noqa: E402
from features import inbox as feature_inbox  # noqa: E402
from features import github as feature_github  # noqa: E402
from features import jobs as feature_jobs  # noqa: E402
from features import linkedin as feature_linkedin  # noqa: E402
from features import notify as feature_notify  # noqa: E402
from features import finder as feature_finder  # noqa: E402
from features import ai_jobs as feature_ai_jobs  # noqa: E402
from features import extension as feature_extension  # noqa: E402
from features import portfolio as feature_portfolio  # noqa: E402
from features import site as feature_site  # noqa: E402
from features import replies as feature_replies  # noqa: E402
from features import updater as feature_updater  # noqa: E402
from features import nearby as feature_nearby  # noqa: E402
from features.learning import course as _learning_course, briefing as feature_briefing  # noqa: E402,F401
from features.learning.common import bp as learning_bp  # noqa: E402
from features.research.company import bp as research_bp  # noqa: E402

bridge.setup(sys.modules[__name__])
feature_notify.init()
feature_inbox.ensure_indexes()
feature_replies.init()
for _bp in (feature_github.bp, feature_jobs.bp, feature_linkedin.bp, feature_apps.bp, feature_notify.bp, feature_inbox.bp, feature_replies.bp, feature_portfolio.bp, feature_site.bp, feature_finder.bp, feature_ai_jobs.bp, feature_extension.bp, feature_updater.bp, feature_nearby.bp, learning_bp, research_bp):
    app.register_blueprint(_bp)

if os.environ.get("BOUNCE_WATCH", "1") == "1":
    threading.Thread(target=bounce_watcher, daemon=True, name="bounce-watcher").start()
    threading.Thread(target=wa_expiry_watcher, daemon=True, name="wa-expiry").start()
    feature_jobs.start_workers()
    feature_apps.start_workers()
    feature_replies.start_workers()
    bridge.every(3600, feature_ai_jobs.weekly_digest_all, "ai-weekly-digest")  # sends on Mondays, once a week
    bridge.every(300, feature_briefing.daily_tick, "learning-briefing")  # today's briefing, once a day after 07:00

if __name__ == "__main__":
    if sys.argv[1:] == ["move-files-to-cloudinary"]:
        move_files_to_cloudinary()
        sys.exit(0)
    port = env_int("PORT", 5050)
    if DEV_OTP:
        print("Email isn't configured (SMTP_HOST), so login codes will be printed here.", flush=True)
    if os.environ.get("OPEN_BROWSER", "1") == "1":
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, threaded=True)
