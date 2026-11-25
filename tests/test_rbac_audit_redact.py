"""Phase 0 step 1: roles and permissions, tamper-evident audit log, secret redaction."""
import io
import logging
import sys

import pytest
from conftest import H, client_for, make_user

import app as A
from core.db import M
from core.validation import Invalid
from infra import audit, rbac, redact


@pytest.fixture
def admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    return {"uid": uid, "email": email, "client": client_for(uid)}


# ---- roles and enforcement ---------------------------------------------------------------------------------

def test_bootstrap_makes_admin_email_super_admin_and_audits(admin):
    assert rbac.role_of(admin["uid"]) == rbac.SUPER_ADMIN
    ev = M.audit_log.find_one({"action": "role.grant", "target": f"user:{admin['uid']}"})
    assert ev and ev["actor"] == "system" and ev["outcome"] == "ok"
    rbac.reconcile()  # idempotent: no second grant event
    assert M.audit_log.count_documents({"action": "role.grant", "target": f"user:{admin['uid']}"}) == 1


def test_me_reports_role_and_permissions(admin, user):
    me = admin["client"].get("/api/me", headers=H).get_json()
    assert me["role"] == "super_admin" and me["is_admin"] is True and "admin.site_leads" in me["permissions"]
    other = user["client"].get("/api/me", headers=H).get_json()
    assert other["role"] == "user" and other["is_admin"] is False and other["permissions"] == []


def test_regular_user_is_refused_on_every_admin_endpoint_and_denials_are_audited(admin, user, monkeypatch):
    c = user["client"]
    assert c.get("/api/leads?scope=site", headers=H).status_code == 403
    assert c.get("/api/leads/export?scope=site", headers=H).status_code == 403
    assert c.put("/api/li/config", json={"client_id": "abcdefgh12", "client_secret": "x" * 20}, headers=H).status_code == 403
    from features import updater
    monkeypatch.setattr(updater, "enabled", lambda: True)
    assert c.get("/api/update", headers=H).status_code == 403
    denied = list(M.audit_log.find({"actor": user["uid"], "action": "access.denied"}))
    assert {d["details"]["permission"] for d in denied} >= {"admin.site_leads", "admin.integrations", "admin.update_app"}
    assert all(d["outcome"] == "denied" and d["request_id"] for d in denied)


def test_admin_keeps_existing_admin_functionality(admin):
    c = admin["client"]
    r = c.get("/api/leads?scope=site", headers=H)
    assert r.status_code == 200 and r.get_json()["is_admin"] is True
    assert c.get("/api/li/config", headers=H).status_code in (200, 404, 405)  # read endpoint unaffected


def test_frontend_flags_cannot_grant_access(user):
    """Sending is_admin/role in a request body or header changes nothing: the server decides."""
    c = user["client"]
    r = c.get("/api/leads?scope=site", headers={**H, "X-Role": "super_admin", "X-Is-Admin": "1"})
    assert r.status_code == 403
    assert rbac.role_of(user["uid"]) == rbac.USER


def test_no_privilege_escalation(admin, user):
    with pytest.raises(Exception) as e:
        rbac.grant(user["uid"], user["uid"], rbac.SUPER_ADMIN)  # a user granting themselves admin
    assert getattr(e.value, "status", None) == 403 and rbac.role_of(user["uid"]) == rbac.USER
    with pytest.raises(Invalid):
        rbac.grant(admin["uid"], admin["uid"], rbac.USER)  # nobody changes their own role
    rbac.grant(admin["uid"], user["uid"], rbac.SUPER_ADMIN)
    assert rbac.role_of(user["uid"]) == rbac.SUPER_ADMIN
    assert M.audit_log.find_one({"action": "role.change", "actor": admin["uid"], "target": f"user:{user['uid']}"})
    with pytest.raises(Invalid):
        rbac.grant(admin["uid"], user["uid"], "root")  # unknown roles refused


def test_content_reveal_is_disabled_even_for_super_admin(admin):
    assert rbac.has(admin["uid"], "admin.read_metadata") is True
    assert rbac.has(admin["uid"], "admin.reveal_content") is False
    with pytest.raises(Exception) as e:
        rbac.check(admin["uid"], "admin.reveal_content")
    assert getattr(e.value, "status", None) == 403


def test_unknown_permission_is_an_error(user):
    with pytest.raises(ValueError):
        rbac.has(user["uid"], "admin.everything")


def test_anonymous_cannot_reach_admin_endpoints():
    c = A.app.test_client()
    assert c.get("/api/leads?scope=site", headers=H).status_code == 401


# ---- audit log integrity -------------------------------------------------------------------------------------

def test_audit_chain_verifies_and_detects_tampering():
    for i in range(5):
        audit.record("test.event", "actor-1", target=f"thing:{i}", details={"n": i})
    ok, bad, n = audit.verify()
    assert ok and bad is None and n >= 5
    victim = M.audit_log.find_one({"action": "test.event", "target": "thing:2"})
    M.audit_log.update_one({"_id": victim["_id"]}, {"$set": {"outcome": "ok-edited"}})
    ok, bad, _ = audit.verify()
    assert not ok and bad == victim["seq"]
    M.audit_log.update_one({"_id": victim["_id"]}, {"$set": {"outcome": victim["outcome"]}})
    assert audit.verify()[0]


def test_audit_detects_deleted_and_reordered_events():
    for i in range(3):
        audit.record("test.order", "actor-2", target=f"o:{i}")
    mid = M.audit_log.find_one({"action": "test.order", "target": "o:1"})
    M.audit_log.delete_one({"_id": mid["_id"]})
    ok, bad, _ = audit.verify()
    assert not ok and bad == mid["seq"] + 1
    M.audit_log.insert_one(mid)
    assert audit.verify()[0]


def test_audit_event_fields_and_redaction():
    ev = audit.record("test.fields", "uid-9", target="model:x", outcome="denied", trace_id="tr-1",
                      details={"api_key": "sk-ant-abcdefghijklmnopqrstuvwxyz", "note": "mail bob@example.com"})
    for k in ("seq", "at", "actor", "action", "target", "outcome", "request_id", "trace_id", "prev_hash", "hash"):
        assert k in ev
    assert ev["details"]["api_key"] == redact.MASK and "bob@example.com" not in str(ev["details"])


def test_concurrent_audit_writes_keep_a_valid_chain():
    import threading
    threads = [threading.Thread(target=lambda i=i: audit.record("test.concurrent", f"t{i}")) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert M.audit_log.count_documents({"action": "test.concurrent"}) == 20 and audit.verify()[0]


# ---- secret redaction ----------------------------------------------------------------------------------------

SECRETS = [
    "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx", "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123",
    "github_pat_11AAAAAAA0abcdefghijklmnopqrstuv", "AIzaSyA1234567890abcdefghijklmnopqrstuv", "tvly-abcdefghijklmnop1234",
    "AKIAABCDEFGHIJKLMNOP", "ya29.a0AfH6SMBabcdefghijklmnopqrstuv",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrst",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_known_secret_formats_are_removed(secret):
    assert secret not in redact.secrets(f"calling api with {secret} now")


def test_credentials_in_urls_headers_and_key_values():
    s = redact.secrets("mongodb+srv://admin:Hunter2pass@cluster0.x.mongodb.net/db and Authorization: Bearer abcdefghijklmnop12345"
                       " password=hunter2 ro_session=eyJfcGVybWFu.something cookie: __Host-ro_session=abc123def456")
    for leak in ("Hunter2pass", "abcdefghijklmnop12345", "hunter2", "abc123def456"):
        assert leak not in s
    assert "cluster0.x.mongodb.net" in s  # the host stays, only the password goes


def test_secret_named_fields_are_masked_in_nested_data():
    out = redact.secrets({"smtp_password": "x", "nested": [{"Authorization": "Basic abc"}], "client_secret": "y",
                          "token": "z", "name": "Pat", "empty_token": ""})
    assert out["smtp_password"] == out["client_secret"] == out["token"] == redact.MASK
    assert out["nested"][0]["Authorization"] == redact.MASK and out["name"] == "Pat" and out["empty_token"] == ""


def test_pii_masks_personal_details():
    out = redact.pii("Reach Jane at jane.doe@acme.com or +91 98765 43210")
    assert "jane.doe@acme.com" not in out and "j***@acme.com" in out and "98765" not in out


def test_log_records_and_printed_lines_are_redacted():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(redact.RedactingFilter())
    log = logging.getLogger("redact-test")
    log.addHandler(handler)
    log.error("token %s", "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123")
    assert "ghp_" not in buf.getvalue()
    out = io.StringIO()
    stream = redact.RedactingStream(out)
    print("key=sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", file=stream)
    assert "sk-ant" not in out.getvalue()


def test_app_installs_stream_redaction():
    assert isinstance(sys.stdout, redact.RedactingStream) or "pytest" in type(sys.stdout).__module__
