"""Usage budgets, checked by the model gateway before every call.

Two kinds of limit:

- **Scoped budgets** (`budgets` collection, set by a holder of `admin.manage_budgets`, audited first): one document per
  scope — "global" (the whole service together), "user_default" (every user, each on their own spend; set once by
  the Super Admin, product decision D-05), "user:<uid>" or "agent:<agent_id>" — with any of `daily_tokens`, `monthly_tokens`, `daily_cost`,
  `monthly_cost`, `daily_calls`, `monthly_calls`, plus `applies_to`: which billing kinds count ("platform" by
  default; "byok" and "local" can be added). Periods are UTC days and calendar months. Spend comes from
  `model_usage`, so it is the same numbers users and admins see.
- **Per-task limits** (runaway protection): a task may carry `budget={"max_tokens": …, "max_calls": …,
  "max_cost": …}`; every model call made while that task (or any task in its tree) runs counts against it, whatever
  pays for the call.

There are **no default amounts**: with no budget documents and no task limits, nothing is refused. Choosing the
amounts is a product decision (D-05). A refused call raises BudgetExceeded (error class "budget"), is recorded in
`model_usage` like any failed call, and never reaches the provider.

The check reads current spend and compares; two calls racing at the edge can both pass (the overshoot is at most the
calls in flight). Exact accounting under concurrency would need a reservation step; not needed yet."""

import datetime as dt
import re
import time

from core.db import M
from core.validation import Invalid
from infra import audit, rbac

LIMIT_FIELDS = ("daily_tokens", "monthly_tokens", "daily_cost", "monthly_cost", "daily_calls", "monthly_calls")
BILLING_KINDS = ("platform", "byok", "local")
TASK_FIELDS = ("max_tokens", "max_calls", "max_cost")
AGENT_OK = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
UID_OK = re.compile(r"^[\w-]{1,64}$")


class BudgetExceeded(RuntimeError):
    error_class = "budget"


def scope_id(agent_id=None, uid=None, per_user=False):
    if per_user:
        if agent_id or uid:
            raise ValueError("A per-user default applies to every user: don't name one.")
        return "user_default"
    if agent_id and uid:
        raise ValueError("A budget covers everything, one agent, or one user: not both.")
    if agent_id:
        if not AGENT_OK.match(agent_id):
            raise Invalid("Unknown agent.", "agent_id")
        return f"agent:{agent_id}"
    if uid:
        if not UID_OK.match(uid):
            raise Invalid("Unknown user.", "uid")
        return f"user:{uid}"
    return "global"


def _clean_limits(limits):
    out = {}
    for k, v in (limits or {}).items():
        if k not in LIMIT_FIELDS:
            raise Invalid(f"Unknown limit: {k}.", k)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise Invalid("Limits are numbers.", k)
        if v < 0:
            raise Invalid("Limits can't be negative.", k)
        out[k] = v
    return out


def set_budget(actor_uid, *, agent_id=None, uid=None, per_user=False, limits=None, applies_to=("platform",), reason=""):
    """Replace a scope's limits (needs admin.manage_budgets; audited before it takes effect)."""
    sid = scope_id(agent_id, uid, per_user)
    rbac.check(actor_uid, "admin.manage_budgets", target=f"budget:{sid}")
    lim = _clean_limits(limits)
    kinds = sorted(set(applies_to or ()))
    if not kinds or any(k not in BILLING_KINDS for k in kinds):
        raise Invalid("applies_to lists billing kinds: platform, byok, local.", "applies_to")
    audit.record("budget.set", actor_uid, target=sid, details={"limits": lim, "applies_to": kinds, "reason": str(reason)[:300]})
    M.budgets.update_one({"_id": sid}, {"$set": {"limits": lim, "applies_to": kinds, "by": actor_uid, "at": time.time()}},
                         upsert=True)
    return {"scope": sid, "limits": lim, "applies_to": kinds}


def listing():
    return [{"scope": d["_id"], "limits": d.get("limits", {}), "applies_to": d.get("applies_to", ["platform"]),
             "by": d.get("by"), "at": d.get("at")} for d in M.budgets.find({}).sort("_id", 1)]


def _periods(now):
    t = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return t.strftime("%Y-%m-%d"), t.strftime("%Y-%m") + "-01", t.strftime("%Y-%m") + "-31"


def _spend(match):
    rows = list(M.model_usage.aggregate([{"$match": match}, {"$group": {
        "_id": None, "calls": {"$sum": 1},
        "tokens": {"$sum": {"$add": [{"$ifNull": ["$input_tokens", 0]}, {"$ifNull": ["$output_tokens", 0]}]}},
        "cost": {"$sum": {"$ifNull": ["$cost", 0]}}}}]))
    return rows[0] if rows else {"calls": 0, "tokens": 0, "cost": 0}


def _check_scope(doc, base_match, billing, now, label):
    if billing not in doc.get("applies_to", ["platform"]):
        return
    lim = doc.get("limits") or {}
    if not lim:
        return
    day, month_start, month_end = _periods(now)
    match = {**base_match, "billing": {"$in": doc.get("applies_to", ["platform"])}, "status": {"$ne": "refused"}}
    for period, rng in (("daily", {"day": day}), ("monthly", {"day": {"$gte": month_start, "$lte": month_end}})):
        if not any(k.startswith(period) for k in lim):
            continue
        used = _spend({**match, **rng})
        for unit in ("tokens", "cost", "calls"):
            cap = lim.get(f"{period}_{unit}")
            if cap is not None and used[unit] >= cap:
                raise BudgetExceeded(f"The {period} {unit.rstrip('s') if unit == 'calls' else unit} limit for {label} "
                                     f"has been reached. Try again {'tomorrow' if period == 'daily' else 'next month'}.")


def check(uid, billing, agent_id=None, task_budget=None, root_task_id=None, now=None):
    """Raise BudgetExceeded when the next call would go over any budget that applies. No budgets → no-op."""
    now = time.time() if now is None else now
    scopes = {"global": {}, **({f"user:{uid}": {"uid": uid}, "user_default": {"uid": uid}} if uid else {}),
              **({f"agent:{agent_id}": {"agent_id": agent_id}} if agent_id else {})}
    labels = {"global": "the whole service", f"user:{uid}": "your account", "user_default": "your account", f"agent:{agent_id}": f"the {agent_id} agent"}
    for doc in M.budgets.find({"_id": {"$in": list(scopes)}}):
        _check_scope(doc, scopes[doc["_id"]], billing, now, labels.get(doc["_id"], doc["_id"]))
    if task_budget and root_task_id:
        used = _spend({"root_task_id": root_task_id, "status": {"$ne": "refused"}})
        for unit, field in (("tokens", "max_tokens"), ("calls", "max_calls"), ("cost", "max_cost")):
            cap = task_budget.get(field)
            if cap is not None and used[unit] >= cap:
                raise BudgetExceeded(f"This task has used its {unit} budget, so it was stopped.")


def clean_task_budget(budget):
    if budget is None:
        return None
    if not isinstance(budget, dict) or set(budget) - set(TASK_FIELDS):
        raise ValueError(f"A task budget may set only {', '.join(TASK_FIELDS)}.")
    out = {}
    for k, v in budget.items():
        if v is None:
            continue
        if not isinstance(v, (int, float)) or v < 0:
            raise ValueError("Task budget limits are non-negative numbers.")
        out[k] = v
    return out or None
