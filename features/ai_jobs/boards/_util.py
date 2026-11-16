"""Shared helpers for board readers: polite HTTP with short timeouts, HTML/RSS to text, dates."""

import html
import re
import xml.etree.ElementTree as ET

import requests

from features.ai_jobs.common import UA, plain
from features.ai_jobs.providers import iso_day  # noqa: F401  (re-exported for board modules)

TIMEOUT = 10


def get_json(url, method="GET", **kw):
    """JSON from a public endpoint, or None (never raises)."""
    try:
        r = requests.request(method, url, headers={**UA, "Accept": "application/json", **kw.pop("headers", {})}, timeout=TIMEOUT, **kw)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def get_text(url, **kw):
    """Body text of a public page or feed, or "" (never raises)."""
    try:
        r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=TIMEOUT, **kw)
        return r.text if r.ok else ""
    except requests.RequestException:
        return ""


def rss_items(xml_text):
    """[{title, link, description, pubDate, ...}] from an RSS or Atom feed ([] when it doesn't parse)."""
    try:
        root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter():
        tag = it.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        row = {}
        for ch in it:
            k = ch.tag.rsplit("}", 1)[-1]
            row[k] = (ch.attrib.get("href") if k == "link" and ch.attrib.get("href") else (ch.text or "")).strip()
        out.append(row)
    return out


def words(query):
    return [w for w in re.findall(r"[a-z0-9+#]+", (query or "").lower()) if len(w) > 2]


def title_matches(title, query):
    """True when the title mentions any word of the role (boards that can't search server-side)."""
    ws = words(query)
    t = (title or "").lower()
    return not ws or any(w in t for w in ws)


def unescape(text):
    return html.unescape(text or "").strip()


__all__ = ["TIMEOUT", "get_json", "get_text", "rss_items", "words", "title_matches", "unescape", "plain", "iso_day"]
