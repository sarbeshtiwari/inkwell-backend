"""Which AI writes the prose parts (career-ops runs on whichever AI tool the user has; this is the same idea for a
web app): the local Ollama model by default, or a cloud model with the user's own key — Claude, OpenAI, Gemini,
OpenRouter, Groq, Mistral, DeepSeek — or any OpenAI-compatible server on this computer (LM Studio, llama.cpp...).

Keys are stored encrypted with the user's other keys and never sent back to the browser. With a cloud provider,
the resume and job text for the jobs you evaluate go to that provider under your own account."""

import json
import re
import threading
from contextlib import contextmanager

import requests
from flask import jsonify
from bridge import login_required

from features.ai_jobs import cli_llm
from features.ai_jobs.common import C, OLLAMA_MODEL, OLLAMA_URL, bp

PROVIDERS = {  # id: (label, OpenAI-compatible base URL, suggested model, key page)
    "claude_cli": ("Claude Code (your plan, on this computer)", "", "", "https://claude.com/claude-code"),
    "kiro_cli": ("Kiro CLI (your plan, on this computer)", "", "", "https://kiro.dev"),
    "ollama": ("Ollama (on this computer)", "", OLLAMA_MODEL, "https://ollama.com/download"),
    "anthropic": ("Claude (Anthropic)", "https://api.anthropic.com/v1", "claude-sonnet-5-5", "https://console.anthropic.com/settings/keys"),
    "openai": ("OpenAI", "https://api.openai.com/v1", "", "https://platform.openai.com/api-keys"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "", "https://aistudio.google.com/app/apikey"),
    "openrouter": ("OpenRouter (many models, some free)", "https://openrouter.ai/api/v1", "", "https://openrouter.ai/keys"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "", "https://console.groq.com/keys"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1", "", "https://console.mistral.ai/api-keys"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "", "https://platform.deepseek.com/api_keys"),
    "local": ("Other local server (OpenAI-compatible)", "", "", ""),
}


def ollama_up():
    if not OLLAMA_URL:
        return False
    try:
        tags = requests.get(OLLAMA_URL + "/api/tags", timeout=2).json().get("models", [])
        return any(m.get("name", "").split(":")[0] == OLLAMA_MODEL.split(":")[0] for m in tags)
    except Exception:
        return False


def local_allowed():
    """A server on this computer can only be used when Inkwell itself runs on the user's computer."""
    return not C().PRODUCTION


def cli_ready(pid):
    return local_allowed() and cli_llm.available(pid)


def default_provider():
    """Claude Code when Inkwell runs on a computer that has it, otherwise the local Ollama model."""
    return "claude_cli" if cli_ready("claude_cli") else "ollama"


def config(ws):
    """The user's model settings, with the key, or None when no model is usable right now."""
    s = ws.load("ai_model", {}) or {}
    pid = s.get("provider") or default_provider()
    if pid in cli_llm.TOOLS:
        if not cli_ready(pid):
            pid = "ollama"  # e.g. on the hosted service: fall back to the free local model
        else:
            model = s.get("model", "")
            return {"provider": pid, "model": model, "local": True, "label": f"{PROVIDERS[pid][0].split(' (')[0]}{' · ' + model if model else ''}"}
    if pid == "ollama" or pid not in PROVIDERS:
        return {"provider": "ollama", "model": OLLAMA_MODEL, "label": f"{OLLAMA_MODEL} (local)", "local": True} if ollama_up() else None
    key = (ws.load("ai_keys", {}) or {}).get("llm_" + pid, "")
    base = s.get("base_url", "") if pid == "local" else PROVIDERS[pid][1]
    model = s.get("model") or PROVIDERS[pid][2]
    if pid == "local" and not (local_allowed() and base):
        return None
    if (pid != "local" and not key) or not model:
        return None
    return {"provider": pid, "model": model, "key": key, "base": base.rstrip("/"), "local": pid == "local",
            "label": f"{model} ({'local' if pid == 'local' else PROVIDERS[pid][0].split(' (')[0]})"}


ACTIVE = threading.local()


@contextmanager
def use(cfg):
    """Make cfg the model for the code running in this thread (a match run, an evaluation)."""
    old = getattr(ACTIVE, "cfg", None)
    ACTIVE.cfg = cfg
    try:
        yield cfg
    finally:
        ACTIVE.cfg = old


def current():
    return getattr(ACTIVE, "cfg", None)


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


def chat(cfg, system, human, as_json=False, max_tokens=700, temperature=0.2, web=False, timeout=90):
    """One system + user turn on the configured model. Returns text, or a dict when as_json.
    web=True lets Claude Code / Kiro search the web (other providers ignore it)."""
    if cfg["provider"] in cli_llm.TOOLS:
        return cli_llm.ask(cfg["provider"], system, human, as_json, web, timeout, cfg.get("model", ""))
    if cfg["provider"] == "ollama":
        from langchain_ollama import ChatOllama
        from langchain_core.messages import HumanMessage, SystemMessage
        model = ChatOllama(base_url=OLLAMA_URL, model=cfg["model"], temperature=temperature, num_predict=max_tokens, reasoning=False,
                           keep_alive="10m", **({"format": "json"} if as_json else {}))
        text = model.invoke([SystemMessage(system), HumanMessage(human)]).content
    elif cfg["provider"] == "anthropic":
        r = requests.post(cfg["base"] + "/messages", timeout=90, json={
            "model": cfg["model"], "max_tokens": max_tokens, "temperature": temperature, "system": system,
            "messages": [{"role": "user", "content": human}]},
            headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
        check(r)
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    else:
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {cfg['key']}"} if cfg.get("key") else {})}
        if cfg["provider"] == "openrouter":
            headers.update({"HTTP-Referer": "https://reachout-web.netlify.app", "X-Title": "Inkwell"})
        r = requests.post(cfg["base"] + "/chat/completions", timeout=90, headers=headers, json={
            "model": cfg["model"], "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": human}]})
        check(r)
        text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return first_json(text) if as_json else text


def check(r):
    if r.status_code in (401, 403):
        raise RuntimeError("The provider didn't accept your API key.")
    if r.status_code == 404:
        raise RuntimeError("The provider doesn't know that model name.")
    if r.status_code == 429:
        raise RuntimeError("The provider says you're over your rate limit or out of credit.")
    if not r.ok:
        raise RuntimeError(f"The provider answered HTTP {r.status_code}.")


def public(ws):
    s = ws.load("ai_model", {}) or {}
    keys = ws.load("ai_keys", {}) or {}
    cfg = config(ws)
    return {"provider": s.get("provider") or default_provider(), "model": s.get("model", ""), "base_url": s.get("base_url", ""),
            "ready": bool(cfg), "label": cfg["label"] if cfg else "", "ollama_up": ollama_up(), "local_allowed": local_allowed(),
            "keys": {p: bool(keys.get("llm_" + p)) for p in PROVIDERS if p not in ("ollama", "local", *cli_llm.TOOLS)},
            "cli": {p: cli_ready(p) for p in cli_llm.TOOLS},
            "providers": [{"id": k, "label": v[0], "suggested": v[2], "key_url": v[3], "keyless": k in ("ollama", "local", *cli_llm.TOOLS)}
                          for k, v in PROVIDERS.items() if (k != "local" or local_allowed()) and (k not in cli_llm.TOOLS or cli_ready(k))]}


@bp.get("/api/ai-jobs/model")
@login_required
def get_model(ws):
    return jsonify(public(ws))


@bp.put("/api/ai-jobs/model")
@login_required
def put_model(ws):
    core = C()
    p = core.body()
    pid = str(p.get("provider") or "ollama")
    if pid not in PROVIDERS or (pid == "local" and not local_allowed()) or (pid in cli_llm.TOOLS and not cli_ready(pid)):
        raise core.Invalid("Pick one of the listed providers.", "provider")
    model = core.v_text(p.get("model"), "model", "Model", 120)
    if model and not re.fullmatch(r"[\w.:/@+-]{1,120}", model):
        raise core.Invalid("That doesn't look like a model name.", "model")
    base = core.v_text(p.get("base_url"), "base_url", "Server address", 200) if pid == "local" else ""
    if base and not re.fullmatch(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d{2,5})?(/[\w./-]*)?", base):
        raise core.Invalid("Use a server on this computer, like http://localhost:1234/v1.", "base_url")
    with ws.lock:
        if "key" in p and pid not in ("ollama", "local", *cli_llm.TOOLS):
            key = str(p.get("key") or "").strip()
            if key and not re.fullmatch(r"[\w\-.:]{10,300}", key):
                raise core.Invalid("That doesn't look like an API key.", "key")
            keys = ws.load("ai_keys", {}) or {}
            keys["llm_" + pid] = key
            ws.save("ai_keys", keys)
        ws.save("ai_model", {"provider": pid, "model": model, "base_url": base})
    return jsonify(public(ws))


@bp.post("/api/ai-jobs/model/test")
@login_required
def test_model(ws):
    core = C()
    if core.rate_limited(("ai-model-test", ws.uid), 30, 3600):
        raise core.Invalid("Too many tests. Try again in a while.", status=429)
    cfg = config(ws)
    if not cfg:
        raise core.Invalid("Finish setting up the model first (provider, key and model name).", "model")
    try:
        out = chat(cfg, "Answer only JSON.", 'Reply with {"ok": true, "model": "<your model name>"}.', as_json=True, max_tokens=60)
    except Exception as e:
        raise core.Invalid(f"It didn't work: {e}", "model")
    return jsonify(ok=bool(out.get("ok")), label=cfg["label"])


@bp.get("/api/ai-jobs/model/list")
@login_required
def list_models(ws):
    """Model names the provider offers (OpenAI-compatible /models), to pick from."""
    s = ws.load("ai_model", {}) or {}
    pid = s.get("provider") or default_provider()
    key = (ws.load("ai_keys", {}) or {}).get("llm_" + pid, "")
    if pid == "claude_cli":
        return jsonify(models=["sonnet", "opus", "haiku"])
    if pid == "kiro_cli":
        return jsonify(models=[])
    if pid == "ollama":
        try:
            names = [m["name"] for m in requests.get(OLLAMA_URL + "/api/tags", timeout=3).json().get("models", [])]
        except Exception:
            names = []
        return jsonify(models=names)
    base = s.get("base_url", "") if pid == "local" else PROVIDERS.get(pid, ("", ""))[1]
    if not base or (pid != "local" and not key) or (pid == "local" and not local_allowed()):
        return jsonify(models=[])
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"} if pid == "anthropic" else {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = requests.get(base.rstrip("/") + "/models", headers=headers, timeout=15)
        data = r.json().get("data", []) if r.ok else []
    except Exception:
        data = []
    return jsonify(models=sorted({str(m.get("id", "")).removeprefix("models/") for m in data if m.get("id")})[:300])
