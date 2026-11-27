"""Inkwell AI job match: read the user's resume, search for openings, rank them, and (when allowed) apply.

The work is done by agents (agents.py): the Orchestrator delegates to the Job Scout (search) and the Job Analyst
(rank with Inkwell's own engine, then a second opinion from the Super Admin's model when one is set).

* Matching runs on our own engine: skills found in the resume vs. the job, role family, years of experience asked
  for, and how closely the resume's wording matches the description (TF-IDF cosine).
* Search sources, picked by the user: company career sites (public Greenhouse, Lever and Ashby job boards) and
  public job boards. No keys needed.
* Auto-apply fills public application forms on Greenhouse, Lever and Ashby and on companies' own careers
  sites (siteapply), only for jobs the user approved. It never logs in anywhere (sites that need an account
  are left to Watch mode, where the user signs in), never guesses answers to questions it can't fill from the
  user's own details, and stops at any CAPTCHA, handing the job back to the user.

Modules: common (Shared pieces), sources (Search sources), ranking (Matching engine), runs (Match runs), answers (My answers), forms (Application forms), apply (Auto-apply helpers), watch (Watch mode), routes (Auto-apply API),
providers (more job sources), signals (liveness, keywords, role clarity, legitimacy), evaluate (A–H
report, cover letters), tracker (statuses, follow-ups),
models (the platform AI model), boards/ (job board readers), portals (job-board recognition), sitejobs (jobs from any careers website), siteapply (applying on company sites), extras (tailored resume,
prep, deadlines, stale applications, weekly digest). Ideas from career-ops (github.com/santifer/career-ops)."""

from .common import (  # noqa: F401
    APPLY_LOCK, APPLY_PER_DAY, C, HANDOVER, J,
    LIVE, Live, MAX_RESULTS,
    RUN_SECONDS, UA, bp, get_json, plain,
)
from .sources import (  # noqa: F401
    BOARDS, board_jobs, company_boards, make_tools, norm_ashby, norm_gh, norm_lever,
)
from .ranking import (  # noqa: F401
    GENERIC_TECH, NON_TECH, STOP, cosine, search_sources,
    rank, role_fit, tokens, years_asked,
)
from .runs import (  # noqa: F401
    DETAIL_FIELDS, pdf_text, public_state, save_details, save_match,
    start, status,
)
from .answers import (  # noqa: F401
    ANS_STOP, add_answer, answer_for, answer_out, approve_all, delete_answer,
    edit_answer, list_answers, norm_q, q_score, same_company, save_answer,
)
from .forms import (  # noqa: F401
    CAPTCHA, COUNTRIES, FIELD_MAP, LABEL_JS, REMAINING_JS, SNAPSHOT_JS,
    TEXT_BOXES, challenge_visible, file_input_label, fill_form, fill_from_bank, fill_pickers,
    learn_from_page, pick, questions_left,
)
from .apply import (  # noqa: F401
    APPLY_HOSTS, BLOCKED, BLOCKED_RESULT, CONFIRMED, COVER_DOC, apply_one,
    apply_url, apply_values, board_key, can_hand_over, cover_file, cover_text,
    is_blocked_board, mark_blocked, open_form, record_application, resume_file, write_cover,
)
from .watch import (  # noqa: F401
    LOGIN_WALL, OVERLAY_JS, Watch, login_wall, run_watch, watch_apply,
)
from .providers import (  # noqa: F401
    REMOTE_BOARDS, extra_board_jobs, remote_board_jobs,
)
from .signals import (  # noqa: F401
    keyword_coverage, legitimacy, liveness, role_clarity, role_summary,
)
from .evaluate import (  # noqa: F401
    evaluate_job, evaluate_many, evaluate_one, get_report, make_cover, save_cover,
)
from .tracker import (  # noqa: F401
    STATES, applied_summary, due_followups, set_status,
)
from .models import (  # noqa: F401
    chat,
)
from .portals import (  # noqa: F401
    from_link,
)
from .extras import (  # noqa: F401
    close_stale, deadline, make_prep, make_resume, weekly_digest_all,
)
from .sitejobs import (  # noqa: F401
    discover, embedded_boards, jobs_in,
)
from .siteapply import (  # noqa: F401
    find_form, known_account_site,
)
from .routes import (  # noqa: F401
    apply, apply_kit, finish, live_view, run_apply,
)
from . import agent_tools  # noqa: F401,E402  (registers the agent tools: infra/tools.py)
from . import agents  # noqa: F401,E402  (registers the first-workflow agents: infra/runtime.py)
