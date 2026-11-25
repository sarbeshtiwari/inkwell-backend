"""WhatsApp: the saved (encrypted) login, its 2-hour expiry, and the browser that links/sends."""

import base64
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
from infra import locks, operations


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
        if browser_busy(uid) or operations.running(uid, "wa_link"):
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


def wa_expiry_tick():
    """Every minute (bridge.every, one process at a time). Each deletion is atomic, so a login is removed and its
    owner told exactly once even if two passes overlap."""
    n = expire_wa_sessions()
    if n:
        print(f"[Inkwell] removed {n} expired WhatsApp login(s).", flush=True)


SLOTS = threading.BoundedSemaphore(MAX_BROWSERS)


def browser_busy(uid):
    """True while any process has a WhatsApp browser open for this account."""
    return locks.holder("wa-browser", uid) is not None


@contextmanager
def browser_slot(uid, say=None):
    """One WhatsApp browser per account across every process (shared lease), and at most MAX_BROWSERS browsers on
    this machine (a local limit: it protects this machine's memory)."""
    lease = locks.try_acquire("wa-browser", uid)
    if lease is None:
        raise RuntimeError("WhatsApp is busy for your account (linking or sending). Try again shortly.")
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
        lease.release()


def open_whatsapp(pw, folder):
    args = ["--disable-dev-shm-usage"] + (["--no-sandbox"] if NO_SANDBOX else [])
    ctx = pw.chromium.launch_persistent_context(str(folder), headless=HEADLESS, user_agent=USER_AGENT,
                                                viewport={"width": 1280, "height": 900}, args=args)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://web.whatsapp.com")
    return ctx, page


def link_whatsapp(op, ws):
    """Show a QR code until the user scans it (or 3 minutes pass, or they cancel), then store the login.
    Progress, including the QR image, lives in the shared record (encrypted) so any process can show it;
    the image is erased when linking ends."""
    stop = operations.CancelEvent(op)
    stage = {"value": "starting"}

    def set_stage(value, qr=None):
        stage["value"] = value
        op.update(public={"stage": value}, private={"qr": base64.b64encode(qr).decode() if qr else ""})
    try:
        from playwright.sync_api import sync_playwright
        with browser_slot(ws.uid), wa_profile(ws, keep=lambda: stage["value"] == "saving") as folder, \
                sync_playwright() as pw:
            ctx, page = open_whatsapp(pw, folder)
            try:
                deadline = time.time() + 180
                while time.time() < deadline and not stop.is_set():
                    if page.locator(LOGGED_IN).count():
                        set_stage("saving")
                        page.wait_for_timeout(8000)  # let WhatsApp finish saving the new login
                        break
                    qr = page.locator("canvas").first
                    if qr.count() and qr.is_visible():
                        set_stage("qr", qr.screenshot())
                    page.wait_for_timeout(1500)
                else:
                    cancelled = stop.is_set()
                    op.update(public={"stage": "error"}, private={"qr": ""})
                    op.finish(operations.CANCELLED if cancelled else operations.FAILED,
                              error="Cancelled." if cancelled else "The QR code expired. Try again.")
                    return
            finally:
                ctx.close()
        # wa_profile has encrypted and stored the login by now.
        M.wa_sessions.update_one({"_id": ws.uid}, {"$set": {"created": time.time()}})  # a re-link starts a new 2 hours
        ws.update_settings(wa_connected=True, wa_linked_at=datetime.now().isoformat(timespec="seconds"))
        op.finish(operations.DONE, public={"stage": "connected"}, private={"qr": ""})
    except (operations.Cancelled, locks.LeaseLost):
        raise
    except Exception as e:
        op.finish(operations.FAILED, public={"stage": "error"}, private={"qr": ""}, error=str(e).splitlines()[0])


def link_view(uid):
    """(stage, error, qr bytes or None) of this account's latest linking attempt."""
    v = operations.current(uid, "wa_link")
    if not v:
        return "", "", None
    if v["state"] == operations.RUNNING:
        qr = v.get("qr") or ""
        return v.get("stage") or "starting", "", base64.b64decode(qr) if qr else None
    if v["state"] in (operations.FAILED, operations.CANCELLED, operations.INTERRUPTED):
        return "error", v.get("error") or "Linking stopped. Try again.", None
    return v.get("stage") or "connected", "", None
