"""The Morning agent: runs an account's daily mail routine (Phase 3, background jobs under agent control).

For accounts whose agent switch is On, the daily sync (features/apps/sync.py) hands its morning run to this agent
instead of running the fixed list of steps. The agent:

1. syncs the mailbox once (the existing combined sync: applications, inbox, job alerts, bounces, replies);
2. delegates to the Inbox agent, which sorts the new job mail and sends one summary;
3. delegates to the Communicator, which handles new replies (answers wait for the user's approval);
4. reports what each did; a failed step is reported, not hidden, and doesn't stop the others.

Accounts in Off or Shadow keep the plain daily sync, unchanged."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from infra import runtime, tools
from infra.tools import WRITE_INTERNAL, tool


@tool("mail.sync", "1", "The user's combined daily mail sync (applications, inbox, job alerts, bounces, replies), "
      "read-only on the mailbox.",
      {"type": "object", "additionalProperties": False, "properties": {}},
      {"type": "object", "required": ["ok"]}, WRITE_INTERNAL, timeout=1800)
def mail_sync(ctx):
    from features.apps.sync import sync_all
    sync_all(ctx.uid, reason="agent")
    return {"ok": True}


class State(TypedDict, total=False):
    synced: dict
    inbox: dict
    replies: dict
    notes: Annotated[list, operator.add]


def sync(state, config):
    c = runtime.ctx(config)
    if c.mode != "on":
        return {"synced": {"skipped": "shadow mode"}}
    runtime.checkpoint(config)
    try:
        return {"synced": tools.call(c, "mail.sync", {})}
    except Exception as e:
        return {"synced": {"error": str(e)[:300]}, "notes": [f"Mail sync failed: {str(e)[:200]}"]}


def _child(config, agent_id, inp, key):
    child = runtime.delegate(config, agent_id, inp, key)
    return {"status": child["status"], "error": child.get("error"), "notes": (child.get("result") or {}).get("notes", [])}


def inbox(state, config):
    return {"inbox": _child(config, "inbox_watcher", {}, "inbox")}


def replies(state, config):
    return {"replies": _child(config, "communicator", {}, "replies")}  # the sync above already read new replies


def build():
    g = StateGraph(State)
    for name, fn in (("sync", sync), ("inbox", inbox), ("replies", replies)):
        g.add_node(name, fn)
    g.add_edge(START, "sync")
    g.add_edge("sync", "inbox")
    g.add_edge("inbox", "replies")
    g.add_edge("replies", END)
    return g


def register():
    runtime.register_agent(runtime.Agent(
        "morning", "1", "morning-1", frozenset({"mail.sync"}), build, max_steps=8,
        description="Your daily mail routine: syncs the mailbox, then the Inbox agent and the Communicator take over.",
        instructions="one sync; delegate inbox then replies; report failures without stopping"))


register()
