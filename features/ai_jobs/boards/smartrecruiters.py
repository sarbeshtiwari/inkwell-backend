"""SmartRecruiters company boards."""
from features.ai_jobs.providers import extra_board_jobs

NAME, KIND, REGIONS = "SmartRecruiters", "company", ["global"]


def fetch(slug):
    return extra_board_jobs("smartrecruiters", slug)
