"""Remove secrets (and optionally personal details) from anything about to be logged or stored as telemetry.

`secrets()` is always applied: API keys, access tokens, bearer/basic credentials, passwords in URLs or key=value form,
session cookies, private keys and JWTs. `pii()` additionally masks email addresses and phone numbers, for places
where content must not be visible (telemetry summaries, admin metadata views). Both work on strings and on nested
dicts/lists; keys whose *name* says they hold a secret are replaced whole."""

import re

MASK = "[redacted]"

# Field names whose values are always secret, whatever they look like.
SECRET_KEYS = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|apikey|auth(orization)?|cookie|session|credential|"
                         r"private[_-]?key|smtp_password|access[_-]?key|refresh|bearer|signature|x-nf-sign)", re.I)

PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:sk-ant-[A-Za-z0-9_\-]{20,}|sk-(?:proj-)?[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
               r"github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_\-]{30,}|xox[baprs]-[A-Za-z0-9\-]{10,}|"
               r"tvly-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ya29\.[A-Za-z0-9_\-]{20,}|gsk_[A-Za-z0-9]{20,})"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=\-]{12,}"),
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{1,20}://[^:/\s@]+:)[^@\s/]+(@)"),  # user:password@ in any URL scheme
    re.compile(r"(?i)\b((?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|client[_-]?secret|"
               r"session|cookie|ro_session|__Host-ro_session)\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"),
]
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{8,}\d)(?!\w)")


def _str_secrets(s):
    for p in PATTERNS:
        if p.groups >= 2 and "://" in p.pattern:
            s = p.sub(lambda m: m.group(1) + MASK + m.group(2), s)
        elif p.groups >= 2:
            s = p.sub(lambda m: m.group(1) + MASK, s)
        else:
            s = p.sub(MASK, s)
    return s


def secrets(value, _depth=0):
    """A copy of `value` with secrets removed. Strings, dicts, lists and tuples; other types are returned as-is."""
    if _depth > 12:
        return MASK
    if isinstance(value, str):
        return _str_secrets(value)
    if isinstance(value, bytes):
        return MASK
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and SECRET_KEYS.search(k) and v not in (None, "", False) else secrets(v, _depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(secrets(v, _depth + 1) for v in value)
    return value


def pii(value, _depth=0):
    """Secrets removed and personal details masked: emails keep their first letter and domain (a***@acme.com),
    phone numbers are replaced. For metadata views where content must stay private."""
    value = secrets(value, _depth)
    if isinstance(value, str):
        return PHONE.sub("[phone]", EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value))
    if isinstance(value, dict):
        return {k: pii(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(pii(v, _depth + 1) for v in value)
    return value


def error_text(exc, limit=300):
    """Text of an exception (and the exceptions it was raised from) that is safe to store or show: secrets removed."""
    parts, seen, e = [], set(), exc
    while e is not None and id(e) not in seen and len(parts) < 4:
        seen.add(id(e))
        parts.append(f"{type(e).__name__}: {e}" if not parts else f"caused by {type(e).__name__}: {e}")
        e = e.__cause__ or e.__context__
    return secrets(" | ".join(parts))[:limit]


class RedactingStream:
    """Wraps sys.stdout/sys.stderr so every print() and traceback is scrubbed of secrets before it is written."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        try:
            text = _str_secrets(text) if isinstance(text, str) else text
        except Exception:
            pass
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_stream_redaction():
    import sys
    if not isinstance(sys.stdout, RedactingStream):
        sys.stdout = RedactingStream(sys.stdout)
    if not isinstance(sys.stderr, RedactingStream):
        sys.stderr = RedactingStream(sys.stderr)


class RedactingFilter:
    """logging.Filter that scrubs secrets from every record's message and arguments."""

    def filter(self, record):
        try:
            record.msg = secrets(str(record.msg)) if not isinstance(record.msg, str) else secrets(record.msg)
            if record.args:
                record.args = secrets(record.args) if isinstance(record.args, (tuple, dict)) else record.args
        except Exception:
            pass
        return True
