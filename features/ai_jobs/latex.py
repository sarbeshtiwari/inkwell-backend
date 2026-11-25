"""Tailored resumes typeset with LaTeX (tectonic), as in Morrow (MIT, (c) 2026 Morrow contributors):
common/resume.py (parse_sections, render, compile_pdf, unsafe_tex, pdf_page_count) and common/resume_template.tex.

Two ways in:
- You keep your own resume as LaTeX: upload a .tex in Files (with Morrow's shapes: a subtitle line, a Summary
  paragraph, "{\\bfseries Category:} items" Skills lines, itemize bullets). Reachout reorders it with the same
  reorder-only rules and compiles it; your layout is kept exactly.
- No .tex: Reachout fills Morrow's template from your PDF resume's parts (copied word for word) and compiles that.

If the tailored version runs a page longer than the original, the original summary is put back; if it is still
longer, or anything fails to compile, Reachout falls back to its HTML resume. Only runs where a LaTeX compiler is
installed (brew install tectonic); the .tex is checked for commands that could run programs or read files."""

import os
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path

from features.ai_jobs import tailor_rules as R

SUBTITLE = re.compile(r"(\{\\normalsize\\color\{muted\}\s*)(?P<subtitle>[^\n}]*?)(\s*\}\\\\\[\d+pt\])")
SUMMARY = re.compile(r"(\\section\{Summary\}[ \t]*\n)(?P<summary>.+?)(\n[ \t]*\n)", re.S)
SKILLS_BLOCK = re.compile(r"(\\section\{Skills\}[ \t]*\n)(?P<block>.+?)(\n[ \t]*\n)", re.S)
SKILL_LINE = re.compile(r"^\{\\bfseries (?P<category>[^}]+?):\}[ \t]*(?P<items>.*?)(?P<tail>\\par\\vspace\{\d+pt\})?[ \t]*$")
ITEMIZE = re.compile(r"(\\begin\{itemize\}[^\n]*\n)(?P<body>.*?)(\n[ \t]*\\end\{itemize\})", re.S)
UNSAFE_TEX = re.compile(r"\\(write18|ShellEscape|directlua|input|include|InputIfFileExists|openin|openout|read|immediate|csname)(?![A-Za-z])")
PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?!s)")
STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
TEMPLATE = Path(__file__).with_name("resume_template.tex")
MAX_PDF = 2 * 1024 * 1024


class LatexError(RuntimeError):
    pass


def compiler():
    """(name, path) of the first LaTeX compiler installed (TEX_COMPILER picks one), or None."""
    pref = os.environ.get("TEX_COMPILER", "").strip()
    for name in [pref] if pref else ["tectonic", "pdflatex", "xelatex", "lualatex"]:
        found = shutil.which(name)
        if found:
            return name, found
    return None


def available():
    return compiler() is not None


def unsafe_tex(tex):
    m = UNSAFE_TEX.search(tex)
    return m.group(0) if m else None


def escape(text):
    text = str(text).replace("\u2019", "'").replace("\u2018", "`").replace("\u201c", "``").replace("\u201d", "''")
    text = text.replace("\\", r"\textbackslash{}")
    text = re.sub(r"([&%#_$])", r"\\\1", text)
    text = text.replace("{", r"\{").replace("}", r"\}").replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}")
    return text.replace("–", "--").replace("—", "---")


def plain(latex):
    text = re.sub(r"^\s*\\item\s*", "", latex)
    text = text.replace("$\\cdot$", "-")
    text = re.sub(r"\\([&%#_$])", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text).replace("{", " ").replace("}", " ")
    return " ".join(text.split())


def split_items(text):
    items, cur, depth = [], [], 0
    for ch in text:
        depth += 1 if ch == "(" else -1 if ch == ")" and depth else 0
        if ch == "," and depth == 0:
            items.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur).strip())
    return [i for i in items if i]


def bullet_items(body):
    items = []
    for line in body.split("\n"):
        if line.lstrip().startswith("\\item") or not items:
            items.append(line)
        else:
            items[-1] += "\n" + line
    return items


def headings(tex):
    out = []
    for m in ITEMIZE.finditer(tex):
        before = [l for l in tex[:m.start()].splitlines() if l.strip() and not l.lstrip().startswith("%")]
        out.append(plain(before[-1])[:120] if before else "")
    return out


def parse(tex):
    """{subtitle, summary, skills: [(category, [items])], bullets: [[item latex]]} of a Morrow-shaped resume."""
    sub, summ, block = SUBTITLE.search(tex), SUMMARY.search(tex), SKILLS_BLOCK.search(tex)
    if not (sub and summ and block):
        raise LatexError("Your .tex resume needs Morrow's shapes: a subtitle line under the name, a \\section{Summary} paragraph "
                         "and \\section{Skills} lines like {\\bfseries Languages:} Python, SQL.")
    skills = [(m.group("category"), split_items(m.group("items"))) for m in map(SKILL_LINE.match, block.group("block").splitlines()) if m]
    if not skills:
        raise LatexError("The Skills section has no '{\\bfseries Category:} items' lines.")
    return {"subtitle": sub.group("subtitle").strip(), "summary": summ.group("summary").strip(), "skills": skills,
            "bullets": [bullet_items(m.group("body")) for m in ITEMIZE.finditer(tex)]}


def render(tex, s):
    tex = SUBTITLE.sub(lambda m: m.group(1) + s["subtitle"] + m.group(3), tex, count=1)
    tex = SUMMARY.sub(lambda m: m.group(1) + s["summary"] + m.group(3), tex, count=1)
    blocks = iter(s["bullets"])

    def one(m):
        items = next(blocks, None)
        return m.group(0) if items is None else m.group(1) + "\n".join(items) + m.group(3)
    tex = ITEMIZE.sub(one, tex)

    def skills_block(block):
        lines, i = [], 0
        for line in block.splitlines():
            m = SKILL_LINE.match(line)
            if m is None or i >= len(s["skills"]):
                lines.append(line)
                continue
            cat, items = s["skills"][i]
            i += 1
            lines.append("{\\bfseries %s:} %s%s" % (cat, ", ".join(items), m.group("tail") or ""))
        return "\n".join(lines)
    return SKILLS_BLOCK.sub(lambda m: m.group(1) + skills_block(m.group("block")) + m.group(3), tex, count=1)


def compile_pdf(tex, timeout=240):
    """PDF bytes of a .tex string. Never lets the document run programs or read other files."""
    bad = unsafe_tex(tex)
    if bad:
        raise LatexError(f"The resume uses {bad}, which could run a program or read another file, so it wasn't compiled.")
    found = compiler()
    if not found:
        raise LatexError("No LaTeX compiler is installed. Install one with: brew install tectonic")
    name, path = found
    with tempfile.TemporaryDirectory(prefix="reachout-tex-") as tmp:
        src = Path(tmp) / "resume.tex"
        src.write_text(tex, encoding="utf-8")
        cmd = ([path, "--outdir", tmp, str(src)] if name == "tectonic" else
               [path, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={tmp}", str(src)])
        try:
            done = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise LatexError(f"{name} took longer than {timeout} seconds.")
        pdf = Path(tmp) / "resume.pdf"
        if done.returncode or not pdf.exists():
            tail = "\n".join(((done.stdout or "") + (done.stderr or "")).strip().splitlines()[-8:])
            raise LatexError(f"{name} couldn't compile the resume:\n{tail}")
        data = pdf.read_bytes()
    if not data or len(data) > MAX_PDF:
        raise LatexError("The compiled PDF was empty or larger than 2 MB.")
    return data


def page_count(data):
    n = len(PAGE_OBJECT.findall(data))
    for m in STREAM.finditer(data):
        try:
            n += len(PAGE_OBJECT.findall(zlib.decompress(m.group(1))))
        except zlib.error:
            continue
    return n


# --- Morrow's template filled from Reachout's resume parts ---------------------------------------------------------

def from_parts(prof, extra, t):
    """A .tex from the template: header from the profile, then summary, sections and skill groups from `t`
    (Reachout's tailored resume, every piece copied from the resume)."""
    head = TEMPLATE.read_text(encoding="utf-8").split("\\begin{document}")[0]
    head = "\n".join(l for l in head.splitlines() if not l.lstrip().startswith("%"))  # its notes would match SUBTITLE
    e = escape
    links = []
    for key in ("github", "linkedin", "portfolio"):
        url = (extra.get(key) or "").strip()
        if url.startswith("http"):
            links.append("\\href{%s}{%s}" % (url.replace("%", "\\%").replace("#", "\\#"), e(re.sub(r"^https?://(www\.)?", "", url).rstrip("/"))))
    contact = [e(x) for x in (extra.get("location", ""), prof.get("email", ""), prof.get("phone", "")) if x]
    sections = t.get("sections") or []
    first_role = next((it["title"] for s in sections if re.search(r"experience|work", s["heading"], re.I) for it in s["items"]), "")
    body = ["\\begin{document}", "\\begin{center}",
            "  {\\LARGE\\bfseries\\color{ink} %s}\\\\[3pt]" % e(prof.get("name", "")),
            "  {\\normalsize\\color{muted} %s}\\\\[5pt]" % e(first_role or "Resume"),
            "  {\\small %s%s}" % (" \\;$\\cdot$\\; ".join(contact), ("\\\\[2pt]\n   " + " \\;$\\cdot$\\; ".join(links)) if links else ""),
            "\\end{center}", ""]
    if t.get("summary"):
        body += ["\\section{Summary}", e(t["summary"]), ""]
    for sec in sections:
        if re.search(r"skill", sec["heading"], re.I):
            continue
        body += ["\\section{%s}" % e(sec["heading"] or "Experience"), ""]
        for it in sec["items"]:
            if it.get("org") or it.get("dates"):
                body.append("\\role{%s}{%s}{%s}" % (e(it.get("title", "")), e(it.get("dates", "")), e(it.get("org", ""))))
            else:
                body.append("{\\bfseries %s}\\par\\vspace{2pt}" % e(it.get("title", "")))
            if it.get("bullets"):
                body += ["\\begin{itemize}"] + ["  \\item " + e(b) for b in it["bullets"]] + ["\\end{itemize}"]
            body.append("")
    groups = t.get("skill_groups") or ([("Skills", t["skills"])] if t.get("skills") else [])
    if groups:
        body += ["\\section{Skills}"] + ["{\\bfseries %s:} %s\\par\\vspace{2pt}" % (e(c), ", ".join(e(i) for i in items)) for c, items in groups] + [""]
    body.append("\\end{document}")
    return head + "\n".join(body) + "\n"


def tailor_tex(base_tex, proposal, original_terms):
    """Apply the AI's proposed order to the user's own .tex under the reorder-only rules. Returns (tex, notes)."""
    s = parse(base_tex)
    # The AI sees and answers in plain text; compare in plain text, then put the original LaTeX back in the new order.
    plain_skills = [(c, [plain(i) for i in items]) for c, items in s["skills"]]
    back = {(c, plain(i)): i for c, items in s["skills"] for i in items}
    accepted, n1 = R.accept_skills(plain_skills, proposal.get("skills"))
    skills = [(c, [back[(c, i)] for i in items]) for c, items in accepted]
    bullets, n2 = R.accept_bullets(s["bullets"], headings(base_tex), proposal.get("bullets"))
    summary, n3 = R.accept_text("Summary", proposal.get("summary"), plain(s["summary"]), R.summary_bounds(plain(s["summary"])), original_terms)
    out = {**s, "skills": skills, "bullets": bullets, "summary": escape(summary) if summary != plain(s["summary"]) else s["summary"]}
    return render(base_tex, out), n1 + n2 + n3, s


def latex_listing(s, tex):
    """What the AI sees of a .tex resume: plain text of each part, with indexes."""
    return {"summary": plain(s["summary"]), "skills": {c: [plain(i) for i in items] for c, items in s["skills"]},
            "roles": [{"index": n, "role": h, "bullets": [(i, plain(b)) for i, b in enumerate(items)]}
                      for n, (h, items) in enumerate(zip(headings(tex), s["bullets"]))]}
