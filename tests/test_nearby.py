"""Companies near me: finding places, keeping only role emails, checks and tracking."""

from conftest import H

from features import nearby as N
from infra import operations


def fake_places(lat, lon, radius_km):
    return [{"id": "N1", "name": "Acme Labs", "type": "IT & software", "lat": lat, "lon": lon, "address": "Tower 10", "website": "https://acme.example",
             "email": "", "distance_m": 40},
            {"id": "N2", "name": "Globex", "type": "Company", "lat": lat, "lon": lon, "address": "", "website": "", "email": "", "distance_m": 300}]


def test_search_by_city_and_check(user, monkeypatch):
    c, ws = user["client"], user["ws"]
    monkeypatch.setattr(N, "nominatim", lambda path, **kw: [{"lat": "28.495", "lon": "77.089", "address": {"suburb": "Cyber City", "city": "Gurugram",
                                                                                                         "country": "India"}}])
    monkeypatch.setattr(N, "find_places", fake_places)
    r = c.post("/api/nearby/search", json={"country": "India", "city": "Gurugram", "area": "Cyber Hub", "radius_km": 1}, headers=H).get_json()
    assert r["area"]["label"] == "Cyber City, Gurugram, India" and [x["name"] for x in r["companies"]] == ["Acme Labs", "Globex"]
    assert c.post("/api/nearby/search", json={"country": "India"}, headers=H).status_code == 400
    monkeypatch.setattr(N, "check_company", lambda row, city: {"careers": "https://acme.example/careers", "board": "", "openings": 2, "openings_country": 2, "country": "India",
                                                                 "openings_here": 1, "jobs": [], "emails": ["careers@acme.example"], "checked": 1, "note": ""})
    with operations.start(ws.uid, "nearby", public={"total": 1, "done": 0}) as op:
        N.run_checks(op, ws.uid, ["N1"])
    d = c.get("/api/nearby").get_json()
    assert d["companies"][0]["info"]["openings"] == 2 and d["checking"]["state"] == "done"


def test_only_role_emails_are_kept():
    assert N.role_email("careers@acme.com") == "careers@acme.com"
    assert N.role_email("info@acme.in") == "info@acme.in"
    assert N.role_email("swati.chabbra@in.acme.com") == ""


def test_google_places_merge_and_key(user, monkeypatch):
    c = user["client"]

    class R:
        ok, status_code = True, 200

        def json(self):
            return {"places": [{"id": "g1", "displayName": {"text": "Acme Labs"}, "location": {"latitude": 28.4951, "longitude": 77.0891},
                                "websiteUri": "https://acme.example", "primaryTypeDisplayName": {"text": "Software Company"}},
                               {"id": "g2", "displayName": {"text": "Initech"}, "location": {"latitude": 28.4955, "longitude": 77.0895},
                                "primaryTypeDisplayName": {"text": "Corporate Office"}},
                               {"id": "g3", "displayName": {"text": "Chai Point"}, "location": {"latitude": 28.4955, "longitude": 77.0895},
                                "primaryTypeDisplayName": {"text": "Cafe"}}]}
    monkeypatch.setattr(N.requests, "post", lambda *a, **kw: R())
    rows, err = N.google_places("AIza" + "x" * 35, 28.495, 77.089, 1)
    assert not err and {r["name"] for r in rows} == {"Acme Labs", "Initech"} and rows[0]["type"] == "IT & software"
    merged = N.merge(fake_places(28.495, 77.089, 1), rows, 28.495, 77.089)
    assert sorted(r["name"] for r in merged) == ["Acme Labs", "Globex", "Initech"]
    assert c.put("/api/nearby/google-key", json={"key": "not-a-key"}, headers=H).status_code == 400
    assert c.put("/api/nearby/google-key", json={"key": "AIza" + "x" * 35}, headers=H).get_json() == {"google": True}
    assert c.get("/api/nearby").get_json()["google"] is True


def test_openings_count_only_my_country_or_remote():
    jobs = [{"title": "Engineer", "location": "Gurugram, Haryana"}, {"title": "Engineer", "location": "Bengaluru, India"},
            {"title": "Engineer", "location": "Remote"}, {"title": "Engineer", "location": "London, UK"},
            {"title": "Engineer", "location": "Austin, TX, United States"}, {"title": "Engineer", "location": ""}]
    o = N.sort_openings(jobs, {"city": "Gurugram", "country": "India"})
    assert (o["openings"], o["openings_here"], o["openings_country"], o["openings_remote"], o["openings_abroad"], o["openings_unknown"]) == (3, 1, 1, 1, 2, 1)


def test_old_or_other_country_checks_show_as_unchecked():
    from features.nearby import fresh
    india = {"city": "Gurugram", "country": "India"}
    assert fresh({"openings": 171, "openings_here": 0}, india) is None
    assert fresh({"openings": 3, "openings_country": 3, "country": "Germany"}, india) is None
    ok = {"openings": 3, "openings_country": 3, "country": "India"}
    assert fresh(ok, india) is ok
    assert fresh({"openings": None, "note": "Couldn't check this company."}, india)["openings"] is None
