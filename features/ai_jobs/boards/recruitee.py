"""Recruitee company boards (<slug>.recruitee.com)."""
from features.ai_jobs.providers import extra_board_jobs

NAME, KIND, REGIONS = "Recruitee", "company", ["global"]


def fetch(slug):
    return extra_board_jobs("recruitee", slug)
