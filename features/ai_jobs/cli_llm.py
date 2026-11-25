"""The AI through the Claude Code or Kiro command-line tool on this computer, under the user's own plan.
Adapted from Morrow (MIT, (c) 2026 Morrow contributors): common/ai_cli.py and common/llm.py.

Only used when Inkwell runs on the user's own computer (never on the hosted service): a personal Claude Code or
Kiro login is for its owner's own use. With web=True the tool may search and read web pages (research and the daily
briefing); otherwise it gets no tools at all."""

import os
import re
import shutil
import signal
import subprocess

TOOLS = {"claude_cli": "claude", "kiro_cli": "kiro-cli"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
QUOTA = re.compile(r"usage limit|rate limit|session limit|quota|out of credits|limit reached", re.I)


class QuotaError(RuntimeError):
    pass


def available(pid):
    return bool(TOOLS.get(pid) and shutil.which(TOOLS[pid]))


def command(pid, prompt, web=False, model=""):
    if pid == "claude_cli":
        tools = ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch"] if web else ["--tools", ""]
        return ["claude", "-p", prompt, *tools, "--permission-mode", "dontAsk", "--strict-mcp-config",
                "--output-format", "text", "--no-session-persistence", *(["--model", model] if model else [])]
    trust = "--trust-tools=web_search,web_fetch" if web else "--trust-tools="
    return ["kiro-cli", "chat", "--no-interactive", trust, *(["--model", model] if model else []), prompt]


def run(pid, prompt, web=False, timeout=90, model=""):
    """The tool's answer as plain text. Raises RuntimeError (QuotaError when the plan's limit is reached)."""
    if not available(pid):
        raise RuntimeError(f"{TOOLS.get(pid, pid)} isn't installed on this computer.")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"))}
    p = subprocess.Popen(command(pid, prompt, web, model), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True, env=env)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise RuntimeError(f"The AI didn't answer within {timeout} seconds.")
    out, err = ANSI.sub("", out or "").strip(), ANSI.sub("", err or "").strip()
    if QUOTA.search(err) or (p.returncode and QUOTA.search(out)):
        raise QuotaError("Your Claude Code / Kiro plan has reached its usage limit. Try again later.")
    if p.returncode or not out:
        last = (err or out).splitlines()[-1:] or ["no answer"]
        raise RuntimeError(f"{TOOLS[pid]} failed: {last[0][:200]}")
    return out


def ask(pid, system, human, as_json=False, web=False, timeout=90, model=""):
    """One question with Inkwell's instructions on top; for JSON, a second try asks for JSON only."""
    from features.ai_jobs.models import first_json
    prompt = f"{system}\n\n{human}"
    if as_json:
        prompt += "\n\nReply with ONE valid JSON object and nothing else."
    text = run(pid, prompt, web, timeout, model)
    if not as_json:
        return text
    try:
        return first_json(text)
    except ValueError:
        return first_json(run(pid, prompt + "\nReply with JSON only.", web, timeout, model))
