"""Sharing contacts between accounts: only chosen contacts, only on acceptance, never your history with them."""

from conftest import H, client_for, make_user


def _add(c, **f):
    return c.post("/api/recipients", json=f, headers=H).get_json()["id"]


def _contacts(c):
    return c.get("/api/state").get_json()["recipients"]


def test_share_reaches_friend_only_after_accept(user):
    me = user["client"]
    a = _add(me, name="Asha", company="Corp", email="asha@corp.example", location="Pune")
    _add(me, name="Ben", email="ben@corp.example")  # not shared
    friend_uid, friend_email = make_user(name="Friend")
    friend = client_for(friend_uid)
    r = me.post("/api/shares", json={"email": friend_email.upper(), "ids": [a], "note": "good leads"}, headers=H)
    assert r.status_code == 200 and r.get_json()["count"] == 1
    assert _contacts(friend) == []  # nothing until they accept
    inc = friend.get("/api/shares").get_json()["incoming"]
    assert len(inc) == 1 and inc[0]["note"] == "good leads" and inc[0]["preview"] == ["Asha"]
    assert inc[0]["from_email"] == user["email"] and inc[0]["from"] != "A Reachout user"
    r = friend.post(f"/api/shares/{inc[0]['id']}/accept", headers=H)
    assert r.status_code == 200 and r.get_json()["added"] == 1
    got = _contacts(friend)
    assert [c["email"] for c in got] == ["asha@corp.example"] and got[0]["location"] == "Pune"
    assert got[0]["list"].startswith("Shared by") and got[0]["stage"] == "new"
    assert friend.post(f"/api/shares/{inc[0]['id']}/accept", headers=H).status_code == 400  # not twice
    assert me.get("/api/shares").get_json()["outgoing"][0]["status"] == "accepted"


def test_share_leaves_out_history_and_skips_duplicates(user):
    me = user["client"]
    a = _add(me, name="Asha", email="asha@corp.example")
    me.put(f"/api/recipients/{a}", json={"stage": "won"}, headers=H)
    friend_uid, friend_email = make_user()
    friend = client_for(friend_uid)
    _add(friend, name="Already", email="asha@corp.example")
    sid = me.post("/api/shares", json={"email": friend_email, "ids": [a]}, headers=H).get_json()["id"]
    r = friend.post(f"/api/shares/{sid}/accept", headers=H).get_json()
    assert r["added"] == 0 and r["duplicates"] == 1
    assert [c["name"] for c in _contacts(friend)] == ["Already"]


def test_decline_cancel_and_bad_targets(user):
    me = user["client"]
    a = _add(me, name="Asha", email="asha@corp.example")
    assert me.post("/api/shares", json={"email": "nobody@nowhere.example", "ids": [a]}, headers=H).status_code == 400
    assert me.post("/api/shares", json={"email": user["email"], "ids": [a]}, headers=H).status_code == 400
    friend_uid, friend_email = make_user()
    friend, stranger = client_for(friend_uid), client_for(make_user()[0])
    sid = me.post("/api/shares", json={"email": friend_email, "ids": [a]}, headers=H).get_json()["id"]
    assert stranger.post(f"/api/shares/{sid}/accept", headers=H).status_code == 400  # not theirs
    assert friend.delete(f"/api/shares/{sid}", headers=H).status_code == 400  # only the sender can cancel
    assert friend.post(f"/api/shares/{sid}/decline", headers=H).status_code == 200
    assert friend.get("/api/shares").get_json()["incoming"] == [] and _contacts(friend) == []
    sid = me.post("/api/shares", json={"email": friend_email, "ids": [a]}, headers=H).get_json()["id"]
    assert me.delete(f"/api/shares/{sid}", headers=H).status_code == 200
    assert friend.get("/api/shares").get_json()["incoming"] == []
