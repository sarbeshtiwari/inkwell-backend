"""Model gateway: the one place that talks to model providers, and records every call.

`complete(request)` picks the provider adapter, makes the call and writes one `model_usage` record: who the call was
for, what asked for it, the requested model, the model id the provider actually answered with, input/output tokens,
cost (when a price is on record), latency, status and error class. Prompts, answers, keys and base URLs are never
written to it.

- **Adapters.** Anthropic, OpenAI-compatible (OpenAI, Gemini, OpenRouter, Groq, Mistral, DeepSeek, local servers) and
  Ollama live here. Others (the local Claude Code / Kiro CLIs, a fake provider in tests) are added with
  register_adapter(); an adapter takes a Request and returns a Reply.
- **Billing.** "byok" for a cloud provider with the user's own key, "local" for Ollama, CLIs and local servers. Platform
  keys (and the budgets that must come with them) aren't wired in yet, so no call is billed to the platform.
- **Prices.** `model_prices` holds per-million-token prices with the date they apply from; the record keeps the price
  version used. No price on record → cost is None (never guessed).
- **Context.** Agents and runs add correlation fields (trace, task, agent, versions) with `with context(...)`.
- **Recording never blocks the answer** for byok/local calls: if MongoDB can't store the record, a redacted warning is
  logged and the result is still returned.

Errors keep the messages users already see (bad key, unknown model, rate limit…) and carry an `error_class`."""

import datetime as dt
import json
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import requests
from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from core.db import M
from infra.redact import error_text

log = logging.getLogger("inkwell.gateway")

HTTP_TIMEOUT = 90  # seconds; what every HTTP provider call has always used
BYOK, LOCAL = "byok", "local"
CONTEXT_FIELDS = ("purpose", "trace_id", "span_id", "task_id", "run_id", "agent_id", "agent_version", "model_config_version")


class GatewayError(RuntimeError):
    """A failed model call. str(e) is the message shown to the user; error_class says what kind of failure it was."""

    def __init__(self, message, error_class="error"):
        super().__init__(message)
        self.error_class = error_class


@dataclass
class Request:
    provider: str
    model: str
    system: str
    human: str
    uid: str | None = None
    key: str = field(default="", repr=False)
    base: str = ""
    as_json: bool = False
    max_tokens: int = 700
    temperature: float = 0.2
    web: bool = False
    timeout: float = 90
    purpose: str = ""
    billing: str = LOCAL


@dataclass
class Reply:
    text: str
    model_id: str | None = None        # as the provider reported it; None when it doesn't say (CLIs)
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


ADAPTERS = {}


def register_adapter(provider, fn):
    ADAPTERS[provider] = fn


_CTX = threading.local()


@contextmanager
def context(**fields):
    """Correlation fields (trace_id, task_id, agent_id, versions, purpose) added to every call made inside the block."""
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown gateway context field(s): {', '.join(sorted(unknown))}")
    old = getattr(_CTX, "fields", {})
    _CTX.fields = {**old, **{k: v for k, v in fields.items() if v is not None}}
    try:
        yield
    finally:
        _CTX.fields = old


def current_context():
    return dict(getattr(_CTX, "fields", {}))


def ensure_indexes():
    M.model_usage.create_index([("uid", 1), ("day", 1)])
    M.model_usage.create_index([("day", 1), ("model_id", 1)])
    M.model_usage.create_index("trace_id", sparse=True)
    M.model_prices.create_index([("provider", 1), ("model", 1), ("effective_from", DESCENDING)])


# ---- the call ----------------------------------------------------------------------------------------------------

def complete(req):
    """Make one model call and record it. Returns text, or a dict when req.as_json. Raises GatewayError."""
    adapter = ADAPTERS.get(req.provider) or (_anthropic if req.provider == "anthropic" else
                                            _ollama if req.provider == "ollama" else _openai_compatible)
    started = time.monotonic()
    reply, out, error = None, None, None
    try:
        reply = adapter(req)
        text = re.sub(r"<think>.*?</think>", "", reply.text or "", flags=re.S).strip()
        out = first_json(text) if req.as_json else text
    except GatewayError as e:
        error = e
    except ValueError as e:  # the answer wasn't the JSON asked for
        error = GatewayError(str(e), "invalid_output")
    except requests.Timeout:
        error = GatewayError("The AI didn't answer in time.", "timeout")
    except requests.RequestException as e:
        error = GatewayError(f"Couldn't reach the model provider ({type(e).__name__}).", "network")
    except Exception as e:  # adapters registered from outside (CLIs) raise their own errors; keep their message
        error = GatewayError(str(e), getattr(e, "error_class", None) or _class_of(e))
    _record(req, reply, error, round((time.monotonic() - started) * 1000))
    if error:
        raise error
    return out


def _class_of(e):
    name = type(e).__name__.lower()
    return "quota" if "quota" in name else "timeout" if "timeout" in name or "didn't answer within" in str(e) else "error"


def first_json(text):
    """The first JSON object in a model's answer (models sometimes wrap it in prose or ```json fences)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError:
                    break
        start = text.find("{", start + 1)
    raise ValueError("The model didn't answer with JSON.")


def check_response(r):
    """Turn a provider's HTTP error into the message users see, with its error class."""
    if r.status_code in (401, 403):
        raise GatewayError("The provider didn't accept your API key.", "auth")
    if r.status_code == 404:
        raise GatewayError("The provider doesn't know that model name.", "not_found")
    if r.status_code == 429:
        raise GatewayError("The provider says you're over your rate limit or out of credit.", "rate_limit")
    if not r.ok:
        raise GatewayError(f"The provider answered HTTP {r.status_code}.", "server" if r.status_code >= 500 else "http_error")


# ---- adapters ----------------------------------------------------------------------------------------------------

def _anthropic(req):
    r = requests.post(req.base + "/messages", timeout=HTTP_TIMEOUT, json={
        "model": req.model, "max_tokens": req.max_tokens, "temperature": req.temperature, "system": req.system,
        "messages": [{"role": "user", "content": req.human}]},
        headers={"x-api-key": req.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    check_response(r)
    data = r.json()
    usage = data.get("usage") or {}
    return Reply("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"),
                 model_id=data.get("model"), input_tokens=usage.get("input_tokens"),
                 output_tokens=usage.get("output_tokens"), request_id=data.get("id"))


def _openai_compatible(req):
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {req.key}"} if req.key else {})}
    if req.provider == "openrouter":
        headers.update({"HTTP-Referer": "https://reachout-web.netlify.app", "X-Title": "Inkwell"})
    r = requests.post(req.base + "/chat/completions", timeout=HTTP_TIMEOUT, headers=headers, json={
        "model": req.model, "max_tokens": req.max_tokens, "temperature": req.temperature,
        "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.human}]})
    check_response(r)
    data = r.json()
    usage = data.get("usage") or {}
    return Reply(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "",
                 model_id=data.get("model"), input_tokens=usage.get("prompt_tokens"),
                 output_tokens=usage.get("completion_tokens"), request_id=data.get("id"))


def _ollama(req):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama
    model = ChatOllama(base_url=req.base, model=req.model, temperature=req.temperature, num_predict=req.max_tokens,
                       reasoning=False, keep_alive="10m", **({"format": "json"} if req.as_json else {}))
    msg = model.invoke([SystemMessage(req.system), HumanMessage(req.human)])
    usage = getattr(msg, "usage_metadata", None) or {}
    meta = getattr(msg, "response_metadata", None) or {}
    return Reply(msg.content, model_id=meta.get("model") or meta.get("model_name"),
                 input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))


class FakeProvider:
    """A scripted provider for tests: answers in order (a string, a Reply, or an exception to raise)."""

    def __init__(self, *answers, model_id="fake-model-1"):
        self.answers, self.model_id, self.calls = list(answers), model_id, []

    def __call__(self, req):
        self.calls.append(req)
        a = self.answers.pop(0) if self.answers else ""
        if isinstance(a, BaseException):
            raise a
        if isinstance(a, Reply):
            return a
        return Reply(a, model_id=self.model_id, input_tokens=len(req.system + req.human) // 4, output_tokens=len(a) // 4,
                     request_id=f"fake-{len(self.calls)}")


# ---- usage records -----------------------------------------------------------------------------------------------

def price(provider, model, at=None):
    """The price on record for this model at time `at` (epoch seconds), or None."""
    at = time.time() if at is None else at
    return M.model_prices.find_one({"provider": provider, "model": model, "effective_from": {"$lte": at}},
                                   sort=[("effective_from", DESCENDING)])


def _cost(p, tokens_in, tokens_out):
    if not p or tokens_in is None or tokens_out is None:
        return None
    return round((tokens_in * p["input_per_mtok"] + tokens_out * p["output_per_mtok"]) / 1_000_000, 8)


def _record(req, reply, error, latency_ms):
    now = time.time()
    ctx = current_context()
    tokens_in = reply.input_tokens if reply else None
    tokens_out = reply.output_tokens if reply else None
    returned = (reply.model_id if reply else None)
    doc = {"_id": uuid.uuid4().hex, "uid": req.uid, "at": now,
           "day": dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d"),
           "purpose": ctx.pop("purpose", None) or req.purpose or "", "provider": req.provider,
           "requested_model": req.model, "model_id": returned, "input_tokens": tokens_in, "output_tokens": tokens_out,
           "latency_ms": latency_ms, "status": "error" if error else "ok",
           "error_class": error.error_class if error else None, "billing": req.billing,
           "request_id": reply.request_id if reply else None, **ctx}
    try:
        p = price(req.provider, returned or req.model, now) if tokens_in is not None else None
        doc.update(cost=_cost(p, tokens_in, tokens_out), currency=p.get("currency") if p else None,
                   price_version=p.get("version") if p else None)
        M.model_usage.insert_one(doc)
    except PyMongoError as e:
        log.warning("model usage record not stored (%s): %s", req.provider, error_text(e, 200))


def usage(uid, since_day=None, until_day=None):
    """One user's usage records (newest first), optionally between two UTC days (YYYY-MM-DD, inclusive)."""
    q = {"uid": uid}
    if since_day or until_day:
        q["day"] = {**({"$gte": since_day} if since_day else {}), **({"$lte": until_day} if until_day else {})}
    return list(M.model_usage.find(q, {"_id": 0}).sort("at", DESCENDING))


def totals(uid, since_day=None, until_day=None):
    """Calls, tokens and cost for one user (cost counts only calls with a price on record)."""
    match = {"uid": uid}
    if since_day or until_day:
        match["day"] = {**({"$gte": since_day} if since_day else {}), **({"$lte": until_day} if until_day else {})}
    rows = list(M.model_usage.aggregate([{"$match": match}, {"$group": {
        "_id": None, "calls": {"$sum": 1}, "errors": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
        "input_tokens": {"$sum": {"$ifNull": ["$input_tokens", 0]}},
        "output_tokens": {"$sum": {"$ifNull": ["$output_tokens", 0]}},
        "cost": {"$sum": {"$ifNull": ["$cost", 0]}}}}]))
    out = rows[0] if rows else {"calls": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0}
    out.pop("_id", None)
    return out
