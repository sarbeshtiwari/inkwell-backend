"""Remote OK (remote job board)."""
from features.ai_jobs import providers

NAME, KIND, REGIONS = "Remote OK", "search", ["global"]


def fetch(role):
    return providers.remoteok(role)
