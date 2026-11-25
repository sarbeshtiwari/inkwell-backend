"""Hirist (hirist.tech): Indian tech jobs from the public search page. Its list is drawn by scripts, so Reachout
opens the page in its headless browser and reads the job cards (stopping at any bot check, never getting round it).
Find only: you open the job and apply on Hirist yourself. Search URL as in Morrow's hirist_bot (MIT)."""
import re

NAME, KIND, REGIONS = "Hirist", "search", ["in"]
ROLE_WORDS = re.compile(r"\b(developer|engineer|manager|lead|analyst|scientist|architect|consultant|intern|designer|"
                        r"python|java|react|node|devops|sde|tester|qa|head|director|specialist|administrator)\b", re.I)
JOB = re.compile(r"^https://www\.hirist\.tech/j/([a-z0-9-]+)-(\d+)")


def slug(role):
    return re.sub(r"[^a-z0-9]+", "-", (role or "software developer").lower()).strip("-")


def parse(anchors):
    out, seen = [], set()
    for href, text in anchors:
        m = JOB.match(href or "")
        if not m or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        parts = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
        head = parts[0] if parts else m.group(1).replace("-", " ")
        company, _, title = head.partition(" - ")
        if not title or ROLE_WORDS.search(company):  # "Python Web Developer - Web Scraping": all title, no company
            company, title = "", head
        exp = next((p for p in parts if re.search(r"\d+\s*-\s*\d+\s*yrs", p)), "")
        where = next((p for p in parts[1:] if p is not exp and not re.search(r"\byrs\b", p) and "|" not in p), "")
        skills = next((p for p in parts if "|" in p), "")
        out.append({"id": f"hirist:{m.group(2)}", "ats": "", "board": "hirist.tech", "title": title.strip() or head,
                    "company": company.strip(), "location": where or "India", "url": href.split("?")[0],
                    "text": f"Experience: {exp}\nSkills: {skills}", "posted": "", "experience": exp, "via": NAME})
    return out


def fetch(role):
    from features.ai_jobs.sitejobs import render
    _, _, anchors, _ = render(f"https://www.hirist.tech/search/{slug(role)}")
    return parse(anchors)
