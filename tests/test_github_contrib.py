"""Contributing from Inkwell's GitHub page: fork, comment on an issue, open a pull request from a fork."""

from conftest import H

from features import github as G


def test_fork_comment_and_pull_from_fork(user, monkeypatch):
    c, ws = user["client"], user["ws"]
    with ws.lock:
        ws.save("github", {"token": "t", "login": "me"})
    calls = []

    def fake(ws_, method, path, ok=(200, 201, 204), **kw):
        calls.append((method, path, kw.get("json")))
        if path == "/user":
            return {"login": "me", "id": 7, "name": "Me"}
        if path == "/repos/me/proj":
            raise G.C().Invalid("Not found", status=404)
        if path.endswith("/forks"):
            return {"name": "proj", "default_branch": "main", "html_url": "https://github.com/me/proj"}
        if path.endswith("/comments"):
            return {"html_url": "https://github.com/up/proj/issues/5#c1", "id": 1}
        if path.endswith("/pulls"):
            return {"number": 9, "title": "fix", "state": "open", "user": {"login": "me"}, "head": {"ref": "fix/5"}, "base": {"ref": "main"},
                    "created_at": "", "updated_at": "", "html_url": "https://github.com/up/proj/pull/9"}
        return {}
    monkeypatch.setattr(G, "gh", fake)
    assert c.post("/api/gh/fork/up/proj", json={}, headers=H).get_json()["owner"] == "me"
    assert c.post("/api/gh/issue/up/proj/5/comments", json={"body": "I'd like to take this"}, headers=H).get_json()["id"] == 1
    assert c.post("/api/gh/issue/up/proj/5/comments", json={"body": " "}, headers=H).status_code == 400
    r = c.post("/api/gh/pulls/up/proj", json={"title": "fix", "head": "fix/5", "head_owner": "me", "base": "main"}, headers=H)
    assert r.get_json()["number"] == 9
    assert next(j for m, p, j in calls if p.endswith("/pulls"))["head"] == "me:fix/5"
    assert G.noreply_author(ws)["email"] == "7+me@users.noreply.github.com"
