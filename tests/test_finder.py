"""Website email finder: which addresses count, how pages are read, and the run → save-to-contacts flow."""

import io

import pytest
from conftest import H

from features import finder as F
from infra import tasks


def run_agents():
    while tasks.run_once("test", lease=10) is not None:
        pass


@pytest.mark.parametrize("email,kind", [
    ("hr@acme.com", "hr"), ("hr.team@acme.com", "hr"), ("HRD@acme.com", "hr"), ("hr2@acme.com", "hr"),
    ("teamhr@acme.com", "hr"), ("human.resources@acme.com", "hr"), ("careers@acme.com", "careers"),
    ("career@acme.com", "careers"), ("india.careers@acme.com", "careers"), ("jobs@acme.com", "hiring"),
    ("recruitment@acme.com", "hiring"), ("talent@acme.com", "hiring"),
    ("chris@acme.com", ""), ("shreya@acme.com", ""), ("info@acme.com", ""), ("sales@acme.com", ""), ("thrive@acme.com", "")])
def test_which_addresses_count_as_hiring(email, kind):
    assert F.hiring_kind(email) == kind


def test_addresses_are_found_however_they_are_written():
    key, addr = 0x2A, "careers@acme.com"
    cf = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in addr)
    page = f"""<a href="mailto:hr@acme.com">Write to HR</a> jobs [at] acme [dot] com
      <span data-cfemail="{cf}">[email protected]</span> <img src="logo@2x.png"> hr&#64;acme.in"""
    assert F.extract_emails(page) >= {"hr@acme.com", "jobs@acme.com", "careers@acme.com", "hr@acme.in"}
    assert not any("png" in e for e in F.extract_emails(page))


@pytest.mark.parametrize("value,ok", [("acme.com", True), ("https://www.acme.com/careers", True), ("http://acme.com", True),
                                      ("not a site", False), ("localhost", False), ("acme.com:8080", False), ("", False)])
def test_website_addresses_are_normalised(value, ok):
    assert bool(F.normalise_site(value)) is ok


def test_private_and_local_addresses_are_never_fetched():
    for url in ("http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/"):
        assert F.fetch(url, None) == (url, None)


SITE = {
    "https://acme.com/": '<a href="/careers">Careers</a> <a href="/about">About</a> <a href="https://other.com/x">x</a> info@acme.com',
    "https://acme.com/careers": 'Send your CV to <a href="mailto:careers@acme.com">careers</a> or hr@acme.com',
    "https://acme.com/about": "Partners: hr@agency.example.net. Sales: sales@acme.com",
    "https://acme.com/robots.txt": "User-agent: *\nDisallow: /private",
}


@pytest.fixture
def fake_web(monkeypatch):
    visited = []

    def fetch(url, session, max_bytes=F.PAGE_BYTES):
        visited.append(url)
        return url, SITE.get(url)
    monkeypatch.setattr(F, "fetch", fetch)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    return visited


def test_scan_finds_hiring_addresses_on_the_same_site_only(fake_web):
    res = F.scan_site("https://acme.com/", __import__("threading").Event())
    assert [e["email"] for e in res["emails"]] == ["hr@acme.com", "careers@acme.com", "hr@agency.example.net"]
    assert res["emails"][-1]["same_domain"] is False and res["other"] == 2  # info@ and sales@ are left out
    assert not any("other.com" in u for u in fake_web)


def test_run_then_save_to_contacts(user, fake_web):
    c = user["client"]
    r = c.post("/api/finder/scan", json={"items": [{"company": "Acme", "website": "acme.com"}, {"website": "ACME.com"}, {"website": "bad site"}]}, headers=H)
    assert r.status_code == 200 and r.get_json()["total"] == 1
    assert c.get("/api/finder").get_json()["state"] == "running"
    run_agents()                                          # the Email Finder agent reads the sites
    st = c.get("/api/finder").get_json()
    assert st["state"] == "done" and len(st["results"][0]["emails"]) == 3
    assert [e.get("best") for e in st["results"][0]["emails"]] == [True, False, False]
    picks = [{"email": e["email"], "company": "Acme", "website": "acme.com"} for e in st["results"][0]["emails"][:2]]
    r = c.post("/api/finder/save", json={"items": picks}, headers=H).get_json()
    assert r["added"] == 2
    assert c.post("/api/finder/save", json={"items": picks}, headers=H).get_json()["duplicates"] == 2
    row = next(x for x in user["ws"].load("recipients", []) if x["email"] == "hr@acme.com")
    assert row["company"] == "Acme" and row["list"] == "Website finder" and row["added_at"]
    assert all(e["saved"] for e in c.get("/api/finder").get_json()["results"][0]["emails"][:2])


def test_csv_upload_reads_company_and_website_columns(user, fake_web):
    data = b"Company Name,Website\nAcme,acme.com\nNorthwind,northwind.example\n"
    r = user["client"].post("/api/finder/scan", data={"file": (io.BytesIO(data), "sites.csv")}, headers=H, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["total"] == 2
    assert user["client"].post("/api/finder/stop", headers=H).status_code == 200
    run_agents()
    assert user["client"].get("/api/finder").get_json()["state"] == "stopped"


def test_website_is_required(user):
    r = user["client"].post("/api/finder/scan", json={"items": [{"company": "Acme", "website": ""}]}, headers=H)
    assert r.status_code == 400 and r.get_json()["field"] == "website"


def test_csv_without_a_header_is_a_plain_list_of_sites(user, fake_web):
    data = b"acme.com\nhttps://northwind.example\n"
    r = user["client"].post("/api/finder/scan", data={"file": (io.BytesIO(data), "sites.csv")}, headers=H, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["total"] == 2
    assert user["client"].post("/api/finder/stop", headers=H).status_code == 200
    run_agents()
    assert user["client"].get("/api/finder").get_json()["state"] == "stopped"


def test_the_agent_can_save_the_best_address_per_company(user, fake_web):
    from infra import runtime
    tid = runtime.start(user["uid"], "email_finder", {"items": [{"company": "Acme", "website": "https://acme.com/"}], "save": True}, mode="on")
    run_agents()
    assert [r["email"] for r in user["ws"].load("recipients", [])] == ["hr@acme.com"]
    assert tasks.get(user["uid"], tid)["result"]["best"][0]["email"] == "hr@acme.com"


def test_shadow_mode_records_and_saves_nothing(user, fake_web):
    from infra import runtime
    tid = runtime.start(user["uid"], "email_finder", {"items": [{"website": "https://acme.com/"}], "save": True}, mode="shadow")
    run_agents()
    assert user["ws"].load("recipients", []) == [] and user["ws"].load("finder", None) is None
    assert len(tasks.get(user["uid"], tid)["result"]["results"][0]["emails"]) == 3
