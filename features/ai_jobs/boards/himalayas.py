"""Himalayas (remote job board)."""
from features.ai_jobs import providers

NAME, KIND, REGIONS = "Himalayas", "search", ["global"]


def fetch(role):
    return providers.himalayas(role)
