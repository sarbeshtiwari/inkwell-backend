"""Application statuses and the rules that classify an email (applied, interview, offer, rejected...)."""

import re


STATUSES = {  # key: (label, rank in the pipeline, terminal?)
    "applied": ("Applied", 1, False),
    "incomplete": ("Application incomplete", 0, False),
    "in_review": ("In review", 2, False),
    "assessment": ("Assessment", 3, False),
    "shortlisted": ("Shortlisted", 4, False),
    "interview": ("Interview", 5, False),
    "offer": ("Offer", 7, True),
    "rejected": ("Not selected", 6, True),
    "closed": ("Position closed", 6, True),
    "withdrawn": ("Withdrawn", 6, True),
}


ACTIVE = {k for k, v in STATUSES.items() if not v[2]}


# Ordered strongest first. Each: (status, subject pattern, body pattern). Body patterns are strict phrases
# so boilerplate ("only shortlisted candidates will be contacted") doesn't trigger them.
RULES = [
    ("offer",
     r"\boffer letter\b|\boffer (?:id|of employment)\b|offer\b.*\baccepted\b|\byour .{0,30}offer\b|\bdigital offer\b",
     r"pleased to (?:offer|extend)|offer of employment|attached (?:is )?(?:your|the) offer letter|"
     r"congratulations[^.\n]{0,80}\b(?:selected|offer)\b"),
    ("rejected",
     r"\bregret\b|\bunsuccessful\b|\bnot (?:been )?selected\b|\bnot shortlisted\b",
     r"regret to inform|(?:decided|chosen|elected) (?:not )?to (?:move|proceed|go) (?:forward|ahead)? ?with (?:other|another)|"
     r"(?:pursue|progress|proceed with|consider) other candidates|other candidates (?:whose|who)|"
     r"not (?:to )?(?:pursue|proceed with|progress|move forward with) your (?:candidacy|application|profile)|"
     r"(?:will|would) not be (?:moving|proceeding|progressing) (?:forward )?with your|"
     r"(?:have|has) not been (?:selected|shortlisted|successful)|(?:were|was|is) (?:not )?unsuccessful (?:on this|at this|in)|"
     r"no longer (?:under|being) consider|unable to (?:offer you|move forward|progress your)|not (?:been )?selected for|"
     r"your application (?:was|has been) (?:declined|rejected)|not a match for (?:this|the) (?:role|position)"),
    ("closed",
     r"\b(?:role|position|opening|requisition|job)\b[^|]{0,40}?\b(?:has been |is |was )?(?:filled|closed|cancel+ed)\b",
     r"(?:role|position|opening|requisition|vacancy|job)[^.\n]{0,50}?(?:has|have) (?:now )?been (?:filled|closed|cancel+ed|put on hold)|"
     r"(?:role|position|opening) is no longer (?:open|available)|no longer accepting applications"),
    ("withdrawn",
     r"\bwithdra(?:wn|wal)\b",
     r"(?:your )?application (?:has been|was) withdrawn|you (?:have )?withdrawn"),
    ("incomplete",
     r"\bincomplete (?:application|skills test)\b|\bfinish your application\b|\bcomplete your application\b",
     r"did not (?:finish|complete) your .{0,30}application|application is (?:still )?incomplete|"
     r"haven'?t (?:finished|completed) your application"),
    ("interview",
     r"\binterview\b(?!.*\btrend\b)|^invitation:.*@|\b(?:meet|meeting|call) (?:with|scheduled)\b",
     r"(?:invite|invited|invitation|like to (?:schedule|invite)|scheduled?|shortlisted) (?:you )?(?:for|to) (?:an? |the |your )?"
     r"(?:\w+ )?(?:round of )?(?:interview|discussion|conversation|call)|your interview (?:is|has been) (?:scheduled|confirmed|booked)|"
     r"interview (?:details|schedule|confirmation|slot|invite)"),
    ("assessment",
     r"\bassessment\b|\bskills? test\b|\bonline test\b|\bcoding (?:test|challenge|round)\b|\baptitude\b|\btest details\b|"
     r"\bhackerrank\b|\bcodility\b|\bhackerearth\b|\bmettl\b|\bshl\b|\btestgorilla\b|\bhirepro\b",
     r"(?:appear|complete|take|attempt) (?:for |in )?(?:the |an? |your )?(?:online |skill |coding |technical )?"
     r"(?:assessment|test|challenge)|(?:assessment|test) link|hck\.re/|hackerrank\.com/test|app\.codility|mettl\.com"),
    ("shortlisted",
     r"\bshortlist(?:ed)?\b|\bnext steps?\b|\bmoving forward\b|\bselected for\b",
     r"(?:your )?(?:profile|application|resume|cv) (?:has been|was|is) (?:shortlisted|selected|moved forward)|"
     r"move (?:you )?(?:forward|ahead) (?:to|in) the (?:next|following)|pleased to inform you that you have been shortlisted|"
     r"would (?:like|love) to (?:speak|talk|connect|chat|discuss) (?:with you|further)|next (?:step|round) (?:in|of) (?:the|our) (?:process|hiring)"),
    ("in_review",
     r"\bstatus of your (?:job )?application has (?:changed|been updated)\b|\bapplication (?:is )?(?:under|in) review\b|"
     r"\bviewed your application\b|\bfollow your application\b|\bapplication status\b|\bupdate on your .{0,40}application\b|"
     r"\bupdate (?:for you )?(?:from|on)\b.*\bapplication\b",
     r"(?:is|are) (?:currently )?(?:under|being) review|(?:is|are) reviewing your (?:application|profile)|"
     r"recruiter activity on your|recruiter (?:viewed|has viewed)|application (?:is )?in process"),
    ("applied",
     r"\bthank(?:s| you)?\b.{0,20}\b(?:applying|application|interest|apply)\b|\bapplication (?:has been )?(?:received|submitted|sent)\b|"
     r"\bsuccessfully (?:submitted|applied)\b|\breceived your application\b|\byou applied for\b|\byour application (?:for|to|at|with|was sent)\b|"
     r"\bjourney at\b.*\bbegins\b|\byour candidature\b|\bapplied (?:to|for|successfully)\b|\bapplication confirmation\b",
     r"thank(?:s| you) for (?:applying|your application|submitting|your interest in)|"
     r"(?:we'?ve|we have) (?:successfully )?received your application|application (?:has been|was) (?:successfully )?(?:received|submitted)|"
     r"received your application for"),
]


RULES = [(s, re.compile(sp, re.I), re.compile(bp_, re.I)) for s, sp, bp_ in RULES]


STRONG_BODY = {"offer", "rejected", "closed", "withdrawn", "incomplete"}


CONDITIONAL = re.compile(r"\b(?:if|even if|in case|should|whether|unless|once|when|until)\b")


NEGATED = re.compile(r"\b(?:not|no|never|nor)\b(?:\s+\S+){0,4}\s*$")


POSITIVE = {"offer", "interview", "assessment", "shortlisted"}


def guarded(rx, text, status):
    """First match that isn't hypothetical ("If you are not selected…") or negated ("doesn't create an offer")."""
    for m in rx.finditer(text):
        pre = text[max(0, m.start() - 160):m.start()].lower()
        clause = re.split(r"[.!?\n;]\s", pre)[-1]
        if CONDITIONAL.search(clause) or (status in POSITIVE and NEGATED.search(clause)):
            continue
        return m
    return None


def classify(subject, body):
    """(status, matched phrase) for one email, or (None, '') if it isn't about an application."""
    s_hit = next(((st, m.group(0)) for st, sp, _ in RULES if (m := guarded(sp, subject, st))), None)
    b_hit = next(((st, m.group(0)) for st, _, bpat in RULES if (m := guarded(bpat, body, st))), None)
    if b_hit and b_hit[0] in STRONG_BODY:  # a rejection hidden behind "An update on your application"
        return b_hit
    if s_hit and s_hit[0] == "in_review" and b_hit and STATUSES[b_hit[0]][1] > STATUSES["in_review"][1]:
        return b_hit  # generic "status update" subject, specific body
    return s_hit or b_hit or (None, "")
