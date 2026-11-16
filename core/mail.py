"""Email: system emails (login codes), the user's SMTP/IMAP connections and message building."""

import html
import imaplib
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from mimetypes import guess_type

from core.config import (
    DEV_OTP,
    MAIL_FROM,
    PRODUCTION,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)
from core.http import app
from core.validation import Invalid


def send_system_email(to, subject, text):
    if DEV_OTP:
        print(f"\n[Reachout dev] Email to {to}: {subject}\n{text}\n", flush=True)
        return
    try:
        if not SMTP_HOST:
            raise Invalid("Email sending isn't configured on this server. Contact the site owner.", status=503)
        return _send_via_smtp(to, subject, text)
    except Invalid:
        raise
    except Exception as e:
        app.logger.error("System email to a user failed: %s: %s", type(e).__name__, str(e)[:200])
        raise Invalid("We couldn't send the email right now. Please try again in a few minutes.", status=503)


def _send_via_smtp(to, subject, text):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = MAIL_FROM, to, subject
    m.set_content(text)
    server = (smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) if SMTP_PORT == 465
              else smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20))
    try:
        if SMTP_PORT != 465:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(m)
    finally:
        try:
            server.quit()
        except Exception:
            pass


MAIL_PORTS = (25, 465, 587, 2525)


def public_host(host, field="smtp_host"):
    """Refuse mail servers that resolve to private, loopback or link-local addresses. Otherwise anyone
    could make this server connect to internal services (database, cloud metadata) and probe the network."""
    import ipaddress
    import socket
    if os.environ.get("ALLOW_PRIVATE_MAIL_HOSTS") == "1" and not PRODUCTION:
        return host
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise Invalid(f"Couldn't find the mail server {host}. Check the address.", field)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise Invalid("That mail server address isn't allowed. Use your provider's public server, like smtp.gmail.com.", field)
    return host


def tls_context():
    import ssl
    return ssl.create_default_context()  # verifies the server's certificate and name


def smtp_connect(profile):
    host, port = profile.get("smtp_host", ""), int(profile.get("smtp_port") or 465)
    if not host or not profile.get("smtp_password"):
        raise RuntimeError("Email isn't set up yet. Add an app password on the Profile page.")
    if port not in MAIL_PORTS:
        raise RuntimeError("Use port 465 or 587 for your mail server.")
    public_host(host)
    server = (smtplib.SMTP_SSL(host, port, timeout=30, context=tls_context()) if port == 465
              else smtplib.SMTP(host, port, timeout=30))
    if port != 465:
        server.starttls(context=tls_context())
    server.login(profile.get("smtp_user") or profile["email"], profile["smtp_password"])
    return server


LINK_RE = re.compile(r"(https?://[^\s<]+|(?:www\.|linkedin\.com/|github\.com/)[^\s<]+|[\w.+-]+@[\w-]+\.[\w.-]+|\b[\w-]+\.vercel\.app\b)")


def text_to_html(text, pixel_url=None):
    """Plain message -> simple HTML twin: same words, clickable links, optional 1x1 open-tracking image."""
    def link(m):
        v = m.group(0)
        href = f"mailto:{v}" if "@" in v and "/" not in v else (v if v.startswith("http") else "https://" + v)
        return f'<a href="{html.escape(href)}">{html.escape(v)}</a>'
    parts, last = [], 0
    for m in LINK_RE.finditer(text):
        parts.append(html.escape(text[last:m.start()])); parts.append(link(m)); last = m.end()
    parts.append(html.escape(text[last:]))
    body = "".join(parts).replace("\n", "<br>\n")
    pixel = f'<img src="{html.escape(pixel_url)}" width="1" height="1" alt="" style="display:block;border:0">' if pixel_url else ""
    return (f'<!doctype html><html><body style="margin:0;padding:0">'
            f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#1f2937">{body}</div>'
            f"{pixel}</body></html>")


def build_email(profile, to, subject, text, files, pixel_url=None):
    m = EmailMessage()
    m["From"] = f'{profile.get("name", "")} <{profile["email"]}>'
    m["To"] = to
    m["Subject"] = subject
    # Our own Message-ID lets bounce notices be matched back to this exact email.
    m["Message-ID"] = make_msgid(idstring="ro", domain=profile["email"].split("@")[-1])
    m.set_content(text)
    if pixel_url:
        m.add_alternative(text_to_html(text, pixel_url), subtype="html")
    for f in files:
        ctype = guess_type(f.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        m.add_attachment(f.read_bytes(), maintype=main, subtype=sub, filename=f.name)
    return m


def imap_host_for(profile):
    host = (profile.get("smtp_host") or "").lower()
    return {"smtp.gmail.com": "imap.gmail.com", "smtp.office365.com": "outlook.office365.com",
            "smtp-mail.outlook.com": "outlook.office365.com", "smtp.mail.yahoo.com": "imap.mail.yahoo.com",
            "smtp.zoho.com": "imap.zoho.com", "smtp.zoho.in": "imap.zoho.in"}.get(host, host.replace("smtp.", "imap.", 1))


def imap_connect(profile):
    """Log in to the sender's mailbox over IMAP with the saved app password (used read-only)."""
    if not profile.get("smtp_password"):
        raise Invalid("Set up email on the Profile page first.")
    host = public_host(imap_host_for(profile))
    try:
        imap = imaplib.IMAP4_SSL(host, 993, timeout=30, ssl_context=tls_context())
    except OSError:
        raise Invalid(f"Couldn't connect to your mailbox at {host}. Check the mail server address.")
    try:
        imap.login(profile.get("smtp_user") or profile["email"], profile["smtp_password"])
    except imaplib.IMAP4.error:
        raise Invalid("Your mail server refused the login. For Gmail, make sure IMAP is on "
                      "(Gmail → Settings → Forwarding and POP/IMAP) and the app password is current.")
    return imap
