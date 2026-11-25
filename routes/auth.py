"""Sign-in routes: email and password (the default while email isn't set up), email codes, logout, account info and deletion."""

import hmac
import re
import secrets
import time
from datetime import datetime, timezone

from flask import jsonify, session
from pymongo import ReturnDocument

from core.accounts import (
    delete_user_data,
    end_session,
    find_user,
    is_admin,
    new_user,
    password_of,
    password_ok,
    set_password,
    start_session,
)
from core.config import (
    ALLOW_SIGNUP,
    ALLOWED_DOCS,
    DAILY_LIMIT,
    DEV_OTP,
    MAX_FILE_MB,
    MAX_FILES_MB,
    MIN_WA_DELAY,
    OTP_ATTEMPTS,
    OTP_COOLDOWN,
    OTP_FAILS_PER_DAY,
    OTP_TTL,
    STAGES,
    SYSTEM_MAIL_PER_HOUR,
    TERMS_VERSION,
    WHATSAPP_ENABLED,
)
from core.crypto import HASH_KEY, lookup_hash, seal, unseal
from core.db import M
from core.http import app
from core.mail import send_system_email
from core.pages import can_track_opens
from core.security import body, client_ip, login_required, rate_limited
from core.sending import JOBS
from core.validation import Invalid, v_email, v_text
from core.whatsapp_store import WA_LINKS
from core.workspace import Workspace, seed_workspace


def otp_hash(email_hash, code):
    return hmac.new(HASH_KEY, f"otp:{email_hash}:{code}".encode(), "sha256").hexdigest()


def otp_throttle(eh):
    """The same cooldown and hourly cap for every address, registered or not, so responses don't reveal who has an account."""
    now = time.time()
    row = M.otps.find_one({"_id": "cool:" + eh}, {"sent_at": 1})
    if row and now - row["sent_at"] < OTP_COOLDOWN:
        wait = int(OTP_COOLDOWN - (now - row["sent_at"])) + 1
        raise Invalid(f"Please wait {wait} seconds before requesting another code.", "email", 429)
    if rate_limited(("otp-email", eh), 6, 3600):
        raise Invalid("Too many codes requested for this email. Try again in an hour.", "email", 429)
    if rate_limited(("system-mail",), SYSTEM_MAIL_PER_HOUR, 3600):
        raise Invalid("We're getting a lot of requests right now. Please try again in a few minutes.", "email", 429)
    M.otps.replace_one({"_id": "cool:" + eh}, {"sent_at": now, "expire_at": datetime.fromtimestamp(now + OTP_COOLDOWN, tz=timezone.utc)}, upsert=True)


def issue_otp(email, purpose, extra=None):
    eh = lookup_hash(email)
    now = time.time()
    if rate_limited(("otp-fail-day", eh), 0, 86400, peek=True):
        raise Invalid("This account is temporarily locked after too many wrong codes. Try again tomorrow.", "email", 429)
    otp_throttle(eh)
    code = f"{secrets.randbelow(10**6):06d}"
    send_system_email(
        email, f"{code} is your Inkwell code",
        {"login": f"Your Inkwell login code is {code}.",
         "signup": f"Welcome to Inkwell. Your sign-up code is {code}.",
         "delete": f"Your code to permanently delete your Inkwell account is {code}. "
                   f"If you didn't ask for this, ignore this email."}[purpose]
        + f"\n\nIt expires in {OTP_TTL // 60} minutes. Never share this code with anyone.")
    M.otps.replace_one({"_id": eh}, {"purpose": purpose, "code_hash": otp_hash(eh, code), "data": seal(extra or {}),
                                     "expires": now + OTP_TTL, "attempts": 0, "sent_at": now,
                                     "expire_at": datetime.fromtimestamp(now + OTP_TTL, tz=timezone.utc)}, upsert=True)


def check_otp(email, code, purposes):
    """Verify a code; returns (purpose, extra). Consumes the code on success."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        raise Invalid("Enter the 6-digit code from the email.", "code")
    eh = lookup_hash(email)
    row = M.otps.find_one({"_id": eh})
    if not row or row["purpose"] not in purposes:
        raise Invalid("No active code for this email. Request a new one.", "code")
    if time.time() > row["expires"]:
        M.otps.delete_one({"_id": eh})
        raise Invalid("This code has expired. Request a new one.", "code")
    if row["attempts"] >= OTP_ATTEMPTS:
        raise Invalid("Too many wrong attempts. Request a new code.", "code")
    if not hmac.compare_digest(row["code_hash"], otp_hash(eh, code)):
        # A day-long cap on wrong guesses on top of the per-code limit: slow, patient guessing gets locked out.
        if rate_limited(("otp-fail-day", eh), OTP_FAILS_PER_DAY, 86400):
            M.otps.delete_one({"_id": eh})
            raise Invalid("Too many wrong codes. This account is locked for 24 hours.", "code", 429)
        # Atomic increment, so parallel guesses can't exceed the limit.
        after = M.otps.find_one_and_update({"_id": eh, "code_hash": row["code_hash"]}, {"$inc": {"attempts": 1}},
                                           return_document=ReturnDocument.AFTER)
        left = OTP_ATTEMPTS - (after["attempts"] if after else OTP_ATTEMPTS)
        raise Invalid(f"That code isn't right. {left} attempt{'s' if left != 1 else ''} left."
                      if left > 0 else "Too many wrong attempts. Request a new code.", "code")
    # Delete only if still unused and under the limit, so one code can't be used twice.
    if M.otps.delete_one({"_id": eh, "code_hash": row["code_hash"], "attempts": {"$lt": OTP_ATTEMPTS}}).deleted_count != 1:
        raise Invalid("No active code for this email. Request a new one.", "code")
    return row["purpose"], unseal(row["data"], {})


@app.post("/api/auth/request-code")
def request_code():
    p = body()
    email = v_email(p.get("email"))
    if rate_limited(("otp-ip", client_ip()), 20, 3600):
        raise Invalid("Too many requests from this network. Try again later.", status=429)
    user = find_user(email)
    if p.get("intent") == "signup":
        name = v_text(p.get("name"), "name", "Full name", 80, required=True, min_len=2)
        if not p.get("agree"):
            raise Invalid("Please tick the box to accept the terms.", "agree")
        if not user and not ALLOW_SIGNUP:
            otp_throttle(lookup_hash(email))  # same response as a registered email: don't reveal who has an account
            send_system_email(email, "Inkwell sign-ups are closed", "Thanks for your interest in Inkwell. "
                              "New sign-ups are closed right now; we'll be glad to have you when they reopen.")
            return jsonify(ok=True, resend_in=OTP_COOLDOWN, dev=DEV_OTP)
        # An existing account just gets a login code, so sign-up doesn't reveal who is registered.
        issue_otp(email, "login" if user else "signup", {} if user else {"name": name, "terms": TERMS_VERSION})
    elif user:
        issue_otp(email, "login")
    else:
        # Same response (and the same cooldown) for unknown emails, so this can't be used to check who has an account.
        otp_throttle(lookup_hash(email))
        send_system_email(email, "Inkwell sign-in attempt",
                          "Someone tried to log in to Inkwell with this email, but there's no account for it. "
                          "If it was you, sign up instead.")
    return jsonify(ok=True, resend_in=OTP_COOLDOWN, dev=DEV_OTP)


@app.post("/api/auth/verify-code")
def verify_code():
    p = body()
    email = v_email(p.get("email"))
    if rate_limited(("verify-ip", client_ip()), 30, 900):
        raise Invalid("Too many attempts. Wait a few minutes and try again.", "code", 429)
    purpose, extra = check_otp(email, p.get("code"), ("login", "signup"))
    user = find_user(email)
    if not user:
        if purpose != "signup":
            raise Invalid("No account for this email. Sign up instead.", "email")
        uid = new_user(email, extra.get("name") or email.split("@")[0], terms=extra.get("terms"))
        seed_workspace(Workspace(uid), extra.get("name", ""), email)
    else:
        uid = user["id"]
    M.users.update_one({"_id": uid}, {"$set": {"last_login": time.time()}})
    start_session(uid)
    return jsonify(ok=True)


PW_MIN, PW_MAX = 8, 128


def v_password(p, email="", field="password"):
    pw = p if isinstance(p, str) else ""
    if len(pw) < PW_MIN:
        raise Invalid(f"Use at least {PW_MIN} characters.", field)
    if len(pw) > PW_MAX:
        raise Invalid(f"Use at most {PW_MAX} characters.", field)
    if pw.strip().lower() in (email.lower(), email.split("@")[0].lower()) or len(set(pw)) < 4:
        raise Invalid("That password is too easy to guess. Try a short phrase.", field)
    return pw


def password_throttle(email):
    """Per network and per account: a few guesses, then a wait. Failed tries for one email are capped per day."""
    if rate_limited(("pw-ip", client_ip()), 30, 900):
        raise Invalid("Too many attempts from this network. Wait a few minutes and try again.", status=429)
    if rate_limited(("pw-acct", lookup_hash(email)), 10, 900, peek=True):
        raise Invalid("Too many wrong passwords for this account. Wait 15 minutes and try again.", status=429)


@app.post("/api/auth/signup")
def signup_password():
    p = body()
    email = v_email(p.get("email"))
    name = v_text(p.get("name"), "name", "Full name", 80, required=True, min_len=2)
    pw = v_password(p.get("password"), email)
    if not p.get("agree"):
        raise Invalid("Please tick the box to accept the terms.", "agree")
    if rate_limited(("signup-ip", client_ip()), 10, 3600):
        raise Invalid("Too many sign-ups from this network. Try again later.", status=429)
    if not ALLOW_SIGNUP:
        raise Invalid("New sign-ups are closed right now.", "email", 403)
    if find_user(email):
        # Without email we can't keep this private: say it plainly and point to log in.
        raise Invalid("There's already an account for this email. Log in instead.", "email", 409)
    uid = new_user(email, name, terms=TERMS_VERSION)
    seed_workspace(Workspace(uid), name, email)
    set_password(uid, pw)
    M.users.update_one({"_id": uid}, {"$set": {"last_login": time.time()}})
    start_session(uid)
    return jsonify(ok=True)


@app.post("/api/auth/login")
def login_password():
    p = body()
    email = v_email(p.get("email"))
    pw = p.get("password") if isinstance(p.get("password"), str) else ""
    password_throttle(email)
    user = find_user(email)
    stored = password_of(user["id"]) if user else None
    if not password_ok(stored, pw[:PW_MAX]):
        rate_limited(("pw-acct", lookup_hash(email)), 10, 900)
        if user and not stored:
            raise Invalid("This account doesn't have a password yet. Sign in once with an email code, or ask the "
                          "site's owner to set one, then add a password in Settings.", "password")
        raise Invalid("Wrong email or password.", "password")
    M.users.update_one({"_id": user["id"]}, {"$set": {"last_login": time.time()}})
    start_session(user["id"])
    return jsonify(ok=True)


@app.post("/api/account/password")
@login_required
def change_password(ws):
    """Set a password (first time) or change it (needs the current one)."""
    p = body()
    u = find_user(uid=ws.uid)
    stored = password_of(ws.uid)
    if stored:
        password_throttle(u["email"])
        if not password_ok(stored, p.get("current") if isinstance(p.get("current"), str) else ""):
            rate_limited(("pw-acct", lookup_hash(u["email"])), 10, 900)
            raise Invalid("Your current password isn't right.", "current")
    set_password(ws.uid, v_password(p.get("password"), u["email"]))
    return jsonify(ok=True)


@app.post("/api/auth/logout")
def logout():
    end_session()
    return jsonify(ok=True)


@app.post("/api/auth/logout-everywhere")
@login_required
def logout_everywhere(ws):
    """Sign out every device, including this one (e.g. after using a shared computer)."""
    n = M.sessions.delete_many({"uid": ws.uid}).deleted_count
    session.clear()
    return jsonify(ok=True, devices=n)


@app.get("/api/me")
@login_required
def me(ws):
    u = find_user(uid=ws.uid)
    from infra import rbac
    return jsonify(name=u["name"], email=u["email"], is_admin=is_admin(ws.uid), role=rbac.role_of(ws.uid),
                   permissions=sorted(rbac.permissions_of(ws.uid)), has_password=bool(password_of(ws.uid)), stages=STAGES,
                   can_track_opens=can_track_opens(), daily_limit=DAILY_LIMIT, sent_today=ws.sent_today(),
                   min_wa_delay=MIN_WA_DELAY, max_files_mb=MAX_FILES_MB, max_file_mb=MAX_FILE_MB,
                   allowed_docs=sorted(ALLOWED_DOCS), whatsapp=WHATSAPP_ENABLED)


@app.post("/api/account/delete/request")
@login_required
def delete_account_request(ws):
    issue_otp(find_user(uid=ws.uid)["email"], "delete")
    return jsonify(ok=True, resend_in=OTP_COOLDOWN, dev=DEV_OTP)


@app.post("/api/account/delete")
@login_required
def delete_account(ws):
    p = body()
    u = find_user(uid=ws.uid)
    stored = password_of(ws.uid)
    if stored and "password" in p:  # confirm with the password while email codes aren't available
        password_throttle(u["email"])
        if not password_ok(stored, p.get("password") if isinstance(p.get("password"), str) else ""):
            rate_limited(("pw-acct", lookup_hash(u["email"])), 10, 900)
            raise Invalid("That password isn't right.", "password")
    else:
        check_otp(u["email"], p.get("code"), ("delete",))
    if ws.uid in JOBS:
        JOBS[ws.uid].stop.set()
    if ws.uid in WA_LINKS:
        WA_LINKS[ws.uid].cancel.set()
    delete_user_data(ws.uid)
    session.clear()
    return jsonify(ok=True)
