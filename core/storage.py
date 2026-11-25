"""File storage: Cloudinary (encrypted, private) with GridFS as the fallback."""

import base64
import hashlib
import re
import time
import uuid

import requests
from cryptography.fernet import InvalidToken
from gridfs.errors import NoFile

from core.config import CLOUDINARY_URL
from core.crypto import fernet, unseal
from core.db import M


class Cloudinary:
    """Minimal client for Cloudinary's REST API (signed requests; the secret never leaves the server)."""

    PART = 4 * 1024 * 1024  # the free plan takes raw files up to 10 MB; smaller parts also survive slow uplinks

    def __init__(self, url):
        m = re.fullmatch(r"cloudinary://([^:@/]+):([^@/]+)@([\w-]+)/?", url)
        if not m:
            raise ValueError("CLOUDINARY_URL should look like cloudinary://<api key>:<api secret>@<cloud name>")
        self.key, self.secret, self.cloud = m.groups()
        self.api = f"https://api.cloudinary.com/v1_1/{self.cloud}"

    def _signed(self, params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params["timestamp"] = int(time.time())
        payload = "&".join(f"{k}={params[k]}" for k in sorted(params))
        params["signature"] = hashlib.sha1((payload + self.secret).encode()).hexdigest()
        params["api_key"] = self.key
        return params

    def _check(self, r):
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                msg = r.text[:200]
            raise RuntimeError(f"Cloudinary {r.status_code}: {msg}")
        return r.json() if r.content else {}

    def upload(self, data, public_id, kind="raw", delivery="private"):
        for attempt in range(3):
            try:
                r = requests.post(f"{self.api}/{kind}/upload", data=self._signed({"public_id": public_id, "type": delivery}),
                                  files={"file": (public_id.rsplit("/", 1)[-1], data)}, timeout=(15, 300))
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        return self._check(r)

    def download(self, public_id):
        r = requests.get(f"{self.api}/raw/download", params=self._signed({"public_id": public_id, "type": "private"}),
                         timeout=(15, 300))
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            self._check(r)
        return r.content

    def destroy(self, public_id, kind="raw", delivery="private"):
        r = requests.post(f"{self.api}/{kind}/destroy",
                          data=self._signed({"public_id": public_id, "type": delivery, "invalidate": "true"}), timeout=30)
        return self._check(r).get("result") == "ok"


CLOUD = Cloudinary(CLOUDINARY_URL) if CLOUDINARY_URL else None


def put_file(data, uid, kind):
    """Store encrypted bytes; returns the reference to keep in MongoDB.

    With Cloudinary: {"cld": [public ids...], "size": n}, private raw objects that only this server can fetch
    (still Fernet-encrypted, so Cloudinary never sees the contents). Otherwise a GridFS file id."""
    sealed = fernet.encrypt(data)
    if not CLOUD:
        return M.files.upload_from_stream(uuid.uuid4().hex, sealed, metadata={"uid": uid, "kind": kind})
    base = f"reachout/{uid}/{kind}-{uuid.uuid4().hex}"
    parts = [sealed[i:i + CLOUD.PART] for i in range(0, len(sealed), CLOUD.PART)] or [b""]
    ids = []
    try:
        for n, part in enumerate(parts):
            pid = base if len(parts) == 1 else f"{base}.{n}"
            CLOUD.upload(part, pid)
            ids.append(pid)
    except Exception:
        drop_file({"cld": ids})  # no half-written files left behind
        raise
    return {"cld": ids, "size": len(sealed)}


def get_file(ref):
    try:
        if isinstance(ref, dict):
            parts = [CLOUD.download(pid) for pid in ref.get("cld", [])] if CLOUD else [None]
            if any(p is None for p in parts):
                return None
            return fernet.decrypt(b"".join(parts))
        return fernet.decrypt(M.files.open_download_stream(ref).read())
    except (NoFile, InvalidToken):
        return None


def drop_file(ref):
    if isinstance(ref, dict):
        for pid in ref.get("cld", []):
            try:
                CLOUD and CLOUD.destroy(pid)
            except Exception as e:  # a leftover object costs a little space; never block the user's action
                print(f"[Inkwell] Couldn't remove {pid} from Cloudinary: {e}", flush=True)
        return
    try:
        M.files.delete(ref)
    except NoFile:
        pass


def move_files_to_cloudinary(log=print):
    """One-off: copy every GridFS file to Cloudinary, point its record at the copy, then remove the GridFS one.

    Each copy is downloaded again and compared before anything is deleted. Safe to run more than once."""
    if not CLOUD:
        raise SystemExit("Set CLOUDINARY_URL first.")
    moved = 0
    for coll in (M.documents, M.wa_sessions):
        for row in coll.find({"file_id": {"$not": {"$type": "object"}}}):
            old = row["file_id"]
            data = get_file(old)
            if data is None:
                log(f"  skipped {coll.name} {row['_id']}: GridFS file missing or unreadable")
                continue
            uid = row.get("uid") or row["_id"]
            ref = put_file(data, uid, "document" if coll.name == "documents" else "whatsapp")
            if get_file(ref) != data:
                drop_file(ref)
                raise SystemExit(f"Verification failed for {coll.name} {row['_id']}; nothing was changed for it.")
            if coll.update_one({"_id": row["_id"], "file_id": old}, {"$set": {"file_id": ref}}).modified_count:
                drop_file(old)
                moved += 1
                log(f"  moved {coll.name} {row['_id']} ({len(data) / 1e6:.2f} MB)")
            else:
                drop_file(ref)  # the record changed meanwhile; keep the newer one
    for row in M.site_images.find({"data": {"$exists": True}}):
        raw = base64.b64decode(unseal(row["data"], "") or "")
        if not raw:
            continue
        up = CLOUD.upload(raw, f"reachout/{row['uid']}/site-{row['_id']}", kind="image", delivery="upload")
        M.site_images.update_one({"_id": row["_id"]}, {"$set": {"url": up["secure_url"], "public_id": up["public_id"]},
                                                         "$unset": {"data": ""}})
        moved += 1
        log(f"  moved site image {row['_id']}")
    left = M.db["files.files"].count_documents({})
    log(f"Done: {moved} file(s) moved; {left} file(s) still in GridFS.")
    return moved
