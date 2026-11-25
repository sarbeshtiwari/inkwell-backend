"""WhatsApp: the saved (encrypted) login, its 2-hour expiry, and the browser that links/sends."""

import io
import shutil
import tarfile
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.config import (
    HEADLESS,
    LOGGED_IN,
    MAX_BROWSERS,
    NO_SANDBOX,
    USER_AGENT,
    WA_SESSION_HOURS,
    WA_SKIP,
)
from core.db import M
from core.storage import drop_file, get_file, put_file
from core.workspace import Workspace, bridge_mod


@contextmanager
def wa_profile(ws, keep=lambda: True):
    """Unpack the encrypted WhatsApp login into a private temp folder for one browser session.

    On exit it is packed, encrypted and written back to the database (when keep() is true),
    and the folder is always deleted."""
    folder = Path(tempfile.mkdtemp(prefix="reachout-wa-"))
    folder.chmod(0o700)
    try:
        row = M.wa_sessions.find_one({"_id": ws.uid})
        blob = get_file(row["file_id"]) if row else None
        if blob:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                tar.extractall(folder, filter="data")
        yield folder
        if keep():
            store_wa_folder(ws, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def store_wa_folder(ws, folder):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=3) as tar:
        tar.add(folder, arcname=".", filter=lambda t: None if WA_SKIP & set(Path(t.name).parts) else t)
    store_wa_blob(ws.uid, buf.getvalue())


def store_wa_blob(uid, data):
    file_id = put_file(data, uid, "whatsapp")
    old = M.wa_sessions.find_one_and_update({"_id": uid}, {"$set": {"file_id": file_id, "updated": time.time()},
                                                           "$setOnInsert": {"created": time.time()}}, upsert=True)
    if old:
        drop_file(old["file_id"])


def wa_expires_at(uid):
    row = M.wa_sessions.find_one({"_id": uid}, {"created": 1, "updated": 1})
    if not row:
        return None
    return (row.get("created") or row.get("updated") or time.time()) + WA_SESSION_HOURS * 3600


def expire_wa_sessions(now=None):
    """Delete every saved WhatsApp login older than WA_SESSION_HOURS and tell its owner. Returns how many went.

    A login that is sending or being linked right now is left until that finishes (the next pass removes it)."""
    now = now or time.time()
    cutoff = now - WA_SESSION_HOURS * 3600
    gone = 0
    for row in M.wa_sessions.find({"$or": [{"created": {"$lt": cutoff}},
                                           {"created": {"$exists": False}, "updated": {"$lt": cutoff}}]}):
        uid = row["_id"]
        link = WA_LINKS.get(uid)
        if uid in BUSY or (link and link.state in ("starting", "qr", "saving")):
            continue
        if not M.wa_sessions.find_one_and_delete({"_id": uid, "file_id": row["file_id"]}):
            continue  # changed meanwhile
        drop_file(row["file_id"])
        ws = Workspace(uid)
        ws.update_settings(wa_connected=False, wa_linked_at="")
        bridge_mod().notify(uid, "WhatsApp was unlinked automatically",
                            f"For your security, saved WhatsApp logins are deleted {WA_SESSION_HOURS} hours after linking. "
                            "Link it again when you want to send.", "/app/whatsapp", "whatsapp")
        gone += 1
    return gone


def wa_expiry_watcher():
    while True:
        time.sleep(60)
        try:
            n = expire_wa_sessions()
            if n:
                print(f"[Inkwell] removed {n} expired WhatsApp login(s).", flush=True)
        except Exception as e:
            print(f"[Inkwell] WhatsApp expiry error: {e}", flush=True)


SLOTS = threading.BoundedSemaphore(MAX_BROWSERS)


BUSY, BUSY_LOCK = set(), threading.Lock()


@contextmanager
def browser_slot(uid, say=None):
    """One WhatsApp browser per user, at most MAX_BROWSERS on the server."""
    with BUSY_LOCK:
        if uid in BUSY:
            raise RuntimeError("WhatsApp is busy for your account (linking or sending). Try again shortly.")
        BUSY.add(uid)
    try:
        if not SLOTS.acquire(timeout=1):
            if say:
                say("Server is busy – waiting for a free WhatsApp slot…")
            SLOTS.acquire()
        try:
            yield
        finally:
            SLOTS.release()
    finally:
        with BUSY_LOCK:
            BUSY.discard(uid)


def open_whatsapp(pw, folder):
    args = ["--disable-dev-shm-usage"] + (["--no-sandbox"] if NO_SANDBOX else [])
    ctx = pw.chromium.launch_persistent_context(str(folder), headless=HEADLESS, user_agent=USER_AGENT,
                                                viewport={"width": 1280, "height": 900}, args=args)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://web.whatsapp.com")
    return ctx, page


class WALink:
    def __init__(self):
        self.state, self.error, self.qr = "starting", "", None
        self.cancel = threading.Event()


WA_LINKS = {}


def link_whatsapp(ws, link):
    try:
        from playwright.sync_api import sync_playwright
        with browser_slot(ws.uid), wa_profile(ws, keep=lambda: link.state == "saving") as folder, \
                sync_playwright() as pw:
            ctx, page = open_whatsapp(pw, folder)
            try:
                deadline = time.time() + 180
                while time.time() < deadline and not link.cancel.is_set():
                    if page.locator(LOGGED_IN).count():
                        link.state = "saving"
                        page.wait_for_timeout(8000)  # let WhatsApp finish saving the new login
                        break
                    qr = page.locator("canvas").first
                    if qr.count() and qr.is_visible():
                        link.qr, link.state = qr.screenshot(), "qr"
                    page.wait_for_timeout(1500)
                else:
                    link.state, link.error = "error", ("Cancelled." if link.cancel.is_set()
                                                       else "The QR code expired. Try again.")
            finally:
                ctx.close()
        # wa_profile has encrypted and stored the login by now.
        if link.state == "saving":
            M.wa_sessions.update_one({"_id": ws.uid}, {"$set": {"created": time.time()}})  # a re-link starts a new 2 hours
            ws.update_settings(wa_connected=True, wa_linked_at=datetime.now().isoformat(timespec="seconds"))
            link.state = "connected"
    except Exception as e:
        link.state, link.error = "error", str(e).splitlines()[0]
