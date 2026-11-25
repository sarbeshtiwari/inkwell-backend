"""Applications tracker: finds the jobs you applied for in your mailbox and follows each one's status.

Sources are the emails companies' career sites send (Workday, Lever, Greenhouse, iCIMS, SuccessFactors, …),
Naukri's application and status emails, LinkedIn's "application sent / viewed" emails, and recruiters
writing to you directly. Each email becomes an event (applied, in review, assessment, shortlisted,
interview, offer, rejected, position closed, …); events are grouped into one application per company +
role, so every application has a current status, the previous one, and its full history.

Mail is read with BODY.PEEK in a read-only folder, so nothing is marked as read. A daily sync (06:00 in
the user's time zone by default, with catch-up if the app wasn't running then) keeps everything current.

Modules: common (Shared pieces), statuses (Application statuses and the rules that classify an email (applied, interview, offer, rejected...)), noise (Filtering out newsletters and job-board noise; tidying company and role names), extract (Reading company, role and job ID out of an email (including Naukri digests)), mailbox (Scanning the mailbox and merging what it finds into applications), sync (The daily sync), routes (Applications API), naukri (Naukri), overview (Dashboard overview across platforms (applications, GitHub, Naukri))."""

from .common import (  # noqa: F401
    C, bp,
)
from .statuses import (  # noqa: F401
    ACTIVE, CONDITIONAL, NEGATED, POSITIVE, RULES, STATUSES,
    STRONG_BODY, classify, guarded,
)
from .noise import (  # noqa: F401
    COMPANY_ROLE, COMPANY_SUFFIX, GENERIC, ME_NAMES, NOISE_FROM, NOISE_SUBJECT,
    NOT_COMPANY, PLATFORMS, PORTALS, ROLE_WORDS, SENDER_WORDS, good_company,
    good_role, my_name_parts, norm_company, norm_role, tidy,
)
from .extract import (  # noqa: F401
    BODY_COMPANY, BODY_ROLE, COMPANYISH, GENERIC_LOCAL, HTML_MAX, JOB_ID,
    JOB_ID2, SUBJ_COMPANY, SUBJ_ROLE, _Text, body_text, calendar_start,
    company_from_domain, company_from_sender, extract, first_match, html_to_text, looks_like_person,
    naukri_events, snippet,
)
from .mailbox import (  # noqa: F401
    ATS_FROM, HISTORY_MAX, SEARCH_TERMS, announce, progress_of, sync_running,
    app_id, fetch_batch, gmail_query, ingest, match, merge_scanned,
    recompute, scan, search_ids,
)
from .sync import (  # noqa: F401
    DEFAULT_SYNC, daily_tick, mail_sync_progress, morning_sync, next_run, start_sync, start_workers,
    sync_all, sync_prefs,
)
from .routes import (  # noqa: F401
    add_app, delete_app, edit_app, get_app, list_apps, mail_sync_now,
    mail_sync_status, progress, public, run_in_background, save_sync_prefs, sync_now,
)
from .naukri import (  # noqa: F401
    NAUKRI_LINKS, naukri_connect, naukri_disconnect, naukri_stats, naukri_status,
)
from .overview import (  # noqa: F401
    GH_CACHE, STATUS_GROUPS, app_stats, buckets, dash_filters, github_summary,
    overview,
)
