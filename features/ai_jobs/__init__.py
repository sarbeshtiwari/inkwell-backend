"""Inkwell AI job match: read the user's resume, search for openings, rank them, and (when allowed) apply.

The pipeline is a LangChain runnable chain:

    profile (resume + what they want) -> search (LangChain tools) -> rank (Inkwell's own engine)
                                      -> optional review by a local open-source model (Ollama)

* Matching runs on our own engine, on our server, with no outside AI service: skills found in the resume vs.
  the job, role family, years of experience asked for, and how closely the resume's wording matches the
  description (TF-IDF cosine). When an Ollama server is reachable (OLLAMA_URL), a local open-source model
  also reads the top matches and writes a short verdict; nothing leaves your own machines either way.
* Search sources, picked by the user: company career sites (public Greenhouse, Lever and Ashby job boards,
  no key needed), Tavily and Brave (each user adds their own key, stored encrypted).
* Auto-apply fills public application forms on Greenhouse, Lever and Ashby and on companies' own careers
  sites (siteapply), only for jobs the user approved. It never logs in anywhere (sites that need an account
  are left to Watch mode, where the user signs in), never guesses answers to questions it can't fill from the
  user's own details, and stops at any CAPTCHA, handing the job back to the user.

Modules: common (Shared pieces), sources (Search sources), ranking (Matching engine), runs (Match runs), answers (My answers), forms (Application forms), apply (Auto-apply helpers), watch (Watch mode), routes (Auto-apply API),
providers (more job sources), signals (liveness, keywords, role clarity, legitimacy, rules), evaluate (A–H
report, cover letters), tracker (statuses, rules, follow-ups, insights),
models (choice of AI provider), boards/ (job board readers), portals (My companies), sitejobs (jobs from any careers website), siteapply (applying on company sites), learning (feedback -> rankings), extras (tailored resume,
prep, deadlines, skills to learn, stale applications, weekly digest). Ideas from career-ops (github.com/santifer/career-ops)."""

from .common import (  # noqa: F401
    APPLY_LOCK, APPLY_PER_DAY, C, HANDOVER, J,
    LIVE, Live, MAX_RESULTS, OLLAMA_MODEL, OLLAMA_URL,
    RUN_SECONDS, UA, bp, get_json, plain,
)
from .sources import (  # noqa: F401
    ATS_URL, BOARDS, SEARCH_SITES, board_jobs, company_boards, from_ats_url,
    make_tools, norm_ashby, norm_gh, norm_lever, search_query, web_hits_to_jobs,
)
from .ranking import (  # noqa: F401
    GENERIC_TECH, NON_TECH, STOP, cosine, search_sources,
    ollama_ready, rank, role_fit, tokens, years_asked,
)
from .runs import (  # noqa: F401
    DETAIL_FIELDS, pdf_text, public_state, save_details, save_keys, save_match,
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
    keyword_coverage, legitimacy, liveness, role_clarity, role_summary, rule_hits,
)
from .evaluate import (  # noqa: F401
    evaluate_job, evaluate_many, evaluate_one, get_report, get_rules, make_cover, save_cover,
)
from .tracker import (  # noqa: F401
    STATES, due_followups, insights, set_status,
)
from .models import (  # noqa: F401
    PROVIDERS, chat, get_model, put_model,
)
from .portals import (  # noqa: F401
    add_portal, from_link, list_portals, remove_portal,
)
from .extras import (  # noqa: F401
    close_stale, deadline, get_skills, make_prep, make_resume, weekly_digest_all,
)
from .sitejobs import (  # noqa: F401
    discover, embedded_boards, jobs_in,
)
from .siteapply import (  # noqa: F401
    find_form, known_account_site,
)
from .learning import (  # noqa: F401
    decide_suggestion, get_learning, give_feedback, reset_learning,
)
from .routes import (  # noqa: F401
    apply, apply_kit, finish, live_view, run_apply,
)
from . import agent_tools  # noqa: F401,E402  (registers the agent tools: infra/tools.py)
from . import agents  # noqa: F401,E402  (registers the first-workflow agents: infra/runtime.py)
