"""Reorder, never add: the checks a tailored resume must pass before Inkwell uses it.
Adapted from Morrow (MIT, (c) 2026 Morrow contributors): common/resume.py (_lint, ungrounded_terms, _accept_skills,
_accept_bullets, _accept_text), for a resume read from a PDF instead of LaTeX.

The AI may only (1) reorder skills inside a category, (2) reorder the bullets under a role, and (3) rewrite the
summary using words already in the resume. A proposal that adds, drops or changes anything else is rejected and the
original is kept, with a note saying why."""

import re

TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.'/-]*")
PRONOUNS = {"i", "i'm", "i've", "i'd", "i'll"}
BANNED_WORDS = ("passionate", "results-driven", "results driven", "leverage", "leveraging", "synergy", "cutting-edge",
                "cutting edge", "delve", "seamless", "seamlessly", "spearheaded", "dynamic", "innovative",
                "proven track record", "guru", "ninja", "rockstar", "thought leader", "excited", "thrilled", "visionary",
                "world-class", "best-in-class", "game-changing", "hit the ground running", "team player", "fast learner", "great fit")


def norm(value):
    return " ".join(str(value).split()).casefold()


def flat(value):
    """Compare text from a PDF regardless of spacing, bullets and dash styles."""
    return re.sub(r"[^a-z0-9+#%]", "", str(value).casefold().replace("–", "-").replace("—", "-"))


def terms(text):
    return {t.lower().strip(".'") for t in TERM.findall(text)}


def at_sentence_start(text, position):
    before = text[:position].rstrip()
    return not before or before[-1] in ".!?:"


def claim_like(term, sentence_start):
    if any(c.isdigit() for c in term) or any(c in term for c in "+#/") or "." in term.strip("."):
        return True
    return any(c.isupper() for c in term) and not sentence_start


def ungrounded_terms(text, original_terms, known=frozenset()):
    """Technologies, names and numbers in `text` that neither the resume nor `known` mention."""
    missing = []
    for m in TERM.finditer(text):
        term = m.group(0)
        if not claim_like(term, at_sentence_start(text, m.start())):
            continue
        low = term.lower().strip(".'")
        if low in PRONOUNS:
            continue
        cands = {low, low.rstrip("s")} | ({low[:-2]} if low.endswith("'s") else set())
        if not cands & original_terms and not cands & known and term not in missing:
            missing.append(term)
    return missing


def lint(text, label):
    problems = []
    if "—" in text or "–" in text:
        problems.append(f"{label} uses an em or en dash")
    if "!" in text:
        problems.append(f"{label} uses an exclamation mark")
    if any(ord(c) > 0x2600 for c in text):
        problems.append(f"{label} contains a symbol or emoji")
    low = text.lower()
    problems += [f"{label} uses the word {w!r}" for w in BANNED_WORDS if re.search(r"(?<![a-z-])%s(?![a-z-])" % re.escape(w), low)]
    return problems


def accept_skills(base, proposed):
    """base: [(category, [items])]. Keep a category's new order only when it is a pure reordering."""
    notes, out = [], []
    for category, items in base:
        new = proposed.get(category) if isinstance(proposed, dict) else None
        if not isinstance(new, list) or not all(isinstance(i, str) for i in new):
            out.append((category, items))
            continue
        by_key = {norm(i): i for i in items}
        keys = [norm(i) for i in new]
        if sorted(keys) != sorted(by_key) or len(set(keys)) != len(keys):
            notes.append(f"Skills ({category}): the AI added or dropped items, so the original order was kept.")
            out.append((category, items))
            continue
        re_ = [by_key[k] for k in keys]
        if re_ != items:
            notes.append(f"Skills ({category}): now lead with {re_[0]}.")
        out.append((category, re_))
    return out, notes


def accept_bullets(base, headings, proposed):
    """base: one bullet list per role. Keep a role's new bullet order only when it is a permutation of its own bullets."""
    notes, out = [], []
    for i, items in enumerate(base):
        order = proposed[i] if isinstance(proposed, list) and i < len(proposed) else None
        if not isinstance(order, list) or sorted(order) != list(range(len(items))):
            if order is not None and order != list(range(len(items))):
                notes.append(f"Bullets under {headings[i] or 'a role'}: the AI's answer wasn't a reordering, so the original order was kept.")
            out.append(items)
            continue
        re_ = [items[p] for p in order]
        if re_ != items:
            notes.append(f"Bullets under {headings[i] or 'a role'}: now lead with \"{' '.join(re_[0].split()[:8])}…\".")
        out.append(re_)
    return out, notes


def summary_bounds(summary):
    """A rewritten summary may be a little shorter and barely longer than the original."""
    words = len(summary.split())
    return (max(30, words - 20), words + 10) if words else (25, 70)


def accept_text(label, proposed, current, words, original_terms, known=frozenset()):
    if not isinstance(proposed, str) or not proposed.strip():
        return current, []
    text = " ".join(proposed.split())
    if norm(text) == norm(current):
        return current, []
    problems = lint(text, label)
    n = len(text.split())
    if not words[0] <= n <= words[1]:
        problems.append(f"{label} has {n} words; expected {words[0]} to {words[1]}")
    missing = ungrounded_terms(text, original_terms, known)
    if missing:
        problems.append(f"{label} mentions things not in the resume: {', '.join(missing)}")
    if problems:
        return current, [f"{label}: the AI's version was rejected ({'; '.join(problems)}), so the original was kept."]
    return text, [f"{label} rewritten for this job, using only words from your resume."]


def verbatim(text, original_flat):
    """True when `text` appears in the resume (ignoring spacing, bullets and punctuation)."""
    f = flat(text)
    return bool(f) and f in original_flat
