"""The AI model every feature uses: the one the Super Admin sets for the whole platform (infra/model_config.py).
Keys come only from the server's environment; users don't choose or bring a model. With none set, the features
that need a model say so and the ones with an engine of their own keep working without it."""

import threading
from contextlib import contextmanager

from infra import gateway


def config(ws):
    """The platform model for this user's calls, or None when the Super Admin hasn't set one (or its key is missing)."""
    from infra import model_config
    return model_config.for_agents(ws.uid)


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


first_json = gateway.first_json  # kept here: research imports it from this module


def chat(cfg, system, human, as_json=False, max_tokens=700, temperature=0.2, web=False, timeout=90):
    """One system + user turn on the configured model. Returns text, or a dict when as_json. `web` is accepted for
    callers that ask for it; the platform providers don't search the web.

    Goes through the model gateway (infra/gateway.py), which records the call's usage; errors are RuntimeErrors
    (gateway.GatewayError)."""
    if cfg.get("config_version"):  # the platform model: record which version of the setting answered
        with gateway.context(model_config_version=cfg["config_version"]):
            return _complete(cfg, system, human, as_json, max_tokens, temperature, web, timeout)
    return _complete(cfg, system, human, as_json, max_tokens, temperature, web, timeout)


def _complete(cfg, system, human, as_json, max_tokens, temperature, web, timeout):
    return gateway.complete(gateway.Request(
        provider=cfg["provider"], model=cfg.get("model", ""), system=system, human=human, uid=cfg.get("uid"),
        key=cfg.get("key", ""), base=cfg.get("base", ""), as_json=as_json, max_tokens=max_tokens,
        temperature=temperature, web=web, timeout=timeout, billing=cfg.get("billing") or (gateway.LOCAL if cfg.get("local") else gateway.BYOK)))


def _claude_code(req):
    """Claude Code on this computer as a gateway adapter (one JSON re-ask inside cli_llm.ask). No token counts."""
    import json
    from features.ai_jobs import cli_llm
    out = cli_llm.ask(req.provider, req.system, req.human, req.as_json, req.web, req.timeout, req.model)
    return gateway.Reply(json.dumps(out) if req.as_json else out)


gateway.register_adapter("claude_cli", _claude_code)


def check(r):
    gateway.check_response(r)
