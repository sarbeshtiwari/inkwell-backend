"""NEOGOV agency boards on governmentjobs.com (slug "agency" or "agency/folder"; "schooljobs:agency" for schooljobs.com). Example: seattle"""
import re
from urllib.parse import urljoin

from features.ai_jobs.boards._util import get_text, unescape

NAME, KIND, REGIONS = "NEOGOV", "company", ["us"]

SEG = r"[a-z0-9][a-z0-9_-]{0,63}"


def _text(s):
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")))


def fetch(slug):
    site, _, rest = (slug or "").lower().rpartition(":")
    agency, _, folder = rest.partition("/")
    if site not in ("", "schooljobs") or not re.fullmatch(SEG, agency) or (folder and not re.fullmatch(SEG, folder)):
        return []
    origin = "https://www.schooljobs.com" if site else "https://www.governmentjobs.com"
    out, seen = [], set()
    for page in range(1, 6):  # 10 per page, newest first
        params = {"agency": agency, "sort": "PostingDate", "isDescendingSort": "true", "page": page, **({"departmentFolder": folder} if folder else {})}
        html = get_text(f"{origin}/careers/home/index", params=params, allow_redirects=False, headers={"X-Requested-With": "XMLHttpRequest"})
        fresh = 0
        for item in html.split('<li class="list-item"')[1:]:
            a = re.search(r'<a [^>]*class="item-details-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
            jid = re.search(r'data-job-id="(\d+)"', item)
            if not (a and jid) or jid.group(1) in seen:
                continue
            seen.add(jid.group(1))
            fresh += 1
            meta = re.findall(r"<li>(.*?)</li>", (re.search(r'<ul class="list-meta">(.*?)</ul>', item, re.S) or [None, ""])[1], re.S)
            dept = next((_text(m)[12:] for m in meta if "Department:" in m), "")
            pay = re.search(r"\$[\d,.]+(?:\s*-\s*\$[\d,.]+)?\s*\w*", _text(meta[1]) if len(meta) > 1 else "")
            out.append({"id": f"neo:{slug}:{jid.group(1)}", "ats": "", "board": slug, "title": _text(a.group(2)),
                        "company": dept or agency.capitalize(), "location": _text(meta[0]) if meta else "",
                        "url": urljoin(origin, unescape(a.group(1))), "text": "", "posted": "",
                        "salary": pay.group(0) if pay else "", "via": NAME})
        if not fresh:
            break
    return out
