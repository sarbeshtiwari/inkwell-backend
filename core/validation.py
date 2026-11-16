"""Input validation helpers and the `Invalid` error shown to users."""

import re
import string
from datetime import date

from core.config import (
    COLUMN_RE,
    CORE_FIELDS,
    CRM_FIELDS,
    IMPORT_MAX_COLS,
    STAGES,
    STATUS_FIELDS,
)


class Invalid(Exception):
    def __init__(self, message, field=None, status=400):
        super().__init__(message)
        self.message, self.field, self.status = message, field, status


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


PHONE_CHARS_RE = re.compile(r"^\+?[\d\s\-().]+$")


def v_text(value, field, label, max_len, required=False, min_len=1):
    value = str(value if value is not None else "").strip()
    if not value:
        if required:
            raise Invalid(f"{label} is required.", field)
        return ""
    if len(value) < min_len:
        raise Invalid(f"{label} must be at least {min_len} characters.", field)
    if len(value) > max_len:
        raise Invalid(f"{label} must be {max_len} characters or fewer.", field)
    return value


def v_email(value, field="email", required=True, label="Email"):
    import unicodedata
    value = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if not value:
        if required:
            raise Invalid(f"{label} is required.", field)
        return ""
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise Invalid("Enter a valid email address, like name@example.com.", field)
    local, _, domain = value.rpartition("@")
    try:  # internationalised domains are stored in their ASCII form, so look-alike letters can't pose as another domain
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise Invalid("Enter a valid email address, like name@example.com.", field)
    if not local.isascii() and any(c.isascii() and c.isalpha() for c in local):
        raise Invalid("That email mixes letters from different alphabets. Please type it again.", field)
    return f"{local}@{domain}"


def v_phone(value, field="phone", required=False):
    value = str(value or "").strip()
    if not value:
        if required:
            raise Invalid("Phone number is required.", field)
        return ""
    digits = re.sub(r"\D", "", value)
    if not PHONE_CHARS_RE.match(value) or not 8 <= len(digits) <= 15:
        raise Invalid("Enter a valid phone number: 8–15 digits, optionally starting with +.", field)
    return value


def safe_int(value, default=0):
    """int() for untrusted input that never raises (bad or giant values give the default)."""
    try:
        return int(str(value)[:12]) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def v_int(value, field, label, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise Invalid(f"{label} must be a whole number.", field)
    if not lo <= n <= hi:
        raise Invalid(f"{label} must be between {lo} and {hi}.", field)
    return n


def v_template_text(value, field, label, max_len, required):
    value = v_text(value, field, label, max_len, required)
    try:
        list(string.Formatter().parse(value))
    except ValueError:
        raise Invalid(f"{label} has an unmatched {{ or }}. Fields look like {{name}}.", field)
    return value


def clean_contact(data, partial=False):
    """Validate a contact's fields; returns only the fields that were given."""
    out = {}
    for k, v in (data or {}).items():
        if k in STATUS_FIELDS or k == "id":
            continue
        if k == "stage":
            if v not in STAGES:
                raise Invalid("Choose a valid stage.", "stage")
            out[k] = v
        elif k == "follow_up":
            v = str(v or "").strip()
            if v:
                try:
                    date.fromisoformat(v)
                except ValueError:
                    raise Invalid("Enter a valid follow-up date.", "follow_up")
            out[k] = v
        elif k == "phone":
            out[k] = v_phone(v)
        elif k == "email":
            out[k] = v_email(v, required=False)
        elif k in ("name", "company"):
            out[k] = v_text(v, k, k.capitalize(), 120)
        else:
            if not COLUMN_RE.match(str(k)):
                raise Invalid(f"Column names can use lowercase letters, numbers and _ only (got “{k}”).", k)
            out[k] = v_text(v, k, k.replace("_", " ").capitalize(), 500)
    extra = [k for k in out if k not in CORE_FIELDS + STATUS_FIELDS + CRM_FIELDS]
    if len(extra) > IMPORT_MAX_COLS:
        raise Invalid(f"A contact can have up to {IMPORT_MAX_COLS} extra columns.")
    if not partial and not out.get("phone") and not out.get("email"):
        raise Invalid("Add a phone number or an email address.", "phone")
    return out
