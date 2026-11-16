"""Jobicy (remote job board)."""
from features.ai_jobs import providers

NAME, KIND, REGIONS = "Jobicy", "search", ["global"]


def fetch(role):
    return providers.jobicy(role)
