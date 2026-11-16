"""Remotive (remote job board)."""
from features.ai_jobs import providers

NAME, KIND, REGIONS = "Remotive", "search", ["global"]


def fetch(role):
    return providers.remotive(role)
