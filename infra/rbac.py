"""Roles and permissions, enforced on the server.

Roles live in the `roles` collection (one document per uid that has more than the default role). The first Super
Admin is bootstrapped from ADMIN_EMAILS (your configured admin email). Without ADMIN_EMAILS, on a computer that
isn't a production server, the oldest account keeps owner rights as before, so existing local setups keep working.

`@require_permission(...)` wraps a route that already has @login_required: it checks the permission, writes an
audit event for denials (and for allowed calls when `audit=True`), and returns 403 otherwise."""

import functools
import time

from core.accounts import find_user
from core.config import ADMIN_EMAILS, PRODUCTION
from core.crypto import lookup_hash
from core.db import M
from core.validation import Invalid
from infra import audit

USER, SUPER_ADMIN = "user", "super_admin"
ROLES = (USER, SUPER_ADMIN)

PERMISSIONS = {
    "admin.read_metadata": "See platform metadata: agent and task status, timings, models, token use and costs, errors",
    "admin.site_leads": "Read and manage enquiries sent to Inkwell's own website",
    "admin.integrations": "Configure platform-wide integrations (e.g. the LinkedIn app)",
    "admin.update_app": "Update the app from its git folders (own computer only)",
    "admin.control_agents": "Start, stop, pause and kill-switch agents",
    "admin.manage_models": "Register, approve, promote and roll back models and model configurations",
    "admin.manage_budgets": "Set usage budgets and limits",
    "admin.manage_roles": "Grant and revoke roles",
    "admin.reveal_content": "See a user's actual content (exceptional, audited; disabled until the privacy policy allows it)",
}
ROLE_PERMISSIONS = {USER: frozenset(), SUPER_ADMIN: frozenset(PERMISSIONS) - {"admin.reveal_content"}}
CONTENT_REVEAL_ENABLED = False  # privacy promise: stays off until the privacy policy is updated and this is changed


def ensure_indexes():
    M.roles.create_index("role")


def bootstrap():
    """Give every ADMIN_EMAILS account the Super Admin role (idempotent; logged once per grant)."""
    for email in sorted(ADMIN_EMAILS):
        row = M.users.find_one({"email_hash": lookup_hash(email)}, {"_id": 1})
        if not row:
            continue
        res = M.roles.update_one({"_id": row["_id"], "role": {"$ne": SUPER_ADMIN}},
                                 {"$set": {"role": SUPER_ADMIN, "granted_by": "bootstrap:ADMIN_EMAILS", "at": time.time()}},
                                 upsert=False)
        if res.matched_count == 0 and not M.roles.find_one({"_id": row["_id"]}):
            M.roles.insert_one({"_id": row["_id"], "role": SUPER_ADMIN, "granted_by": "bootstrap:ADMIN_EMAILS", "at": time.time()})
            audit.record("role.grant", "system", target=f"user:{row['_id']}", details={"role": SUPER_ADMIN, "via": "ADMIN_EMAILS"})
        elif res.modified_count:
            audit.record("role.grant", "system", target=f"user:{row['_id']}", details={"role": SUPER_ADMIN, "via": "ADMIN_EMAILS"})


def role_of(uid):
    if not uid:
        return USER
    row = M.roles.find_one({"_id": uid}, {"role": 1})
    if row and row.get("role") in ROLES:
        return row["role"]
    user = find_user(uid=uid)
    if not user:
        return USER
    if ADMIN_EMAILS:
        return SUPER_ADMIN if user["email"] in ADMIN_EMAILS else USER
    if PRODUCTION:
        return USER
    first = M.users.find_one({}, {"_id": 1}, sort=[("created", 1)])  # local setup without ADMIN_EMAILS: the owner
    return SUPER_ADMIN if first and first["_id"] == uid else USER


def permissions_of(uid):
    return ROLE_PERMISSIONS[role_of(uid)]


def has(uid, permission):
    if permission not in PERMISSIONS:
        raise ValueError(f"Unknown permission: {permission}")
    if permission == "admin.reveal_content" and not CONTENT_REVEAL_ENABLED:
        return False
    return permission in permissions_of(uid)


def check(uid, permission, target="", message="You don't have permission to do that."):
    """Raise 403 (and audit the denial) unless uid has the permission."""
    if not has(uid, permission):
        audit.record("access.denied", uid, target=target, outcome="denied", details={"permission": permission})
        raise Invalid(message, status=403)


def require_permission(permission, audit_success=False, message="You don't have permission to do that."):
    """Route decorator, placed *below* @login_required: the wrapped view receives `ws` first."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(ws, *args, **kwargs):
            from flask import request
            check(ws.uid, permission, target=f"{request.method} {request.path}", message=message)
            out = fn(ws, *args, **kwargs)
            if audit_success:
                audit.record(f"admin.{fn.__name__}", ws.uid, target=f"{request.method} {request.path}", details={"permission": permission})
            return out
        return wrapper
    return deco


def grant(actor_uid, target_uid, role):
    """Change a user's role (needs admin.manage_roles); you can't change your own role."""
    if role not in ROLES:
        raise Invalid("Unknown role.")
    check(actor_uid, "admin.manage_roles", target=f"user:{target_uid}")
    if actor_uid == target_uid:
        audit.record("role.change", actor_uid, target=f"user:{target_uid}", outcome="denied", details={"reason": "self", "role": role})
        raise Invalid("You can't change your own role.", status=403)
    M.roles.update_one({"_id": target_uid}, {"$set": {"role": role, "granted_by": actor_uid, "at": time.time()}}, upsert=True)
    audit.record("role.change", actor_uid, target=f"user:{target_uid}", details={"role": role})
