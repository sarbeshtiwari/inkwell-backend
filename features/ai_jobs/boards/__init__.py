"""Job board readers, after career-ops' providers/: one module per public, no-login job source.

Every module that doesn't start with "_" is loaded and must define:

    NAME    = "BambooHR"                 shown to the user
    KIND    = "company" | "search"       company: reads one company's board by its slug; search: finds jobs for a role
    REGIONS = ["global"] | ["in", "de"]  where the board's jobs are (country codes); "global" boards always run
    def fetch(arg) -> list[dict]         arg = the company slug (company) or the role (search); never raises badly

Each job: {"id": "<prefix>:<unique>", "ats": "", "board": <slug or board id>, "title", "company", "location",
"url", "text" (plain description, "" when the list doesn't carry it), "posted" ("YYYY-MM-DD" or ""),
"salary" (optional text), "via" (board name)}. Only Greenhouse/Lever/Ashby set "ats" (those can be auto-applied)."""

import importlib
import pkgutil
import re
from concurrent.futures import ThreadPoolExecutor

BOARDS = {}  # module name -> module


def load():
    if BOARDS:
        return BOARDS
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{m.name}")
        except Exception as e:  # a broken reader never takes the others down
            print(f"[Reachout] job board {m.name} not loaded: {e!r}", flush=True)
            continue
        if callable(getattr(mod, "fetch", None)) and getattr(mod, "KIND", "") in ("company", "search"):
            BOARDS[m.name] = mod
    return BOARDS


COUNTRY_WORDS = {"in": "india|bengaluru|bangalore|delhi|gurugram|gurgaon|noida|mumbai|pune|hyderabad|chennai|kolkata",
                 "us": "united states|usa|\\bus\\b|new york|san francisco|seattle|austin|boston|california",
                 "gb": "united kingdom|\\buk\\b|london|manchester|england|scotland", "de": "germany|deutschland|berlin|munich|münchen|hamburg",
                 "fr": "france|paris|lyon", "es": "spain|españa|madrid|barcelona", "pl": "poland|polska|warsaw|krakow|kraków|wroclaw",
                 "nl": "netherlands|amsterdam|rotterdam", "be": "belgium|brussels|antwerp", "se": "sweden|stockholm", "cz": "czech|prague|praha|brno",
                 "ca": "canada|toronto|vancouver|montreal", "sg": "singapore", "vn": "vietnam|ho chi minh|hanoi", "id": "indonesia|jakarta",
                 "ph": "philippines|manila", "my": "malaysia|kuala lumpur", "br": "brazil|brasil|são paulo|sao paulo", "mx": "mexico|méxico",
                 "ar": "argentina|buenos aires", "co": "colombia|bogotá|bogota", "cl": "chile|santiago", "pt": "portugal|lisbon|lisboa|porto",
                 "dk": "denmark|copenhagen|københavn|aarhus", "no": "norway|oslo|bergen", "fi": "finland|helsinki|espoo", "it": "italy|milan|rome", "au": "australia|sydney|melbourne",
                 "jp": "japan|tokyo", "cn": "china|beijing|shanghai|shenzhen", "tw": "taiwan|taipei", "ae": "uae|dubai|abu dhabi", "sn": "senegal|dakar", "ch": "switzerland|zurich|zürich|geneva|basel",
                 "at": "austria|vienna|wien", "ie": "ireland|dublin", "nz": "new zealand|auckland", "za": "south africa|johannesburg|cape town",
                 "hk": "hong kong", "kr": "korea|seoul", "pe": "peru|lima", "ec": "ecuador|quito", "uy": "uruguay|montevideo",
                 "th": "thailand|bangkok", "ng": "nigeria|lagos", "ke": "kenya|nairobi", "eg": "egypt|cairo", "il": "israel|tel aviv"}


def countries_for(location):
    loc = (location or "").lower()
    return {c for c, pat in COUNTRY_WORDS.items() if re.search(pat, loc)}


def search_boards(location=""):
    """Search boards worth asking for this location: global ones, plus regional ones for the user's country."""
    want = countries_for(location)
    return {k: m for k, m in load().items() if m.KIND == "search" and ("global" in m.REGIONS or want & set(m.REGIONS))}


def company_boards():
    return {k: m for k, m in load().items() if m.KIND == "company"}


def call(mod, arg, countries=()):
    """mod.fetch(arg), passing the user's countries to search boards that serve several (fetch(role, countries))."""
    import inspect
    if countries and len(inspect.signature(mod.fetch).parameters) > 1:
        return mod.fetch(arg, sorted(countries & set(mod.REGIONS)))
    return mod.fetch(arg)


def run_all(calls, workers=12, location=""):
    """[(module, arg)] side by side; a board that fails or answers nothing is skipped. Returns (jobs, errors)."""
    countries = countries_for(location)

    def one(c):
        mod, arg = c
        try:
            return [j for j in (call(mod, arg, countries) or []) if j.get("url") and j.get("title")], None
        except Exception as e:
            return [], f"{getattr(mod, 'NAME', mod.__name__)}: {str(e)[:80]}"
    jobs, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for js, err in pool.map(one, calls):
            jobs += js
            if err:
                errors.append(err)
    return jobs, errors
