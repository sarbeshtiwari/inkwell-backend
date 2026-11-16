"""Encryption: the data key (SECRET_KEY / data/secret.key), seal/unseal and keyed lookup hashes."""

import base64
import hashlib
import hmac
import json
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from core.config import DATA


def load_secret():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "secret.key"
    if not path.exists():
        # Created owner-only atomically (no moment where other accounts on the machine could read it).
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    if path.stat().st_mode & 0o077:
        path.chmod(0o600)
    return path.read_text().strip()


SECRET = load_secret()


def _derive(label):
    return hashlib.sha256(f"{label}:{SECRET}".encode()).digest()


fernet = Fernet(base64.urlsafe_b64encode(_derive("reachout-data-v1")))


HASH_KEY = _derive("reachout-lookup-v1")


def lookup_hash(value):
    return hmac.new(HASH_KEY, value.encode(), "sha256").hexdigest()


def seal(obj):
    return fernet.encrypt(json.dumps(obj, ensure_ascii=False).encode())


class Undecryptable(Exception):
    """Stored data that can't be decrypted: wrong/rotated key or corruption. Never treated as empty."""


def unseal(blob, default=None, strict=False):
    try:
        return json.loads(fernet.decrypt(blob)) if blob else default
    except (InvalidToken, ValueError):
        if strict:
            raise Undecryptable("stored data could not be decrypted")
        return default
