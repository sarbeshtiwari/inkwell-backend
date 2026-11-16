"""Workable company boards (apply.workable.com)."""
from features.ai_jobs.providers import extra_board_jobs

NAME, KIND, REGIONS = "Workable", "company", ["global"]


def fetch(slug):
    return extra_board_jobs("workable", slug)
