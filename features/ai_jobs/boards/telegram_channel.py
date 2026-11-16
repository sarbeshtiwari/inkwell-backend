"""Public Telegram job channels, read from their t.me/s/<channel> web preview (no login, no account).
Board address: the channel name. Example: remotejobss"""
import re

from features.ai_jobs.boards._util import get_text, iso_day, plain, unescape

NAME, KIND, REGIONS = "Telegram channel", "company", ["global"]
GENERIC = re.compile(r"^(new )?(job|jobs|hiring|we.?re hiring|job (opportunity|opening|alert|post)|vacancy|opening|remote jobs?)\W*$", re.I)
KEYED = re.compile(r"^(company|employer|location|where|salary|type|apply|link)\s*[:\-–]\s*(.+)$", re.I)


def fetch(slug):
    ch = (re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,64})", slug or "") or [None, (slug or "").strip("@/ ")])[1]
    if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", ch):
        return []
    html = get_text(f"https://t.me/s/{ch}")
    out = []
    for chunk in html.split('<div class="tgme_widget_message_wrap')[1:]:
        if "service_message" in chunk:
            continue
        post = re.search(r'data-post="([^"/]+)/(\d+)"', chunk)
        body = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.S)
        if not post or post.group(1).lower() != ch.lower() or not body:
            continue
        lines = [plain(x) for x in re.split(r"<br\s*/?>", body.group(1)) if plain(x)]
        clean = [re.sub(r"^[#\W_]+", "", x).strip() for x in lines]
        clean = [x for x in clean if len(x) > 3 and not re.fullmatch(r"[━─\-=_ ]+", x)]
        title = next((x[:160] for x in clean if not GENERIC.match(x) and not KEYED.match(x)), "")
        company = next((KEYED.match(x).group(2) for x in clean if KEYED.match(x) and KEYED.match(x).group(1).lower() in ("company", "employer")), "")
        where = next((KEYED.match(x).group(2) for x in clean if KEYED.match(x) and KEYED.match(x).group(1).lower() in ("location", "where")), "")
        if not title:
            continue
        links = [unescape(h) for h in re.findall(r'href="(https://[^"]+)"', body.group(1)) if "t.me/" not in h]
        out.append({"id": f"tg:{ch}:{post.group(2)}", "ats": "", "board": ch, "title": title, "company": company[:120], "location": where[:120],
                    "url": links[0] if links else f"https://t.me/{ch}/{post.group(2)}", "text": "\n".join(lines)[:6000],
                    "posted": iso_day((re.search(r'<time datetime="([^"]+)"', chunk) or [None, ""])[1]), "via": f"Telegram @{ch}"})
    return out[::-1]
