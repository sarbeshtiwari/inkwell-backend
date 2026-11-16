"""Built In (tech job board; public search page, job cards read from its HTML)."""
import re

from features.ai_jobs.boards._util import get_text, title_matches, unescape

NAME, KIND, REGIONS = "Built In", "search", ["global", "us"]
MODES = {"remote", "hybrid", "in-office"}


def fetch(role):
    page = get_text("https://builtin.com/jobs", params={"search": role})
    ids = list(dict.fromkeys(re.findall(r'id="job-card-(\d+)"', page)))
    out = []
    for n, jid in enumerate(ids):
        start, end = page.find(f'id="job-card-{jid}"'), page.find(f'id="job-card-{ids[n + 1]}"') if n + 1 < len(ids) else None
        card = page[start:end][:8000]
        segs = [unescape(s) for s in re.split(r"\s*<[^>]+>\s*", card[card.find(">") + 1:]) if s.strip()]
        link = re.search(r'href="(/job/[^"]+/' + jid + ')"', card)
        if len(segs) < 2 or not link or not title_matches(segs[1], role):
            continue
        mode = next((i for i, s in enumerate(segs) if s.lower() in MODES), None)
        where = segs[mode + 1] if mode is not None and mode + 1 < len(segs) else ""
        blurb = next((s for s in segs if len(s) > 120), "")
        out.append({"id": f"bi:{jid}", "ats": "", "board": "builtin", "title": segs[1], "company": segs[0],
                    "location": ", ".join(filter(None, [where, segs[mode] if mode is not None else ""])),
                    "url": "https://builtin.com" + link.group(1), "text": blurb, "posted": "", "via": NAME})
    return out[:100]
