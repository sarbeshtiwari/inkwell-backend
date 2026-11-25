"""Settings: environment variables, limits and fixed texts."""

import os
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent  # backend/ (this file is backend/core/config.py)


HERE = BACKEND.parent  # project root: web/, data/ and legacy files live here, next to backend/


WEB = HERE / "web"


DATA = Path(os.environ.get("DATA_DIR", HERE / "data")).resolve()


SQLITE_PATH = DATA / "app.db"  # previous storage; migrated into MongoDB on first start


def env_int(name, default):
    """A whole-number setting. Unset, empty or invalid values fall back to the default (a blank line in a
    hosting dashboard must not stop the server from starting)."""
    try:
        return int(str(os.environ.get(name) or "").strip() or default)
    except ValueError:
        print(f"[Reachout] Ignoring {name}={os.environ.get(name)!r}: not a whole number; using {default}.", flush=True)
        return default


MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")


MONGODB_DB = os.environ.get("MONGODB_DB", "reachout")


# cloudinary://<api key>:<api secret>@<cloud name>, from the Cloudinary dashboard. Empty: files stay in GridFS.
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "").strip()


ALLOW_SIGNUP = os.environ.get("ALLOW_SIGNUP", "1") != "0"


DAILY_LIMIT = env_int("DAILY_LIMIT", 100)        # messages per user per day


MIN_WA_DELAY = env_int("MIN_WA_DELAY", 20)        # seconds, enforced server-side


MAX_BROWSERS = env_int("MAX_BROWSERS", 3)         # concurrent WhatsApp browsers


MAX_CONTACTS = env_int("MAX_CONTACTS", 5000)


MAX_FILES_MB = env_int("MAX_FILES_MB", 25)        # per user


MAX_FILE_MB = 10                                                # per uploaded file


MAX_FILES = 20


ALLOWED_DOCS = {"pdf", "doc", "docx", "odt", "rtf", "txt", "tex", "xls", "xlsx", "csv", "ppt", "pptx",
                "png", "jpg", "jpeg", "webp", "gif"}


HEADLESS = os.environ.get("WA_HEADLESS", "1") != "0"


# WhatsApp sending automates WhatsApp Web, which WhatsApp's terms don't allow and which can get numbers
# banned. Hosted versions can switch it off entirely: WHATSAPP_ENABLED=0.
WHATSAPP_ENABLED = os.environ.get("WHATSAPP_ENABLED", "1") != "0"


NO_SANDBOX = os.environ.get("CHROMIUM_NO_SANDBOX", "0") == "1"


# A saved WhatsApp login is deleted this many hours after it was linked (the user is told, and links again to send).
WA_SESSION_HOURS = env_int("WA_SESSION_HOURS", 2)


USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


LOGGED_IN = "#pane-side, [aria-label='Chat list']"


# Browser caches are rebuilt automatically; leaving them out keeps the stored login small.
WA_SKIP = {"Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache", "ShaderCache",
           "GrShaderCache", "GraphiteDawnCache", "CacheStorage", "component_crx_cache",
           "extensions_crx_cache", "Crashpad", "BrowserMetrics", "BrowserMetrics-spare.pma", "ScriptCache",
           "SingletonLock", "SingletonCookie", "SingletonSocket"}


OTP_TTL = 600


OTP_ATTEMPTS = 5


OTP_COOLDOWN = 60


OTP_FAILS_PER_DAY = 15          # wrong codes per email per day before a 24 h lock


SYSTEM_MAIL_PER_HOUR = env_int("SYSTEM_MAIL_PER_HOUR", 300)  # login/notice emails the server may send


SMTP_HOST = os.environ.get("SMTP_HOST", "")


SMTP_PORT = env_int("SMTP_PORT", 465)


SMTP_USER = os.environ.get("SMTP_USER", "")


SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)


# Production = served to other people. APP_ENV=production is the explicit switch; COOKIE_SECURE=1 still implies it.
# Anything bound to a non-loopback address is treated as production too, so a forgotten flag can't
# silently turn on developer conveniences (codes printed to the log, first account = admin) on a public server.
_HOST = os.environ.get("HOST", "127.0.0.1")


PRODUCTION = (os.environ.get("APP_ENV", "").lower() == "production" or os.environ.get("COOKIE_SECURE", "0") == "1"
              or _HOST not in ("127.0.0.1", "localhost", "::1"))


SECURE_COOKIES = os.environ.get("COOKIE_SECURE", "1" if PRODUCTION else "0") == "1"


SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")


# Split hosting (landing on one Netlify site, app on another, this API on Render): the landing page's address.
LANDING_URL = os.environ.get("LANDING_URL", "").rstrip("/")


# Shared secret of Netlify's signed proxy rewrites. When set, every request (except /healthz) must come
# through Netlify: calls straight to this server are refused, and the visitor's real address is taken
# from Netlify's signed request.
NETLIFY_PROXY_SECRET = os.environ.get("NETLIFY_PROXY_SECRET", "")


# Version of the terms/privacy policy a new account accepts (recorded with the account). Bump when they change.
TERMS_VERSION = "2026-10-01"


# Shown on the privacy policy and terms pages.
OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "the Reachout team")


JURISDICTION = os.environ.get("JURISDICTION", "India")  # public address, e.g. https://reachout.example.com


DEV_OTP = not SMTP_HOST and not PRODUCTION  # print codes to the terminal instead of emailing


# Site owners who can see website enquiries (Leads). Locally, with none set, the oldest account is the owner.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


CORE_FIELDS = ["name", "company", "phone", "email"]


STATUS_FIELDS = ["wa_status", "wa_last", "email_status", "email_last", "email_opened", "replied_at", "reply_intent", "reply_count", "added_at"]


CRM_FIELDS = ["stage", "follow_up"]


STAGES = {"new": "New", "contacted": "Contacted", "replied": "Replied", "interested": "Interested",
          "not_interested": "Not interested", "won": "Won"}


LEAD_STATUSES = {"new": "New", "contacted": "Contacted", "qualified": "Qualified", "closed": "Closed", "spam": "Spam"}


LEAD_TOPICS = ["General question", "Sales & pricing", "Support", "Partnership", "Other"]


HEADER_ALIASES = {
    "phone": {"phone", "mobile", "mobile no", "mobile number", "phone number", "phone no", "contact",
              "contact number", "contact no", "whatsapp", "whatsapp number", "whatsapp no", "number", "cell"},
    "email": {"email", "e mail", "mail", "email id", "email address", "mail id"},
    "name": {"name", "hr name", "contact name", "person", "full name", "recipient", "recipient name"},
    "company": {"company", "company name", "organisation", "organization", "org", "firm", "employer"},
}


DEFAULT_SETTINGS = {
    "country_code": "91",
    "min_delay": 45,
    "max_delay": 120,
    "limit": 20,
    "mode": "whatsapp",
    "template_id": "",
    "documents": None,  # None = attach every file until the user picks
    "wa_connected": False,
}


STARTER_TEMPLATE = """Hi {name},

I hope you're doing well. I'm {sender_name}, and I'm reaching out to <say why you're contacting {company}>.

<one or two lines about you or your offer>

I've attached <what you've attached> for your reference. I'd be glad to hear from you whenever it suits.

Thank you,
{sender_name}
{sender_email}"""


IMPORT_MAX_COLS = 40
COLUMN_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
