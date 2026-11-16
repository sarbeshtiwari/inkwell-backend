"""Arbeitnow (remote job board)."""
from features.ai_jobs import providers

NAME, KIND, REGIONS = "Arbeitnow", "search", ["de"]


def fetch(role):
    return providers.arbeitnow(role)
