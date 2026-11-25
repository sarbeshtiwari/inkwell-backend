"""Companies near me: the companies around a place (the user's location, if they allow it, or a city and area they
type), on a map, with what Reachout can find about each: kind of company, its careers page, open jobs on its job
board, and the hiring email it publishes on its own website.

Places come from OpenStreetMap (Photon for nearby offices, Nominatim for addresses and the website/email tags
companies publish there). Checking a company reuses Reachout's own readers: the job boards (ai_jobs), any careers
website (sitejobs) and the email finder (finder). Only public business information; nothing about people.
The user's position is rounded to about 100 m and kept only as the last area they searched."""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests
from flask import Blueprint, jsonify

import bridge
from bridge import login_required

bp = Blueprint("nearby", __name__)

UA = {"User-Agent": "ReachoutCompanyMap/1.0 (+https://reachout-web.netlify.app)"}
PHOTON = "https://photon.komoot.io"
NOMINATIM = "https://nominatim.openstreetmap.org"
TAGS = ("office", "building:office", "building:commercial", "amenity:coworking_space", "landuse:commercial")
TYPES = {"it": "IT & software", "company": "Company", "consulting": "Consulting", "financial": "Finance", "insurance": "Insurance",
         "coworking": "Coworking", "coworking_space": "Coworking", "telecommunication": "Telecom", "logistics": "Logistics",
         "estate_agent": "Real estate", "advertising_agency": "Marketing", "lawyer": "Legal", "accountant": "Accounting",
         "employment_agency": "Recruitment", "government": "Government", "ngo": "Non-profit", "research": "Research",
         "educational_institution": "Education", "architect": "Architecture", "office": "Office building",
         "commercial": "Office building"}
MAX_RADIUS_KM = 5
CHECK_PER_RUN = 40
CHECKS = {}                       # uid -> {"state", "total", "done", "current"}
_NOMINATIM_LOCK = threading.Lock()
_last_nominatim = [0.0]


def C():
    return bridge.C


def nominatim(path, **params):
    """Nominatim allows about one request a second; calls are spaced out across the whole app."""
    with _NOMINATIM_LOCK:
        wait = 1.1 - (time.time() - _last_nominatim[0])
        if wait > 0:
            time.sleep(wait)
        _last_nominatim[0] = time.time()
        try:
            r = requests.get(NOMINATIM + path, params={"format": "jsonv2", **params}, headers=UA, timeout=15)
            return r.json() if r.ok else None
        except (requests.RequestException, ValueError):
            return None


def place_label(addr, fallback=""):
    a = addr or {}
    parts = [a.get("suburb") or a.get("neighbourhood") or a.get("quarter") or a.get("road"),
             a.get("city") or a.get("town") or a.get("state_district") or a.get("county"), a.get("country")]
    return ", ".join(dict.fromkeys(p for p in parts if p)) or fallback


def locate(p):
    """{lat, lon} (from the browser) or {country, city, area} -> {lat, lon, label, city}."""
    core = C()
    if p.get("lat") is not None and p.get("lon") is not None:
        try:
            lat, lon = round(float(p["lat"]), 3), round(float(p["lon"]), 3)
        except (TypeError, ValueError):
            raise core.Invalid("That location isn't valid.")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise core.Invalid("That location isn't valid.")
        d = nominatim("/reverse", lat=lat, lon=lon, zoom=16, addressdetails=1) or {}
        a = d.get("address") or {}
        return {"lat": lat, "lon": lon, "label": place_label(a, f"{lat}, {lon}"),
                "city": a.get("city") or a.get("town") or a.get("state_district") or "", "country": a.get("country", "")}
    country = core.v_text(p.get("country"), "country", "Country", 60)
    city = core.v_text(p.get("city"), "city", "City", 80)
    area = core.v_text(p.get("area"), "area", "Area", 120)
    if not country or not city:
        raise core.Invalid("Enter your country and city.", "city")
    q = ", ".join(x for x in (area, city, country) if x)
    hits = nominatim("/search", q=q, limit=1, addressdetails=1) or []
    if not hits and area:
        hits = nominatim("/search", q=f"{city}, {country}", limit=1, addressdetails=1) or []
    if not hits:
        raise core.Invalid("Couldn't find that place. Check the spelling, or try a bigger area.", "area" if area else "city")
    h = hits[0]
    return {"lat": round(float(h["lat"]), 3), "lon": round(float(h["lon"]), 3), "label": place_label(h.get("address"), q), "city": city,
            "country": (h.get("address") or {}).get("country") or country}


def site_of(url):
    url = (url or "").strip()
    if not url:
        return ""
    url = url if re.match(r"https?://", url) else "https://" + url
    return url if urlsplit(url).hostname else ""


def find_places(lat, lon, radius_km):
    """Named offices and office buildings around a point, nearest first, with the website/email tags from OSM."""
    found = {}

    def one(tag):
        try:
            r = requests.get(PHOTON + "/reverse", headers=UA, timeout=20,
                             params={"lat": lat, "lon": lon, "radius": radius_km, "limit": 200, "osm_tag": tag})
            return r.json().get("features") or [] if r.ok else []
        except (requests.RequestException, ValueError):
            return []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for feats in pool.map(one, TAGS):
            for f in feats:
                p = f.get("properties") or {}
                name = (p.get("name") or "").strip()
                if not name or not p.get("osm_id") or len(name) > 120:
                    continue
                key = f"{(p.get('osm_type') or 'N')[0]}{p['osm_id']}"
                lo, la = (f.get("geometry") or {}).get("coordinates") or [None, None]
                if la is None:
                    continue
                kind = p.get("osm_value") or p.get("osm_key") or "office"
                found.setdefault(key, {"id": key, "name": name, "type": TYPES.get(kind, kind.replace("_", " ").capitalize()),
                                       "lat": la, "lon": lo, "address": ", ".join(x for x in (p.get("housenumber"), p.get("street"),
                                                                                              p.get("district") or p.get("locality")) if x),
                                       "website": "", "email": ""})
    rows = list(found.values())
    for i in range(0, len(rows), 50):  # OSM tags companies publish: website, email
        batch = rows[i:i + 50]
        info = nominatim("/lookup", osm_ids=",".join(r["id"] for r in batch), extratags=1) or []
        tags = {f"{d.get('osm_type', 'n')[0].upper()}{d.get('osm_id')}": d.get("extratags") or {} for d in info}
        for r in batch:
            t = tags.get(r["id"]) or {}
            r["website"] = site_of(t.get("website") or t.get("contact:website") or t.get("url"))
            r["email"] = role_email((t.get("email") or t.get("contact:email") or "").split(";")[0].strip())
    for r in rows:
        r["distance_m"] = round(_metres(lat, lon, r["lat"], r["lon"]))
    # one pin per company: the same name in several buildings keeps the nearest
    best = {}
    for r in sorted(rows, key=lambda r: r["distance_m"]):
        best.setdefault(re.sub(r"[^a-z0-9]", "", r["name"].lower()), r)
    return list(best.values())


ROLE_WORDS = ("info", "contact", "hello", "hr", "careers", "career", "jobs", "job", "recruit", "talent", "hiring", "people",
              "office", "enquiry", "enquiries", "support", "admin", "team", "mail", "india", "sales", "business")


def role_email(email):
    """A company's own role address (info@, careers@, hr@…) or ""; a person's address (first.last@) is never kept."""
    from features.finder import hiring_kind
    local = email.split("@")[0].lower() if "@" in email else ""
    return email if local and (hiring_kind(email) or any(local.startswith(w) for w in ROLE_WORDS)) else ""


GOOGLE = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_QUERIES = ("companies", "IT company", "software company", "corporate office", "consulting firm", "startup", "bank office")
GOOGLE_FIELDS = ("places.id,places.displayName,places.location,places.formattedAddress,places.shortFormattedAddress,"
                 "places.websiteUri,places.primaryTypeDisplayName,places.businessStatus,nextPageToken")
SKIP_GOOGLE = re.compile(r"restaurant|cafe|coffee|bar|food|store|shop|salon|spa|gym|hotel|hospital|clinic|pharmacy|atm|parking|"
                         r"school|temple|church|mosque|apartment|residential", re.I)


def google_key(ws):
    return (ws.load("ai_keys", {}) or {}).get("google_places", "")


def google_places(key, lat, lon, radius_km):
    """Companies around a point from Google Places (the user's own key): several text searches, up to 3 pages each.
    Returns (rows, error)."""
    rows, error = {}, ""
    for query in GOOGLE_QUERIES:
        token = ""
        for _ in range(3):
            body = {"textQuery": query, "pageSize": 20,
                    "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": radius_km * 1000}}}
            if token:
                body["pageToken"] = token
            try:
                r = requests.post(GOOGLE, json=body, timeout=20,
                                  headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": GOOGLE_FIELDS, "Content-Type": "application/json"})
                d = r.json()
            except (requests.RequestException, ValueError):
                break
            if not r.ok:
                error = ((d.get("error") or {}).get("message") or f"Google said {r.status_code}")[:200]
                return list(rows.values()), error
            for p in d.get("places") or []:
                loc, name = p.get("location") or {}, (p.get("displayName") or {}).get("text", "")
                kind = (p.get("primaryTypeDisplayName") or {}).get("text") or "Company"
                if not name or "latitude" not in loc or p.get("businessStatus") == "CLOSED_PERMANENTLY" or SKIP_GOOGLE.search(kind):
                    continue
                dist = _metres(lat, lon, loc["latitude"], loc["longitude"])
                if dist > radius_km * 1000 * 1.15:
                    continue
                rows.setdefault("G" + p["id"], {"id": "G" + p["id"], "name": name[:120], "type": GOOGLE_TYPES.get(kind, kind),
                                                "lat": loc["latitude"], "lon": loc["longitude"],
                                                "address": p.get("shortFormattedAddress") or p.get("formattedAddress", ""),
                                                "website": site_of(p.get("websiteUri")), "email": "", "source": "google"})
            token = d.get("nextPageToken")
            if not token:
                break
    return list(rows.values()), error


GOOGLE_TYPES = {"Corporate Office": "Company", "Software Company": "IT & software", "IT Services": "IT & software",
                "Consultant": "Consulting", "Bank": "Finance", "Coworking Space": "Coworking", "Insurance Agency": "Insurance",
                "Real Estate Agency": "Real estate", "Employment Agency": "Recruitment", "Marketing Agency": "Marketing"}


def merge(osm, google, lat, lon):
    """OpenStreetMap and Google places together: one row per company (same name nearby = same company)."""
    out = {}
    for r in google + osm:
        r.setdefault("source", "osm")
        r["distance_m"] = round(_metres(lat, lon, r["lat"], r["lon"]))
        key = re.sub(r"[^a-z0-9]", "", r["name"].lower())
        cur = out.get(key)
        if not cur:
            out[key] = r
        else:  # keep the nearest pin, fill in what the other source knows
            cur["website"] = cur["website"] or r["website"]
            cur["email"] = cur["email"] or r["email"]
            if r["distance_m"] < cur["distance_m"]:
                cur.update(lat=r["lat"], lon=r["lon"], distance_m=r["distance_m"], address=cur["address"] or r["address"])
    return sorted(out.values(), key=lambda r: r["distance_m"])


def _metres(a_lat, a_lon, b_lat, b_lon):
    import math
    dlat, dlon = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def hiring_here(ws, city):
    """Companies with openings in this city among the user's AI job matches: {company key: [jobs]}."""
    if not city:
        return {}
    out = {}
    for r in (ws.load("ai_match", {}) or {}).get("results", []):
        if city.lower() in (r.get("location") or "").lower():
            out.setdefault(re.sub(r"[^a-z0-9]", "", (r.get("company") or "").lower()), []).append(
                {"title": r["title"], "url": r.get("url", ""), "score": r.get("score")})
    return out


REMOTE = re.compile(r"\bremote\b|anywhere|work from home|\bwfh\b|distributed", re.I)


def sort_openings(jobs, area):
    """Which openings count for the user: in their city, elsewhere in their country, or remote. Jobs in other
    countries are counted separately and never shown as "hiring"."""
    from features.ai_jobs.boards import COUNTRY_WORDS, countries_for
    area = area if isinstance(area, dict) else {"city": area or ""}
    city = (area.get("city") or "").lower()
    codes = countries_for(area.get("country") or area.get("label") or "") or countries_for(city)
    country_re = re.compile("|".join(COUNTRY_WORDS[c] for c in codes) or r"(?!x)x", re.I)
    here, country, remote, unknown = [], [], [], []
    for j in jobs:
        where = j.get("location") or ""
        if not where.strip():
            unknown.append(j)
        elif city and city in where.lower():
            here.append(j)
        elif country_re.search(where):
            country.append(j)
        elif REMOTE.search(where) or REMOTE.search(j.get("title", "")):
            remote.append(j)
    mine = here + country + remote
    return {"openings": len(mine), "openings_here": len(here), "openings_country": len(country), "openings_remote": len(remote),
            "openings_unknown": len(unknown), "openings_abroad": len(jobs) - len(mine) - len(unknown),
            "country": area.get("country") or "", "jobs": [{"title": j["title"], "url": j.get("url", ""), "location": j.get("location", "")}
                                                           for j in (mine or unknown)[:8]]}


def check_company(row, area):
    """Careers page, open jobs that suit the user (their city, their country, or remote) and published hiring emails."""
    from features.ai_jobs import sitejobs
    from features.ai_jobs.portals import try_boards
    from features.ai_jobs.sources import company_boards
    from features.finder import scan_site
    info = {"careers": "", "board": "", "openings": None, "openings_here": 0, "jobs": [], "emails": [], "checked": time.time(), "note": ""}
    site = row.get("website")
    jobs, careers = [], ""
    root = f"https://{urlsplit(site).hostname}" if site else ""
    tries = [root + p for p in ("/careers", "/jobs", "/careers/jobs")] + [site] if site else \
        [u for g in site_guesses(row["name"]) for u in (g, g.rsplit("/careers", 1)[0] + "/jobs")]
    for url in tries:
        final, page = sitejobs.get_page(url)
        if not page:
            continue
        found = sitejobs.discover(final, rendered=False)
        hit = try_boards(found["boards"]) if found["boards"] else None
        if hit:
            jobs, info["board"] = hit[2], hit[0]
        else:
            jobs = found["jobs"]
        if jobs or CAREERS.search(urlsplit(final).path):
            careers = found["url"] if CAREERS.search(urlsplit(found["url"]).path) or not jobs else final
            site = site or f"https://{urlsplit(final).hostname}"
            break
    if not jobs:  # nothing on its website: the job boards that need only a name
        hit = try_boards(company_boards(row["name"])[:18])
        if hit and _same_company(row["name"], hit[2]):
            jobs, info["board"] = hit[2], hit[0]
            careers = careers or re.sub(r"/[^/]*$", "", jobs[0].get("url", ""))
    info["careers"] = careers
    info.update(sort_openings(jobs, area))
    if not jobs and not careers:
        info["openings"] = None
    if site:
        scan = scan_site(site, threading.Event(), pages_cap=8)
        info["emails"] = [e["email"] for e in scan["emails"]][:4]
        if scan["error"] and not jobs:
            info["note"] = "Its website didn't answer."
    if role_email(row.get("email", "")) and row["email"] not in info["emails"]:
        info["emails"].append(row["email"])
    return info


CAREERS = re.compile(r"career|jobs?\b|join|vacanc|opening|work-with-us|hiring", re.I)


def site_guesses(name):
    from features.ai_jobs.sources import site_guesses as guesses
    return guesses(name)


def _same_company(name, jobs):
    key = re.sub(r"[^a-z0-9]", "", name.lower())[:10]
    return any(key and key in re.sub(r"[^a-z0-9]", "", (j.get("company") or "").lower()) for j in jobs[:5]) or not key


def run_checks(uid, ids):
    ws = C().Workspace(uid)
    st = CHECKS[uid]
    rows = {r["id"]: r for r in (ws.load("nearby", {}) or {}).get("companies", [])}
    area = (ws.load("nearby", {}) or {}).get("area") or {}
    try:
        def one(cid):
            row = rows.get(cid)
            if not row:
                return cid, None
            st["current"] = row["name"]
            try:
                return cid, check_company(row, area)
            except Exception as e:
                print(f"[Reachout] company check skipped ({row['name']}): {e!r}", flush=True)
                return cid, {"checked": time.time(), "note": "Couldn't check this company.", "openings": None, "emails": [], "jobs": [],
                             "careers": "", "board": "", "openings_here": 0}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for cid, info in pool.map(one, ids):
                if info is not None:
                    with ws.lock:
                        cache = ws.load("nearby_info", {}) or {}
                        cache[cid] = info
                        if len(cache) > 1500:
                            cache = dict(sorted(cache.items(), key=lambda kv: -kv[1].get("checked", 0))[:1500])
                        ws.save("nearby_info", cache)
                st["done"] += 1
    finally:
        st["state"], st["current"] = "done", ""


def fresh(info, area):
    """A saved check only counts for the area it was made for: openings are judged against the user's country, so a
    check from another country (or from before openings were filtered by country) shows as not checked."""
    if not info or info.get("openings") is None:
        return info
    if "openings_country" not in info or (info.get("country") or "").lower() != ((area or {}).get("country") or "").lower():
        return None
    return info


def public(ws):
    d = ws.load("nearby", {}) or {}
    info = ws.load("nearby_info", {}) or {}
    hiring = hiring_here(ws, (d.get("area") or {}).get("city", ""))
    companies = []
    for r in d.get("companies", []):
        key = re.sub(r"[^a-z0-9]", "", r["name"].lower())
        companies.append({**r, "info": fresh(info.get(r["id"]), d.get("area")), "matches": hiring.get(key, [])[:5]})
    on_map = {re.sub(r"[^a-z0-9]", "", c["name"].lower()) for c in companies}
    names = {re.sub(r"[^a-z0-9]", "", (r.get("company") or "").lower()): r.get("company") for r in (ws.load("ai_match", {}) or {}).get("results", [])}
    elsewhere = [{"company": names.get(k) or k, "jobs": v[:5]} for k, v in hiring.items() if k and k not in on_map]
    return {"area": d.get("area"), "radius_km": d.get("radius_km", 1), "companies": companies, "searched": d.get("at"),
            "hiring_in_city": sorted(elsewhere, key=lambda x: -len(x["jobs"]))[:30],
            "checking": CHECKS.get(ws.uid, {}), "types": sorted({c["type"] for c in companies}), "google": bool(google_key(ws)),
            "sources": {"osm": sum(c.get("source", "osm") == "osm" for c in companies), "google": sum(c.get("source") == "google" for c in companies)}}


@bp.get("/api/nearby")
@login_required
def status(ws):
    return jsonify(public(ws))


@bp.post("/api/nearby/search")
@login_required
def search(ws):
    """Body: {lat, lon} from the browser, or {country, city, area}; plus radius_km (0.3–5)."""
    core = C()
    p = core.body()
    if core.rate_limited(("nearby-search", ws.uid), 40, 3600):
        raise core.Invalid("Too many searches. Try again in a while.", status=429)
    try:
        radius = min(max(float(p.get("radius_km") or 1), 0.3), MAX_RADIUS_KM)
    except (TypeError, ValueError):
        radius = 1.0
    area = locate(p)
    companies, note = find_places(area["lat"], area["lon"], radius), ""
    key = google_key(ws)
    if key and p.get("google", True):
        if core.rate_limited(("nearby-google", ws.uid), 60, 86400):
            note = "Google Places skipped: you've used today's 60 Google searches."
        else:
            g, err = google_places(key, area["lat"], area["lon"], radius)
            companies = merge(companies, g, area["lat"], area["lon"])
            note = f"Google Places: {err}" if err else ""
    with ws.lock:
        ws.save("nearby", {"area": area, "radius_km": radius, "companies": companies, "at": core.now_iso()})
    return jsonify({**public(ws), "note": note})


@bp.put("/api/nearby/google-key")
@login_required
def save_google_key(ws):
    """Body: {key} (empty removes it). Stored encrypted with the other API keys; never sent back to the browser."""
    core = C()
    v = str(core.body().get("key") or "").strip()
    if v and not re.fullmatch(r"AIza[\w\-]{30,60}", v):
        raise core.Invalid("That doesn't look like a Google API key (they start with AIza).", "key")
    if v:
        _, err = google_places(v, 28.6139, 77.2090, 0.5)
        if err:
            raise core.Invalid(f"Google didn't accept the key: {err}", "key")
    with ws.lock:
        keys = ws.load("ai_keys", {}) or {}
        keys["google_places"] = v
        ws.save("ai_keys", keys)
    return jsonify(google=bool(v))


@bp.post("/api/nearby/check")
@login_required
def check(ws):
    """Body: {ids}: look up careers page, openings and hiring emails for these companies (in the background)."""
    core = C()
    ids = [str(x) for x in (core.body().get("ids") or [])][:CHECK_PER_RUN]
    known = {r["id"] for r in (ws.load("nearby", {}) or {}).get("companies", [])}
    ids = [i for i in ids if i in known]
    if not ids:
        raise core.Invalid("Pick at least one company to check.")
    if (CHECKS.get(ws.uid) or {}).get("state") == "running":
        raise core.Invalid("Already checking. Wait for it to finish.", status=409)
    for _ in ids:
        if core.rate_limited(("nearby-check", ws.uid), 300, 86400):
            raise core.Invalid("You've checked a lot of companies today. Try again tomorrow.", status=429)
    CHECKS[ws.uid] = {"state": "running", "total": len(ids), "done": 0, "current": ""}
    threading.Thread(target=run_checks, args=(ws.uid, ids), daemon=True, name="nearby-check").start()
    return jsonify(checking=CHECKS[ws.uid])


@bp.post("/api/nearby/track/<cid>")
@login_required
def track(ws, cid):
    """Add a checked company's job board to My companies, so every AI job search reads it."""
    core = C()
    row = next((r for r in (ws.load("nearby", {}) or {}).get("companies", []) if r["id"] == cid), None)
    info = (ws.load("nearby_info", {}) or {}).get(cid) or {}
    if not row or not (info.get("careers") or row.get("website")):
        raise core.Invalid("Check this company first; Reachout needs its careers page.")
    from features.ai_jobs.portals import from_link, try_boards
    link = info.get("careers") or row["website"]
    hit = try_boards(from_link(link)) or (("site", link, [None] * (info.get("openings") or 0)) if info.get("openings") else None)
    if not hit:
        raise core.Invalid("Couldn't find a job board Reachout can read for this company.")
    ats, slug, jobs = hit
    with ws.lock:
        rows = [r for r in (ws.load("ai_portals", []) or []) if (r["ats"], r["slug"]) != (ats, slug)]
        rows.append({"ats": ats, "slug": slug, "name": row["name"][:80], "jobs": len(jobs)})
        ws.save("ai_portals", rows[-60:])
    return jsonify(added={"name": row["name"], "jobs": len(jobs)})
