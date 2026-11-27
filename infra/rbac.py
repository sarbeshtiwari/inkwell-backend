"""Roles and permissions, enforced on the server.

Roles live in the `roles` collection (one document per uid that has more than the default role). The first Super
Admin is bootstrapped from ADMIN_EMAILS (your configured admin email). Without ADMIN_EMAILS, on a computer that
isn't a production server, the oldest account keeps owner rights as before, so existing local setups keep working.

`@require_permission(...)` wraps a route that already has @login_required: it checks the permission, writes an
audit event for denials (and for allowed calls when `audit=True`), and returns 403 otherwise."""

import functools
import re
import time
import uuid

from pymongo.errors import DuplicateKeyError, PyMongoError

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
    M.roles.create_index("pending_audit.id", sparse=True)


EMAIL_OK = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[a-z]{2,}$")


def reconcile(admin_emails=None):
    """Make stored Super Admin roles match ADMIN_EMAILS: grant configured accounts, revoke roles that came from the
    config for accounts no longer listed. Manual grants (made by an admin through grant()) are left alone.

    Runs only on a configuration it can trust; otherwise it changes nothing and says why:
    - ADMIN_EMAILS empty, or containing anything that isn't a valid address;
    - none of the configured addresses belongs to an existing account (likely a typo: revoking now could leave
      nobody as admin).
    Grants are audited first and skipped if the audit fails (fail closed). Revocations always happen at once (they only
    reduce access); their audit event goes through a durable outbox (see revoke())."""
    flush_pending_audit()
    emails = {e.strip().lower() for e in (ADMIN_EMAILS if admin_emails is None else admin_emails) if e and e.strip()}
    report = {"granted": [], "revoked": [], "skipped": ""}
    if not emails:
        report["skipped"] = "ADMIN_EMAILS is empty"
        return report
    bad = sorted(e for e in emails if not EMAIL_OK.match(e))
    if bad:
        report["skipped"] = f"ADMIN_EMAILS has {len(bad)} invalid address(es)"
        print(f"[Inkwell] Admin roles not reconciled: {report['skipped']}.", flush=True)
        return report
    wanted = {}
    for email in emails:
        row = M.users.find_one({"email_hash": lookup_hash(email)}, {"_id": 1})
        if row:
            wanted[row["_id"]] = email
    if not wanted:
        report["skipped"] = "no configured admin email belongs to an existing account"
        print(f"[Inkwell] Admin roles not reconciled: {report['skipped']}.", flush=True)
        return report
    for uid in sorted(wanted):
        current = M.roles.find_one({"_id": uid}) or {}
        if current.get("role") == SUPER_ADMIN:
            continue
        try:
            audit.record("role.grant", "system", target=f"user:{uid}", details={"role": SUPER_ADMIN, "via": "ADMIN_EMAILS"})
        except audit.AuditError:
            continue  # fail closed: no unaudited grant
        M.roles.update_one({"_id": uid}, {"$set": {"role": SUPER_ADMIN, "granted_by": "config:ADMIN_EMAILS", "at": time.time()}},
                           upsert=True)
        report["granted"].append(uid)
    for row in M.roles.find({"role": SUPER_ADMIN, "granted_by": {"$in": ["config:ADMIN_EMAILS", "bootstrap:ADMIN_EMAILS"]}}):
        if row["_id"] not in wanted:
            revoke(row["_id"], "system", details={"role": SUPER_ADMIN, "via": "ADMIN_EMAILS"})
            report["revoked"].append(row["_id"])
    return report


def revoke(uid, actor, details, granted_by="config:revoked"):
    """Remove Super Admin now, whatever state the audit log is in.

    The revocation and its audit event are stored in ONE atomic write to the role document: the event goes into
    `pending_audit` (an outbox) together with the role change. Then it's appended to the audit log and removed from the
    outbox. If the audit log is unavailable, access is still revoked, the event stays in the outbox (durable, it can't
    be lost without losing the revocation too) and an alert is raised; flush_pending_audit() writes it later."""
    event = {"id": uuid.uuid4().hex, "action": "role.revoke", "actor": actor, "target": f"user:{uid}",
             "details": details, "occurred_at": time.time()}
    try:  # upsert covers a local owner who is admin without a stored role
        done = M.roles.update_one({"_id": uid, "role": {"$ne": USER}},
                                  {"$set": {"role": USER, "granted_by": granted_by, "at": event["occurred_at"]},
                                   "$push": {"pending_audit": event}}, upsert=True)
    except DuplicateKeyError:
        return  # already a regular user
    if done.modified_count or done.upserted_id:
        _flush_one(uid, event)


def _flush_one(uid, event):
    """Append one outbox event to the audit log (once), then drop it from the outbox. False if the log is unavailable."""
    try:
        if not M.audit_log.find_one({"details.outbox_id": event["id"]}, {"_id": 1}):  # not already written before a crash
            audit.record(event["action"], event["actor"], target=event["target"],
                         details={**event["details"], "outbox_id": event["id"], "occurred_at": event["occurred_at"]})
        M.roles.update_one({"_id": uid}, {"$pull": {"pending_audit": {"id": event["id"]}}})
        return True
    except (audit.AuditError, PyMongoError) as e:
        audit.alert(f"admin role for {uid} was revoked, but its audit event is waiting in the outbox ({type(e).__name__}); "
                    f"it will be written by flush_pending_audit()")
        return False


def flush_pending_audit():
    """Write every outbox event to the audit log (run at startup and before each reconcile). Returns how many remain."""
    left = 0
    for row in M.roles.find({"pending_audit.0": {"$exists": True}}, {"pending_audit": 1}):
        for event in sorted(row["pending_audit"], key=lambda e: e["occurred_at"]):
            if not _flush_one(row["_id"], event):
                left += 1
    return left


bootstrap = reconcile  # startup entry point (kept name)


def role_of(uid):
    if not uid:
        return USER
    row = M.roles.find_one({"_id": uid}, {"role": 1})
    if row and row.get("role") in ROLES:
        return row["role"]
    # No stored role: admin rights only come from audited role records (see reconcile()). The one exception is a
    # computer that isn't a production server and has no ADMIN_EMAILS at all, where the oldest account is the owner
    # (unchanged local behaviour).
    if ADMIN_EMAILS or PRODUCTION:
        return USER
    first = M.users.find_one({}, {"_id": 1}, sort=[("created", 1)])
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
        try:
            audit.record("access.denied", uid, target=target, outcome="denied", details={"permission": permission})
        except audit.AuditError:
            pass  # still denied
        raise Invalid(message, status=403)


def require_permission(permission, audit_success=False, message="You don't have permission to do that."):
    """Route decorator, placed *below* @login_required: the wrapped view receives `ws` first."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(ws, *args, **kwargs):
            from flask import request
            check(ws.uid, permission, target=f"{request.method} {request.path}", message=message)
            if audit_success:  # recorded before the action: if the audit can't be stored, the action doesn't run
                audit.record(f"admin.{fn.__name__}", ws.uid, target=f"{request.method} {request.path}", details={"permission": permission})
            return fn(ws, *args, **kwargs)
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
    if role == USER and role_of(target_uid) == SUPER_ADMIN:  # removing access: immediate, audited via the outbox
        return revoke(target_uid, actor_uid, details={"role": SUPER_ADMIN, "via": "manual"}, granted_by=actor_uid)
    audit.record("role.change", actor_uid, target=f"user:{target_uid}", details={"role": role})  # fail closed
    M.roles.update_one({"_id": target_uid}, {"$set": {"role": role, "granted_by": actor_uid, "at": time.time()}}, upsert=True)
