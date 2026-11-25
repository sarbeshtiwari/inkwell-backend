"""Shared pieces: the blueprint, limits, in-memory run state and small helpers."""

import html
import os
import re
import threading
import time
import requests
from flask import Blueprint
import bridge


bp = Blueprint("ai_jobs", __name__)


OLLAMA_URL = os.environ.get("OLLAMA_URL", "" if os.environ.get("APP_ENV") == "production" else "http://127.0.0.1:11434").rstrip("/")


OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b")


APPLY_PER_DAY = 20


RUN_SECONDS = 8 * 60


MAX_RESULTS = 40


UA = {"User-Agent": "InkwellJobMatch/1.0 (+https://reachout-web.netlify.app)"}


RUNS, APPLYING = {}, {}


LIVE = {}                          # uid -> {"frame": jpeg bytes, "log": [...], "job": title, "at": t}


HANDOVER = {}                      # uid -> job id of the window left open on the user's Mac


APPLY_LOCK = threading.Lock()      # one browser at a time on this server


def C():
    return bridge.C


def J():
    from features import jobs
    return jobs


def plain(text):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(text)))
    return re.sub(r"\s+", " ", text).strip()


def get_json(url, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=15, **kw)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


class Live:
    """Shows what the browser is doing: a screenshot after each step, plus a short log (for the live view)."""

    def __init__(self, uid, job):
        self.uid = uid
        LIVE[uid] = {"frame": b"", "log": [], "job": f"{job['title']} · {job['company']}", "at": time.time(), "url": ""}

    def __call__(self, page, step=""):
        st = LIVE.get(self.uid)
        if st is None:
            return
        if step:
            st["log"] = (st["log"] + [step])[-14:]
        try:
            st["frame"] = page.screenshot(type="jpeg", quality=55, timeout=5000)
            st["url"] = page.url
        except Exception:
            pass
        st["at"] = time.time()
