"""Phase 0 step 1 security fixes: audit concurrency, truncation, key rotation, fail-closed writes, admin
reconciliation, redaction of persisted errors, update-progress permission."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import DB_NAME, H, client_for, make_user

from core.db import M
from infra import audit, rbac, redact

BACKEND = Path(__file__).resolve().parents[1]


def _reset_audit():
    M.audit_log.delete_many({})
    M.audit_meta.delete_many({})
    audit.ensure_indexes()


# ---- 1. concurrency across independent processes ----------------------------------------------------------------

WRITER = """
import os, sys
sys.path.insert(0, {backend!r})
from core.db import M
M.connect()
from infra import audit
for i in range({n}):
    audit.record("mp.write", "proc-" + str(os.getpid()), target=str(i))
"""


def test_four_processes_write_100_events_each_with_no_loss_or_duplicates():
    _reset_audit()
    env = {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1"}
    procs = [subprocess.Popen([sys.executable, "-c", WRITER.format(backend=str(BACKEND), n=100)], env=env, cwd=BACKEND)
             for _ in range(4)]
    assert all(p.wait(timeout=240) == 0 for p in procs)
    seqs = [d["seq"] for d in M.audit_log.find({}, {"seq": 1}).sort("seq", 1)]
    assert M.audit_log.count_documents({"action": "mp.write"}) == 400
    assert seqs == list(range(1, 401))  # no gaps, no duplicates
    assert len({d["actor"] for d in M.audit_log.find({"action": "mp.write"})}) == 4
    assert audit.verify() == (True, None, 400)
    assert audit.checkpoint()["seq"] == 400


# ---- 3. truncation / checkpoint ---------------------------------------------------------------------------------

def test_deleting_the_newest_events_is_detected():
    _reset_audit()
    for i in range(5):
        audit.record("t.trunc", "a", target=str(i))
    assert audit.verify()[0]
    M.audit_log.delete_many({"seq": {"$gte": 4}})  # cut the tail
    ok, bad, _ = audit.verify()
    assert not ok and bad == 4


def test_checkpoint_tampering_is_detected():
    _reset_audit()
    audit.record("t.cp", "a")
    M.audit_meta.update_one({"_id": "head"}, {"$set": {"hash": "0" * 64}})
    assert not audit.verify()[0]


def test_crash_between_insert_and_checkpoint_is_repaired_forward_only():
    _reset_audit()
    audit.record("t.crash", "a")
    M.audit_meta.update_one({"_id": "head"}, {"$set": {"seq": 0, "hash": ""}})  # as if the process died after insert
    assert not audit.verify()[0]
    assert audit.repair_head() == 1 and audit.verify()[0]
    # a forged event (bad hash) appended after the head is never adopted
    M.audit_log.insert_one({"seq": 2, "at": 1.0, "actor": "x", "action": "forged", "target": "", "outcome": "ok",
                            "request_id": "", "trace_id": "", "details": {}, "key_id": "k0", "prev_hash": "", "hash": "f" * 64})
    assert audit.repair_head() == 1 and not audit.verify()[0]


# ---- 4. key rotation --------------------------------------------------------------------------------------------

def test_key_rotation_keeps_old_events_verifiable_and_never_resigns(monkeypatch):
    _reset_audit()
    audit.record("t.rot", "a", target="before")
    before = M.audit_log.find_one({"target": "before"})
    monkeypatch.setenv("AUDIT_KEYS", "a1:" + "r" * 40)
    monkeypatch.setenv("AUDIT_KEY_ID", "a1")
    audit.record("t.rot", "a", target="after")
    after = M.audit_log.find_one({"target": "after"})
    assert before["key_id"] == "k0" and after["key_id"] == "a1"
    assert M.audit_log.find_one({"target": "before"})["hash"] == before["hash"]  # not re-signed
    assert audit.verify()[0]
    monkeypatch.setenv("AUDIT_KEYS", "")  # losing the new key: its events can't be verified any more
    monkeypatch.setenv("AUDIT_KEY_ID", "k0")
    assert not audit.verify()[0]


def test_events_signed_before_key_ids_still_verify():
    """Events already on the live server were signed without key_id: they must keep verifying, unchanged."""
    _reset_audit()
    import hashlib
    import hmac

    old = {"seq": 1, "at": 1.5, "actor": "system", "action": "role.grant", "target": "user:x", "outcome": "ok",
           "request_id": "", "trace_id": "", "details": {}, "prev_hash": ""}
    body = audit.json.dumps({k: old.get(k) for k in audit.LEGACY_FIELDS}, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, default=str).encode()
    old["hash"] = hmac.new(audit._keys()[0]["k0"], b"|" + body, hashlib.sha256).hexdigest()
    M.audit_log.insert_one(dict(old))
    assert audit.repair_head() == 1
    audit.record("t.after_legacy", "a")
    assert audit.verify() == (True, None, 2)
    assert M.audit_log.find_one({"seq": 1})["hash"] == old["hash"]


def test_misconfigured_active_key_fails_closed(monkeypatch):
    monkeypatch.setenv("AUDIT_KEY_ID", "missing")
    with pytest.raises(audit.AuditError):
        audit.record("t.badkey", "a")


# ---- fail closed on database failure ----------------------------------------------------------------------------

def test_database_failure_raises_and_recovery_works(monkeypatch):
    _reset_audit()
    from pymongo.errors import AutoReconnect
    monkeypatch.setattr(audit, "RETRY_SECONDS", 0.3)
    calls = {"n": 0}
    real = audit._write

    def flaky(base):
        calls["n"] += 1
        raise AutoReconnect("primary stepped down")
    monkeypatch.setattr(audit, "_write", flaky)
    with pytest.raises(audit.AuditError):
        audit.record("t.down", "a")
    assert calls["n"] >= 1 and M.audit_log.count_documents({"action": "t.down"}) == 0
    monkeypatch.setattr(audit, "_write", real)  # database back
    audit.record("t.up", "a")
    assert audit.verify()[0]


def test_role_change_is_not_applied_when_audit_fails(monkeypatch):
    uid_a, email_a = make_user()
    uid_b, _ = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email_a})
    rbac.reconcile()
    monkeypatch.setattr(audit, "_write", lambda base: (_ for _ in ()).throw(audit.AuditError("down")))
    with pytest.raises(audit.AuditError):
        rbac.grant(uid_a, uid_b, rbac.SUPER_ADMIN)
    assert rbac.role_of(uid_b) == rbac.USER


# ---- 2. admin reconciliation ------------------------------------------------------------------------------------

def test_removing_email_revokes_and_readding_regrants(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    assert uid in rbac.reconcile()["granted"] and rbac.role_of(uid) == rbac.SUPER_ADMIN
    other_uid, other = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {other})
    rep = rbac.reconcile()
    assert uid in rep["revoked"] and other_uid in rep["granted"] and rbac.role_of(uid) == rbac.USER
    assert M.audit_log.find_one({"action": "role.revoke", "target": f"user:{uid}"})
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email, other})
    assert uid in rbac.reconcile()["granted"] and rbac.role_of(uid) == rbac.SUPER_ADMIN
    client = client_for(uid)
    assert client.get("/api/leads?scope=site", headers=H).status_code == 200


@pytest.mark.parametrize("config,reason", [(set(), "empty"), ({"not-an-email"}, "invalid"),
                                           ({"nobody@nowhere.example"}, "no configured admin")])
def test_bad_config_never_revokes_admins(monkeypatch, config, reason):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", config)
    rep = rbac.reconcile()
    assert reason in rep["skipped"] and rep["revoked"] == [] and rbac.role_of(uid) == rbac.SUPER_ADMIN


def test_manual_grants_are_not_revoked_by_config(monkeypatch):
    a_uid, a_email = make_user()
    b_uid, _ = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {a_email})
    rbac.reconcile()
    rbac.grant(a_uid, b_uid, rbac.SUPER_ADMIN)
    rbac.reconcile()
    assert rbac.role_of(b_uid) == rbac.SUPER_ADMIN


def test_production_without_stored_role_is_not_admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})  # configured but never reconciled (no audited grant)
    M.roles.delete_many({"_id": uid})
    assert rbac.role_of(uid) == rbac.USER


# ---- 5. redaction of persisted errors ----------------------------------------------------------------------------

def test_error_text_removes_secrets_from_nested_exceptions():
    try:
        try:
            raise ConnectionError("auth failed for mongodb+srv://admin:Sup3rSecret@cluster0.x.mongodb.net/db")
        except ConnectionError as inner:
            raise RuntimeError("sync failed, token=ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123") from inner
    except RuntimeError as e:
        text = redact.error_text(e)
    assert "Sup3rSecret" not in text and "ghp_" not in text
    assert "caused by ConnectionError" in text and "cluster0.x.mongodb.net" in text


def test_imap_style_credential_urls_and_headers():
    text = redact.error_text(OSError("LOGIN failed imaps://me%40gmail.com:app-pass-1234@imap.gmail.com:993 Authorization: Bearer abcdefghijklmnop12345"))
    assert "app-pass-1234" not in text and "abcdefghijklmnop12345" not in text


def test_inbox_sync_stores_redacted_error(user, monkeypatch):
    import bridge
    from features import inbox
    ws = user["ws"]
    ws.save("profile", {**(ws.profile() or {}), "smtp_password": "x", "smtp_user": "me@example.com", "smtp_host": "imap.example.com"})

    def boom(*a, **k):
        raise OSError("connect failed: https://bob:hunter2pass@mail.example.com/ api_key=sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    monkeypatch.setattr(bridge.C, "imap_connect", boom)
    with pytest.raises(OSError):
        inbox.scan(ws)
    saved = str(ws.load("inbox_sync", {}) or {})
    assert "Sync failed" in saved and "hunter2pass" not in saved and "sk-ant-api03" not in saved


# ---- 6. update progress permission -------------------------------------------------------------------------------

def test_update_progress_requires_admin(user, monkeypatch):
    assert user["client"].get("/api/update/progress", headers=H).status_code == 403
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    r = client_for(uid).get("/api/update/progress", headers=H)
    assert r.status_code == 200 and "job" in r.get_json()


# ---- crash recovery: a writer killed (SIGKILL) part-way through a write -------------------------------------------

CRASHER = """
import os, signal, sys
sys.path.insert(0, {backend!r})
from core.db import M
M.connect()
from infra import audit
point, crash = {point!r}, {{"on": False}}
digest, head = audit._digest, audit._advance_head
def _digest(doc, prev, key):          # runs after the next seq is chosen, before the insert
    if crash["on"] and point == "before_insert":
        os.kill(os.getpid(), signal.SIGKILL)
    return digest(doc, prev, key)
def _advance_head(*a):                # runs after the insert, before the checkpoint moves
    if crash["on"] and point == "after_insert":
        os.kill(os.getpid(), signal.SIGKILL)
    return head(*a)
audit._digest, audit._advance_head = _digest, _advance_head
for i in range({before}):
    audit.record("crash.pre", "crasher", target=str(i))
crash["on"] = True
audit.record("crash.me", "crasher")
"""
RESTART = "import sys; sys.path.insert(0, {backend!r}); import app"  # app start runs audit.ensure_indexes -> repair_head


def _env():
    return {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1"}


def _spawn(code):
    return subprocess.Popen([sys.executable, "-c", code], env=_env(), cwd=BACKEND)


def _crash(point, before=0):
    p = _spawn(CRASHER.format(backend=str(BACKEND), point=point, before=before))
    assert p.wait(timeout=60) == -9  # really killed
    return p


def _restart():
    assert _spawn(RESTART.format(backend=str(BACKEND))).wait(timeout=120) == 0


def _seqs():
    return [d["seq"] for d in M.audit_log.find({}, {"seq": 1}).sort("seq", 1)]


def test_kill_after_choosing_seq_before_insert_leaves_no_gap():
    _reset_audit()
    for i in range(3):
        audit.record("t.pre", "a")
    _crash("before_insert", before=2)
    assert _seqs() == [1, 2, 3, 4, 5]  # the chosen seq 6 was never stored, so it isn't missing either
    assert audit.verify() == (True, None, 5)
    assert audit.record("t.next", "a")["seq"] == 6 and audit.verify()[0]


def test_kill_after_insert_before_checkpoint_recovers_on_restart():
    _reset_audit()
    for i in range(3):
        audit.record("t.pre", "a")
    _crash("after_insert")
    assert _seqs() == [1, 2, 3, 4]
    assert audit.checkpoint()["seq"] == 3  # head never moved past what was confirmed
    assert not audit.verify()[0]  # the lag is visible until recovery
    _restart()
    assert audit.checkpoint()["seq"] == 4 and audit.verify() == (True, None, 4)
    assert audit.record("t.next", "a")["seq"] == 5 and audit.verify()[0]


def test_kill_after_insert_does_not_block_the_next_writer():
    _reset_audit()
    audit.record("t.pre", "a")
    _crash("after_insert")
    assert audit.record("t.next", "a")["seq"] == 3  # no restart needed to keep writing
    assert audit.checkpoint()["seq"] == 3 and audit.verify() == (True, None, 3)


@pytest.mark.parametrize("point", ["before_insert", "after_insert"])
def test_writer_killed_among_concurrent_writers(point):
    _reset_audit()
    writers = [_spawn(WRITER.format(backend=str(BACKEND), n=100)) for _ in range(3)]
    crasher = _spawn(CRASHER.format(backend=str(BACKEND), point=point, before=50))
    assert all(w.wait(timeout=240) == 0 for w in writers) and crasher.wait(timeout=240) == -9
    total = 300 + 50 + (1 if point == "after_insert" else 0)
    assert _seqs() == list(range(1, total + 1))  # nothing missing, nothing duplicated
    _restart()
    assert audit.verify() == (True, None, total) and audit.checkpoint()["seq"] == total
    assert audit.record("t.after", "a")["seq"] == total + 1


# ---- revocation while the audit log is unavailable ---------------------------------------------------------------

@pytest.fixture
def take_audit_down(monkeypatch):
    """Call it to make the audit log unavailable; monkeypatch.undo() brings it back."""
    from pymongo.errors import AutoReconnect

    def down(base):
        raise AutoReconnect("audit storage unavailable")

    def apply():
        monkeypatch.setattr(audit, "RETRY_SECONDS", 0.2)
        monkeypatch.setattr(audit, "_write", down)
    return apply


def _alerts(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "inkwell.alert"]


def test_revocation_is_immediate_and_its_audit_is_recoverable(monkeypatch, take_audit_down, caplog):
    uid, email = make_user()
    _, other = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email, other})
    rbac.reconcile()
    take_audit_down()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {other})
    assert uid in rbac.reconcile()["revoked"]

    assert rbac.role_of(uid) == rbac.USER  # access gone at once
    assert client_for(uid).get("/api/leads?scope=site", headers=H).status_code == 403
    pending = M.roles.find_one({"_id": uid})["pending_audit"]  # stored in the same write as the revocation
    assert len(pending) == 1 and pending[0]["action"] == "role.revoke"
    assert any("waiting in the outbox" in m for m in _alerts(caplog))  # operational alert
    assert not M.audit_log.find_one({"action": "role.revoke", "target": f"user:{uid}"})

    monkeypatch.undo()  # database recovers (also restores ADMIN_EMAILS etc.)
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {other})
    rbac.reconcile()  # startup path: flushes the outbox first
    ev = M.audit_log.find_one({"action": "role.revoke", "target": f"user:{uid}"})
    assert ev and ev["details"]["outbox_id"] == pending[0]["id"] and ev["details"]["occurred_at"] == pending[0]["occurred_at"]
    assert M.roles.find_one({"_id": uid}).get("pending_audit") == []
    assert rbac.flush_pending_audit() == 0
    assert M.audit_log.count_documents({"details.outbox_id": pending[0]["id"]}) == 1  # written exactly once
    assert audit.verify()[0] and rbac.role_of(uid) == rbac.USER


def test_outbox_is_not_written_twice_after_a_crash_mid_flush():
    uid, _ = make_user()
    event = {"id": "ob-1", "action": "role.revoke", "actor": "system", "target": f"user:{uid}", "details": {"role": "super_admin"},
             "occurred_at": 1.0}
    M.roles.update_one({"_id": uid}, {"$set": {"role": rbac.USER, "pending_audit": [event]}}, upsert=True)
    audit.record("role.revoke", "system", target=f"user:{uid}", details={"outbox_id": "ob-1"})  # written, then "crashed"
    assert rbac.flush_pending_audit() == 0
    assert M.audit_log.count_documents({"details.outbox_id": "ob-1"}) == 1
    assert M.roles.find_one({"_id": uid})["pending_audit"] == []


def test_manual_demotion_while_audit_is_down_is_immediate(monkeypatch, take_audit_down, caplog):
    a_uid, a_email = make_user()
    b_uid, _ = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {a_email})
    rbac.reconcile()
    rbac.grant(a_uid, b_uid, rbac.SUPER_ADMIN)
    take_audit_down()
    rbac.grant(a_uid, b_uid, rbac.USER)
    assert rbac.role_of(b_uid) == rbac.USER and len(M.roles.find_one({"_id": b_uid})["pending_audit"]) == 1
    assert _alerts(caplog)
    monkeypatch.undo()  # database recovers
    assert rbac.flush_pending_audit() == 0
    ev = M.audit_log.find_one({"action": "role.revoke", "target": f"user:{b_uid}"})
    assert ev and ev["actor"] == a_uid and ev["details"]["via"] == "manual" and audit.verify()[0]
