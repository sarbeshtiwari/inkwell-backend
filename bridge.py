"""Shared plumbing for the feature modules (GitHub, jobs, LinkedIn).

app.py calls setup() with its own module once everything is defined, so feature modules can use the
core helpers (Workspace, Mongo handles, encryption, validation) without importing app.py a second time
(which would happen under `python app.py`, where the core module is __main__, not app).
"""

import functools
import threading
import time

import requests

C = None                 # the core app module, set by setup()
QUEUE_HANDLERS = {}      # kind -> fn(ws, payload) -> (ok: bool, detail: str); used by the send queue


def setup(core):
    global C
    C = core


def login_required(fn):
    """Same as the core decorator, resolved at request time."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return C.login_required(fn)(*args, **kwargs)
    return wrapper


def http(method, url, *, token=None, timeout=30, **kw):
    """requests wrapper with a sensible timeout and a clear error for network failures."""
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("User-Agent", "Inkwell/1.0")
    try:
        return requests.request(method, url, headers=headers, timeout=timeout, **kw)
    except requests.RequestException as e:
        raise C.Invalid(f"Couldn't reach {url.split('/')[2]}: {e.__class__.__name__}. Check your internet connection.",
                        status=502)


def every(seconds, fn, name):
    """Run fn() about every `seconds` in a daemon thread, logging (not raising) errors.

    Coordinated across every process running the app: each process wakes every `seconds`, but a run happens only if
    it (1) claims this interval's slot in the shared schedule (exactly one process gets each slot, so the job runs
    once per interval in total, not once per process) and (2) takes the lease `loop:<name>`, so a run that is still
    going (longer than the interval, or in another process) is never overlapped. The lease is renewed while fn runs;
    if that process dies, the lease expires and the next slot runs anywhere."""
    from infra import locks, schedule
    from infra.redact import error_text
    slot = seconds * 0.9  # a little under the interval, so processes waking slightly early still find it due

    def loop():
        while True:
            time.sleep(seconds)
            try:
                lease = locks.try_acquire(f"loop:{name}")
                if lease is None:
                    continue  # still running (here or in another process): skip, never overlap
                try:
                    if schedule.claim(f"loop:{name}", slot):
                        fn()
                finally:
                    lease.release()
            except Exception as e:  # keep the loop alive
                print(f"[Inkwell] {name}: {error_text(e, 200)}", flush=True)
    threading.Thread(target=loop, daemon=True, name=name).start()


class Timeout(Exception):
    """A per-account job ran past its deadline (it keeps running in the background, but the loop moves on)."""


def with_deadline(seconds, fn, *args, **kwargs):
    """Run fn in its own daemon thread and wait at most `seconds`. One slow or stalling mail server then
    can't hold up the background work of every other account (loops call this per account)."""
    box = {}

    def run():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:  # handed back to the caller below
            box["error"] = e
    t = threading.Thread(target=run, daemon=True, name=f"deadline:{getattr(fn, '__name__', 'job')}")
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise Timeout(f"{getattr(fn, '__name__', 'job')} took longer than {seconds}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")

