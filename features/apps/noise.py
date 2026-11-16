"""Filtering out newsletters and job-board noise; tidying company and role names."""

import contextvars
import re


NOISE_SUBJECT = re.compile(
    r"new jobs? posted|latest .{0,20}jobs|job alert|jobs? (?:for you|you (?:may|might))|recommended (?:jobs|for you)|"
    r"recommendations|webinar|newsletter|leetcode|contest|be first to apply|check out jobs|handpicked|urgently hiring|"
    r"top openings|urgent requirement|unlock your potential|rank higher|interview trend|invited to apply|"
    r"verify your (?:candidate )?(?:account|email)|reset your password|password|activate your|sign[- ]?in code|"
    r"join our talent (?:community|network)|similar jobs|who viewed|profile views|premium|subscription|"
    r"^✉️|walk-?in interview \||^job \||hiring for .{0,40}\|\s|admit card|\bgate[- ]?20\d\d\b|"
    r"interview (?:questions|tips|prep|advice|guide)|mock interview|resume (?:score|review|tips)|what'?s new in your|"
    r"your weekly|daily digest|jobs? matching|people also applied|applied by other|similar (?:roles|openings)|"
    r"\botp\b|verification (?:code|needed)|verify your|email verification|confirm your .{0,30}email|login details|"
    r"candidate portal|one more step|seat booking|ticket .{0,20} solved|saved as a draft|continue to apply|"
    r"let'?s stay in touch|registration procedure|stand out from the crowd|new comment for|about case \d+|"
    r"support id|link not active|unresponsive|request for new|\bconsent to represent\b", re.I)


NOISE_FROM = re.compile(
    r"@(?:[\w-]+\.)*(?:digialm\.com|nic\.in|gov\.in|ibps\.in|leetcode\.com|geeksforgeeks\.org|coursera|udemy|"
    r"unstop\.com|ambitionbox\.com|glassdoor\.|medium\.com|substack\.com|quora\.com|youtube\.com|"
    r"minis\.naukri\.com|jobs2web\.com|facebookmail\.com|accounts\.google\.com)|"
    r"@(?:[\w-]+\.)*(?:ac|edu)(?:\.[a-z]{2})?>?$|jobalerts?|job-?notification|newsletter|marketing|promo", re.I)


# ATS / job-board sender domains: the company is not the sender's domain.
PORTALS = {
    "myworkday.com": "Workday", "workday.com": "Workday", "lever.co": "Lever", "greenhouse-mail.io": "Greenhouse",
    "greenhouse.io": "Greenhouse", "icims.com": "iCIMS", "smartrecruiters.com": "SmartRecruiters",
    "successfactors.com": "SuccessFactors", "successfactors.eu": "SuccessFactors", "brassring.com": "BrassRing",
    "avature.net": "Avature", "ashbyhq.com": "Ashby", "rippling.com": "Rippling", "jobvite.com": "Jobvite",
    "taleo.net": "Taleo", "oraclecloud.com": "Oracle Recruiting", "workablemail.com": "Workable", "workable.com": "Workable",
    "naukri.com": "Naukri", "linkedin.com": "LinkedIn", "indeed.com": "Indeed", "indeedemail.com": "Indeed",
    "njoyn.com": "Njoyn", "testedrecruits.com": "TestedRecruits", "darwinbox.in": "Darwinbox", "darwinbox.com": "Darwinbox",
    "zohorecruit.com": "Zoho Recruit", "zohorecruit.in": "Zoho Recruit", "freshteam.com": "Freshteam", "keka.com": "Keka",
    "kekamail.com": "Keka", "joinsuperset.com": "Superset", "wellfound.com": "Wellfound", "instahyre.com": "Instahyre",
    "cutshort.io": "Cutshort", "foundit.in": "foundit", "internshala.com": "Internshala", "hirist.tech": "Hirist",
    "hackerrank.com": "HackerRank", "hackerearth.com": "HackerEarth", "testgorilla.com": "TestGorilla",
    "talentrecruitmail.com": "Talent Recruit", "recruitee.com": "Recruitee", "teamtailor-mail.com": "Teamtailor",
    "personio.de": "Personio", "bamboohr.com": "BambooHR", "hirepro.in": "HirePro", "mettl.com": "Mercer Mettl",
    "shl.com": "SHL", "apna.co": "apna", "uplers.com": "Uplers", "turing.com": "Turing", "crossinghurdles.com": "Crossing Hurdles",
    "micro1.ai": "micro1",
}


SENDER_WORDS = re.compile(
    r"\b(?:workday|notifications?|notification|recruiting|recruitment|recruiters?|talent acquisition|talent|careers?|"
    r"hiring|hr|team|people services|people|resourcing|human resources|jobs|job|do-?not-?reply|no-?reply|noreply|"
    r"action may be required|global talent acquisition|p&o|india|inc|the|joining)\b|@\s*icims|\bat\b|[-–|:]|\s{2,}", re.I)


PLATFORMS = re.compile(r"\b(?:turbohire|ripplehire|expertia(?: ai)?|talenttitan|superset|codesignal|testgorilla|zwayam|"
                       r"hackerrank|hackerearth|mettl|keka|darwinbox|naukri|linkedin|indeed|internshala|unstop|wellfound|"
                       r"instahyre|cutshort|workday|lever|greenhouse|icims|successfactors|smartrecruiters|brassring|"
                       r"avature|taleo|jobvite|zoho recruit|freshteam|talentrecruit|system|systems administrator)\b", re.I)


ROLE_WORDS = re.compile(r"\b(?:engineer|engg|engr|developer|analyst|intern|internship|trainee|manager|architect|specialist|"
                        r"associate|consultant|designer|tester|sdet|sde|support|executive|representative|programmer|lead|"
                        r"officer|scientist|apprentice|graduate|qa|devops|administrator|process|operations|product|data|"
                        r"front ?end|back ?end|full ?stack|fullstack|technician|staff|customer|voice|analytics|stack|"
                        r"software|security|platform|cloud|mern|react|java|python|flutter|\.net|node|ai|ml|fresher|"
                        r"coordinator|member|contributor|expert|benchmarking|agent|advisor|writer|editor|researcher)\b", re.I)


COMPANY_ROLE = re.compile(r"\b(?:engineers?|developers?|analyst|intern|internship|trainee|manager|architect|specialist|"
                          r"consultant|designer|tester|sdet|sde|executive|representative|programmer|officer|scientist|"
                          r"apprentice|graduate|devops|administrator|technician|fresher|coordinator|contributor|associate)\b", re.I)


NOT_COMPANY = re.compile(r"^(?:action required|reminder|important|invitation|interview|update|status(?: update)?|next steps?|"
                         r"shortlisted|incomplete|assessment|completed?|kind regards|regards|welcome|congratulations|"
                         r"application|thank you|thanks|candidate|confirmation|acknowledgement|campus|india|remote|urgent|"
                         r"hiring|new|re|fw|fwd|reg|hello|hi|dear|sorry|greetings|notice|alert|result|results|offer|"
                         r"bangalore|bengaluru|noida|gurugram|gurgaon|hyderabad|pune|chennai|mumbai|delhi|kolkata|"
                         r"navi mumbai|ncr|usa|uk|us|job|jobs|opportunity|career|careers)\b", re.I)


GENERIC = {"", "central", "unknown sender", "an unknown sender", "workday", "lever", "greenhouse", "icims", "recruiting", "naukri", "linkedin", "indeed", "careers", "talent",
           "hr", "team", "sap successfactors", "successfactors", "info", "mail", "support", "admin", "the company", "us",
           "our company", "your", "we"}


COMPANY_SUFFIX = re.compile(
    r"[\s,]+(?:pvt\.?|private|ltd\.?|limited|llp|llc|inc\.?|corp\.?|corporation|co\.|plc|gmbh|s\.?a\.?|"
    r"technologies|technology|solutions|services|software|systems|consulting|global|group|india|international|associates|"
    r"team|careers|labs|"
    r"& co\.?|and company)\b\.?", re.I)


def norm_company(name):
    n = (name or "").lower().replace("&", " and ")
    prev = None
    while prev != n:
        prev, n = n, COMPANY_SUFFIX.sub("", " " + n).strip(" .,-")
    return re.sub(r"[^a-z0-9]+", "", n)


def norm_role(role):
    r = re.sub(r"\([^)]*\)|\b(?:i{1,3}|iv|[1-4])\b$|[^a-z0-9 ]+", " ", (role or "").lower())
    return re.sub(r"\s+", " ", r).strip()


def tidy(text, n=80):
    t = re.sub(r"[​-‏⁠﻿]", "", text or "")
    t = re.sub(r"\s+", " ", t).strip(" .,:;!-–—|'\"*")
    return t[:n].strip()


# The signed-in person's own name parts, set per scan: their name is never taken for a company.
ME_NAMES = contextvars.ContextVar("me_names", default=())


def my_name_parts(profile):
    return tuple(n for n in re.split(r"\s+", (profile.get("name") or "")) if len(n) >= 3)


def good_company(c):
    c = tidy(re.sub(r"\([^)]*\)?|\s+via\s+.*$|^@|@.*$", " ", c or "", flags=re.I), 60)
    c = re.sub(r"^.*\bfrom\s+", "", c) if re.search(r"\bfrom\s+\S", c) else c
    c = tidy(re.sub(r"\s+(?:team|careers?|recruiting|recruitment|hiring|hr|talent acquisition|people services|support|notifications?|system)$", "", c, flags=re.I), 60)
    if (not c or c.lower() in GENERIC or len(c) < 2 or NOT_COMPANY.match(c) or COMPANY_ROLE.search(c) or PLATFORMS.search(c)
            or re.search(r"\b(?:you|your|our|we|position|role|application|job|id|candidate)\b|\d{4,}|\.(?:com|in|io|ai)$", c, re.I)
            or any(re.search(rf"\b{re.escape(n)}\b", c, re.I) for n in ME_NAMES.get())
            or re.fullmatch(r"(?:do-?not-?reply|no-?reply|noreply|donotreply)\w*", c, re.I)):
        return ""
    if re.fullmatch(r"[a-z0-9]+", c):  # 'barclays' from a tenant name
        c = c.upper() if len(c) <= 3 else c.title()
    return c


def good_role(r, me=()):
    r = tidy(r, 140)
    for n in me:  # "Jane Doe - JR0295478 IT Security Analyst I"
        r = re.sub(rf"\b{re.escape(n)}\b", " ", r, flags=re.I)
    r = re.sub(r"^\W*(?:role|position|job title|designation)\s*[:-]\s*|^(?:job|post|position|role) of\s+|^position\s+", "",
               tidy(r, 140), flags=re.I)
    r = re.sub(r"\s+(?:at|@)\s+[A-Z][\w&. -]*$|^[A-Z][\w&.]*\s*\|\s*|\s+-\s+application form$", "", r)
    r = re.sub(r"^(?:the|a|an|our)\s+|\b(?:role|position|opening|opportunity|job|post)\b\s*$", "", tidy(r, 140), flags=re.I)
    r = re.sub(r"^[a-z][a-z ]{0,20}\s+-\s+", "", tidy(r, 140))  # "completion - 36556 - Software Developer"
    r = tidy(re.sub(r"^(?:[-–—]\s*)?(?:R|JR|REQ|R-)?[-_]?\d{4,}[\w-]*\s*[-–—]?\s*", "", r), 90)
    words = r.split()
    if (not r or len(words) > 12 or len(r) < 3 or not ROLE_WORDS.search(r) or "#" in r
            or re.search(r"\b(?:you|your|we|our|us|thank|thanks|application|interest|applying|apply|interview|link|verification|"
                         r"represent|started|track|status|career|update|received|submitted)\b|[;{}]|background-", r, re.I)):
        return ""
    return r
