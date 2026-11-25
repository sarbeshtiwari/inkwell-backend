"""Email and password sign-in (used while email codes can't be sent)."""
from conftest import H

import app as A
from core.accounts import password_of


def signup(c, email="pw@example.com", password="correct horse battery", **kw):
    return c.post("/api/auth/signup", json={"email": email, "name": "Pat Doe", "password": password, "agree": True, **kw}, headers=H)


def test_signup_then_login_and_wrong_password():
    c = A.app.test_client()
    assert signup(c).status_code == 200 and c.get("/api/me", headers=H).get_json()["has_password"] is True
    c.post("/api/auth/logout", headers=H)
    c2 = A.app.test_client()
    bad = c2.post("/api/auth/login", json={"email": "pw@example.com", "password": "nope nope nope"}, headers=H)
    assert bad.status_code == 400 and bad.get_json()["error"] == "Wrong email or password."
    unknown = c2.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope nope nope"}, headers=H)
    assert unknown.get_json()["error"] == "Wrong email or password."
    assert c2.post("/api/auth/login", json={"email": "PW@example.com", "password": "correct horse battery"}, headers=H).status_code == 200
    assert c2.get("/api/me", headers=H).status_code == 200


def test_signup_rules():
    c = A.app.test_client()
    assert signup(c, email="short@example.com", password="abc").get_json()["field"] == "password"
    assert signup(c, email="same@example.com", password="same@example.com").status_code == 400
    assert signup(c, email="noagree@example.com", agree=False).get_json()["field"] == "agree"
    signup(c, email="taken@example.com")
    assert signup(A.app.test_client(), email="taken@example.com").status_code == 409


def test_password_is_hashed_never_stored():
    c = A.app.test_client()
    signup(c, email="hash@example.com", password="a long secret phrase")
    uid = A.find_user("hash@example.com")["id"]
    stored = password_of(uid)
    assert stored.startswith("scrypt$") and "a long secret phrase" not in stored


def test_existing_account_without_password_cannot_be_claimed(user):
    c = A.app.test_client()
    r = c.post("/api/auth/login", json={"email": user["email"], "password": "anything at all"}, headers=H)
    assert r.status_code == 400 and "doesn't have a password yet" in r.get_json()["error"]
    assert signup(c, email=user["email"]).status_code == 409
    # the owner, signed in, sets one; then needs the current one to change it
    me = user["client"]
    assert me.post("/api/account/password", json={"password": "first password here"}, headers=H).status_code == 200
    assert me.post("/api/account/password", json={"password": "second password"}, headers=H).get_json()["field"] == "current"
    assert me.post("/api/account/password", json={"current": "first password here", "password": "second password"}, headers=H).status_code == 200
    assert c.post("/api/auth/login", json={"email": user["email"], "password": "second password"}, headers=H).status_code == 200


def test_too_many_wrong_passwords_locks_for_a_while():
    c = A.app.test_client()
    signup(c, email="lock@example.com")
    c = A.app.test_client()
    codes = [c.post("/api/auth/login", json={"email": "lock@example.com", "password": f"wrong guess {i}"}, headers=H).status_code for i in range(12)]
    assert 429 in codes
    assert c.post("/api/auth/login", json={"email": "lock@example.com", "password": "correct horse battery"}, headers=H).status_code == 429


def test_delete_account_with_password():
    c = A.app.test_client()
    signup(c, email="bye@example.com")
    assert c.post("/api/account/delete", json={"password": "wrong one here"}, headers=H).status_code == 400
    assert c.post("/api/account/delete", json={"password": "correct horse battery"}, headers=H).status_code == 200
    assert A.find_user("bye@example.com") is None
