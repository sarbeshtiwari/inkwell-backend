"""The service-wide model, chosen by the Super Admin (product decision D-05, 2026-11-27).

One document in `model_configs` (`_id` "global"): provider, model, a version number that goes up on every change,
who changed it and when. Changing it needs `admin.manage_models` and is written to the audit log first.

- **Keys** for the platform providers come only from the server's environment (PLATFORM_ANTHROPIC_KEY,
  PLATFORM_OPENAI_KEY): never stored in MongoDB, never returned by any API, never given to agents. A provider whose
  key isn't set can't be chosen.
- **Use.** for_agents(uid) returns the gateway config for every model call (agents and features alike, through
  features.ai_jobs.models.config): the platform model, billed "platform" so the Super Admin's budgets apply, when one
  is set and its key exists; otherwise None, and nothing uses a model.
- Every agent call on the platform model records the config version in its usage record."""

import os
import re
import time

from core.db import M
from core.validation import Invalid
from infra import audit, rbac

PLATFORM = {  # provider: (label, base URL, environment variable holding the key)
    "anthropic": ("Claude (Anthropic)", "https://api.anthropic.com/v1", "PLATFORM_ANTHROPIC_KEY"),
    "openai": ("OpenAI", "https://api.openai.com/v1", "PLATFORM_OPENAI_KEY"),
}
LOCAL = {  # provider: (label, model names offered) — runs on this computer, no key; never in production
    "claude_cli": ("Claude Code (this computer, your plan)", ("sonnet", "opus", "haiku")),
}
MODEL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


def _key(provider):
    return os.environ.get(PLATFORM[provider][2], "").strip() if provider in PLATFORM else ""


def _ready(provider):
    """The provider can answer: a platform key on the server, or Claude Code installed on this computer."""
    if provider in LOCAL:
        from features.ai_jobs import cli_llm
        return cli_llm.available(provider)
    return provider in PLATFORM and bool(_key(provider))


def current():
    """The setting as stored (no keys), or None when the Super Admin hasn't chosen one."""
    d = M.model_configs.find_one({"_id": "global"})
    if not d or not d.get("provider"):
        return None
    return {"provider": d["provider"], "model": d["model"], "version": d.get("version", 1), "by": d.get("by"),
            "at": d.get("at"), "key_configured": _ready(d["provider"])}


def status():
    """For the admin view: the setting and which providers have a key configured on the server (never the keys)."""
    return {"config": current(), "providers": [{"id": p, "label": v[0], "key_configured": bool(_key(p))}
                                               for p, v in PLATFORM.items()]
            + [{"id": p, "label": v[0], "key_configured": _ready(p), "local": True, "models": list(v[1])}
               for p, v in LOCAL.items() if _ready(p)]}


def set_model(actor_uid, provider, model, reason=""):
    """Choose the platform model (or provider None for no model at all). Audited before it applies."""
    rbac.check(actor_uid, "admin.manage_models", target="model_config:global")
    reason = str(reason or "").strip()[:300]
    if len(reason) < 5:
        raise Invalid("Give a short reason for the change.", "reason")
    if provider is None:
        audit.record("model_config.clear", actor_uid, target="model_config:global", details={"reason": reason})
        M.model_configs.update_one({"_id": "global"}, {"$set": {"provider": None, "model": None, "by": actor_uid,
                                                               "at": time.time()}, "$inc": {"version": 1}}, upsert=True)
        return current()
    if provider not in PLATFORM and provider not in LOCAL:
        raise Invalid("Choose anthropic, openai or claude_cli.", "provider")
    if not isinstance(model, str) or not MODEL_OK.match(model):
        raise Invalid("Give the provider's model name.", "model")
    if provider in LOCAL and not _ready(provider):
        raise Invalid("Claude Code isn't installed on this computer (or this is the hosted service), so it can't be used.",
                      "provider", status=409)
    if provider in PLATFORM and not _key(provider):
        raise Invalid(f"The server has no {PLATFORM[provider][2]} set, so {PLATFORM[provider][0]} can't be used yet.",
                      "provider", status=409)
    audit.record("model_config.set", actor_uid, target="model_config:global",
                 details={"provider": provider, "model": model, "reason": reason})
    M.model_configs.update_one({"_id": "global"}, {"$set": {"provider": provider, "model": model, "by": actor_uid,
                                                           "at": time.time()}, "$inc": {"version": 1}}, upsert=True)
    return current()


def for_agents(uid):
    """Gateway config for a model call for `uid` on the platform model, or None when no model is set."""
    c = current()
    if not c or not c["key_configured"]:
        return None
    if c["provider"] in LOCAL:
        return {"provider": c["provider"], "model": c["model"], "key": "", "base": "", "uid": uid, "local": True,
                "billing": "platform", "config_version": str(c["version"]),
                "label": f"Claude Code · {c['model']} (this computer)"}
    label, base, _ = PLATFORM[c["provider"]]
    return {"provider": c["provider"], "model": c["model"], "key": _key(c["provider"]), "base": base, "uid": uid,
            "local": False, "billing": "platform", "config_version": str(c["version"]),
            "label": f"{c['model']} ({label}, Inkwell)"}
