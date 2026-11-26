"""Agent runtime: LangGraph graphs run as queue tasks, with checkpoints in MongoDB (sealed, per user).

- **Checkpointer.** MongoCheckpointer(uid) implements LangGraph's BaseCheckpointSaver over two collections,
  `checkpoints` and `checkpoint_writes`. Every document carries `uid` and every query includes it, so one user's
  checkpoints are never read for another. Checkpoint state and pending writes (which hold user content) are
  serialised with LangGraph's serializer and then encrypted with the data key; only ids, namespaces and step numbers
  are in the clear. (The packaged langgraph-checkpoint-mongodb stores state unencrypted and isn't uid-scoped, so it
  isn't used.)
- **Agents.** An Agent declares its id, version, instructions version, granted tools, mode default, step limit and a
  build() returning a LangGraph StateGraph builder. register_agent() makes it runnable as task kind
  "agent.<agent_id>"; a task's thread is its task id, so a retried or reclaimed task resumes from its last checkpoint
  instead of starting over.
- **Inside a node** the agent gets a ToolContext (from the task's owner, never from model output) via `ctx(config)`,
  and calls tools with infra.tools.call; model calls go through the gateway; checkpoint(config) between steps stops
  on cancellation or a kill switch.
- **Limits.** `max_steps` becomes LangGraph's recursion limit; hitting it fails the task (error class "max_steps",
  not retried). Delegation, budgets and deadlines are enforced by the task queue and the gateway.

- **Approvals.** A node calling an external_action tool pauses the graph (LangGraph interrupt, infra/approvals.py);
  the task waits without a lease and resumes from its checkpoint when the user decides, re-running only that node.

- **Versions (ADR-0007).** Every run writes an `agent_runs` record with the exact agent version, instructions
  version and hash, the tool grants (with each tool's version), and afterwards the models the provider actually
  answered with and the run's token totals. An agent version is immutable: `agent_versions` stores a fingerprint of
  each (agent, version) the first time it runs, and a later run whose definition differs under the same version
  number is refused — a changed agent needs a new version number.

Not here yet: promoting a version needs an evaluation result and an approval (the Evaluator, PH4-02)."""

import datetime as dt
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from langgraph.checkpoint.base import (WRITES_IDX_MAP, BaseCheckpointSaver, ChannelVersions, Checkpoint,
                                       CheckpointMetadata, CheckpointTuple, get_checkpoint_id, get_checkpoint_metadata)
from langgraph.errors import GraphRecursionError
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from core.crypto import fernet
from core.db import M
from infra import tasks, telemetry, tools

AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def ensure_indexes():
    M.checkpoints.create_index([("uid", ASCENDING), ("thread_id", ASCENDING), ("ns", ASCENDING), ("checkpoint_id", DESCENDING)],
                               unique=True)
    M.checkpoint_writes.create_index([("uid", ASCENDING), ("thread_id", ASCENDING), ("ns", ASCENDING),
                                      ("checkpoint_id", ASCENDING), ("task_id", ASCENDING), ("idx", ASCENDING)], unique=True)
    M.agent_runs.create_index([("uid", ASCENDING), ("started", DESCENDING)])
    M.agent_runs.create_index([("agent_id", ASCENDING), ("agent_version", ASCENDING), ("started", DESCENDING)])
    M.agent_runs.create_index("task_id")


# ---- checkpointer -------------------------------------------------------------------------------------------------

class MongoCheckpointer(BaseCheckpointSaver):
    """LangGraph checkpoints for one user: sealed state, every read and write scoped to `uid`."""

    def __init__(self, uid):
        super().__init__()
        if not uid:
            raise ValueError("Checkpoints belong to a user.")
        self.uid = uid

    def _seal(self, value):
        kind, data = self.serde.dumps_typed(value)
        return {"t": kind, "d": fernet.encrypt(data)}

    def _open(self, blob):
        return self.serde.loads_typed((blob["t"], fernet.decrypt(blob["d"])))

    @staticmethod
    def _ids(config):
        c = config["configurable"]
        return c["thread_id"], c.get("checkpoint_ns", "")

    def _tuple(self, doc):
        thread_id, ns, cid = doc["thread_id"], doc["ns"], doc["checkpoint_id"]
        writes = M.checkpoint_writes.find({"uid": self.uid, "thread_id": thread_id, "ns": ns, "checkpoint_id": cid}
                                          ).sort([("task_id", ASCENDING), ("idx", ASCENDING)])
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cid}},
            checkpoint=self._open(doc["checkpoint"]), metadata=self._open(doc["metadata"]),
            parent_config=({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": doc["parent_id"]}}
                           if doc.get("parent_id") else None),
            pending_writes=[(w["task_id"], w["channel"], self._open(w["value"])) for w in writes])

    def get_tuple(self, config) -> CheckpointTuple | None:
        thread_id, ns = self._ids(config)
        q = {"uid": self.uid, "thread_id": thread_id, "ns": ns}
        cid = get_checkpoint_id(config)
        if cid:
            q["checkpoint_id"] = cid
        doc = M.checkpoints.find_one(q, sort=[("checkpoint_id", DESCENDING)])
        return self._tuple(doc) if doc else None

    def list(self, config, *, filter: dict[str, Any] | None = None, before=None, limit: int | None = None
             ) -> Iterator[CheckpointTuple]:
        q = {"uid": self.uid}
        if config:
            thread_id, ns = config["configurable"]["thread_id"], config["configurable"].get("checkpoint_ns")
            q["thread_id"] = thread_id
            if ns is not None:
                q["ns"] = ns
            if get_checkpoint_id(config):
                q["checkpoint_id"] = get_checkpoint_id(config)
        if before and get_checkpoint_id(before):
            q["checkpoint_id"] = {"$lt": get_checkpoint_id(before)}
        n = 0
        for doc in M.checkpoints.find(q).sort("checkpoint_id", DESCENDING):
            t = self._tuple(doc)
            if filter and any(t.metadata.get(k) != v for k, v in filter.items()):
                continue
            yield t
            n += 1
            if limit is not None and n >= limit:
                return

    def put(self, config, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions):
        thread_id, ns = self._ids(config)
        doc = {"uid": self.uid, "thread_id": thread_id, "ns": ns, "checkpoint_id": checkpoint["id"],
               "parent_id": config["configurable"].get("checkpoint_id"), "step": (metadata or {}).get("step"),
               "checkpoint": self._seal(checkpoint), "metadata": self._seal(get_checkpoint_metadata(config, metadata)),
               "at": dt.datetime.now(dt.timezone.utc)}
        M.checkpoints.replace_one({"uid": self.uid, "thread_id": thread_id, "ns": ns, "checkpoint_id": checkpoint["id"]},
                                  doc, upsert=True)
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id, ns = self._ids(config)
        cid = config["configurable"]["checkpoint_id"]
        for i, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, i)
            key = {"uid": self.uid, "thread_id": thread_id, "ns": ns, "checkpoint_id": cid, "task_id": task_id, "idx": idx}
            doc = {**key, "channel": channel, "task_path": task_path, "value": self._seal(value)}
            if idx >= 0:  # ordinary writes are recorded once (a retried step doesn't duplicate them)
                try:
                    M.checkpoint_writes.insert_one(doc)
                except DuplicateKeyError:
                    pass
            else:  # special writes (errors, interrupts, resume values) replace the previous one
                M.checkpoint_writes.replace_one(key, doc, upsert=True)

    def delete_thread(self, thread_id: str) -> None:
        M.checkpoints.delete_many({"uid": self.uid, "thread_id": thread_id})
        M.checkpoint_writes.delete_many({"uid": self.uid, "thread_id": thread_id})

    async def aget_tuple(self, config):
        return self.get_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for t in self.list(config, filter=filter, before=before, limit=limit):
            yield t

    async def aput(self, config, checkpoint, metadata, new_versions):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return self.delete_thread(thread_id)


# ---- agents -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Agent:
    id: str
    version: str
    instructions_version: str
    grants: frozenset
    build: Callable[[], Any] = field(repr=False)  # returns an uncompiled StateGraph builder
    max_steps: int = 25
    description: str = ""
    instructions: str = ""  # the agent's system instructions (fingerprinted with its version)

    def fingerprint(self):
        """What defines this version: instructions, grants with their tool versions, step limit."""
        spec = {"instructions_version": self.instructions_version, "instructions": self.instructions,
                "grants": tool_versions(self.grants), "max_steps": self.max_steps}
        return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()

    @property
    def kind(self):
        return f"agent.{self.id}"


AGENTS = {}


class VersionChanged(tasks.Permanent):
    def __init__(self, agent):
        super().__init__(f"{agent.id} version {agent.version} was changed without a new version number; "
                         "publish it as a new version.", "version_changed")


def tool_versions(grants):
    return {t: tools.REGISTRY[t].version if t in tools.REGISTRY else None for t in sorted(grants)}


def _pin_version(agent):
    """Record (agent, version)'s fingerprint the first time it runs; refuse a different definition later."""
    fp = agent.fingerprint()
    doc = {"_id": f"{agent.id}@{agent.version}", "agent_id": agent.id, "version": agent.version,
           "instructions_version": agent.instructions_version, "fingerprint": fp,
           "grants": tool_versions(agent.grants), "max_steps": agent.max_steps,
           "first_seen": dt.datetime.now(dt.timezone.utc)}
    existing = M.agent_versions.find_one_and_update({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
    if existing and existing.get("fingerprint") != fp:
        raise VersionChanged(agent)
    return fp


def register_agent(agent):
    if not AGENT_ID.match(agent.id):
        raise ValueError(f"Bad agent id: {agent.id!r}")
    unknown = set(agent.grants) - set(tools.REGISTRY)
    if unknown:
        raise ValueError(f"{agent.id} is granted tools that don't exist: {', '.join(sorted(unknown))}")
    if not 1 <= agent.max_steps <= 200:
        raise ValueError("max_steps must be between 1 and 200.")
    AGENTS[agent.id] = agent
    tasks.register(agent.kind, lambda task: _run(agent, task))
    return agent


def start(uid, agent_id, input, *, mode="shadow", parent_id=None, **task_options):
    """Queue a run of an agent for `uid` (a delegation when parent_id is given). Returns the task id."""
    agent = AGENTS.get(agent_id)
    if agent is None:
        raise ValueError(f"Unknown agent: {agent_id!r}")
    if mode not in tools.MODES:
        raise ValueError(f"Unknown mode {mode!r}.")
    return tasks.enqueue(uid, agent.kind, {"input": input, "mode": mode}, parent_id=parent_id, agent_id=agent.id,
                         **task_options)


def ctx(config):
    """The ToolContext of the run executing this node."""
    return config["configurable"]["inkwell_ctx"]


def checkpoint(config):
    """Stop here if the task was cancelled, a kill switch covers it, or its deadline passed (raises)."""
    config["configurable"]["inkwell_task"].checkpoint()


def _run(agent, task):
    payload = task.input or {}
    mode = payload.get("mode", "shadow")
    tctx = tools.ToolContext(uid=task.uid, grants=agent.grants, mode=mode, trace_id=task.trace_id, task_id=task.id,
                             agent_id=agent.id, agent_version=agent.version)
    saver = MongoCheckpointer(task.uid)
    graph = agent.build().compile(checkpointer=saver)
    config = {"configurable": {"thread_id": task.id, "inkwell_ctx": tctx, "inkwell_task": task},
              "recursion_limit": agent.max_steps}
    resuming = saver.get_tuple({"configurable": {"thread_id": task.id}}) is not None
    fp = _pin_version(agent)
    run_id = uuid.uuid4().hex
    started = dt.datetime.now(dt.timezone.utc)
    M.agent_runs.insert_one({"_id": run_id, "uid": task.uid, "task_id": task.id, "trace_id": task.trace_id,
                             "attempt": task.attempt, "agent_id": agent.id, "agent_version": agent.version,
                             "instructions_version": agent.instructions_version, "fingerprint": fp,
                             "grants": tool_versions(agent.grants), "mode": mode, "resumed": resuming,
                             "started": started, "outcome": "running"})
    outcome = "error"
    try:
        result = _invoke(agent, task, graph, config, payload, resuming)
        outcome = "done"
        return result
    except tasks.Waiting:
        outcome = "waiting_approval"
        raise
    except tasks.Cancelled:
        outcome = "cancelled"
        raise
    finally:
        _close_run(run_id, task, started, outcome)


def _close_run(run_id, task, started, outcome):
    """Fill in what actually ran: models the provider answered with, calls and tokens (from model_usage)."""
    rows = list(M.model_usage.aggregate([
        {"$match": {"uid": task.uid, "task_id": task.id, "at": {"$gte": started.timestamp()}}},
        {"$group": {"_id": {"provider": "$provider", "model_id": "$model_id", "requested": "$requested_model"},
                    "calls": {"$sum": 1}, "input_tokens": {"$sum": {"$ifNull": ["$input_tokens", 0]}},
                    "output_tokens": {"$sum": {"$ifNull": ["$output_tokens", 0]}}}}]))
    models_used = [{**r["_id"], "calls": r["calls"]} for r in rows]
    M.agent_runs.update_one({"_id": run_id}, {"$set": {
        "outcome": outcome, "ended": dt.datetime.now(dt.timezone.utc), "models": models_used,
        "model_calls": sum(r["calls"] for r in rows), "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows)}})


def runs_for(uid, task_id=None):
    q = {"uid": uid, **({"task_id": task_id} if task_id else {})}
    return list(M.agent_runs.find(q, {"_id": 0}).sort("started", DESCENDING).limit(200))


def _invoke(agent, task, graph, config, payload, resuming):
    telemetry.record("step", f"{agent.id}@{agent.version}", summary="resumed from checkpoint" if resuming else "started",
                     agent_id=agent.id, agent_version=agent.version, task_id=task.id)
    try:
        from infra import gateway
        with gateway.context(agent_version=agent.version):  # usage records carry the exact agent version
            result = graph.invoke(None if resuming else payload.get("input", {}), config)
        if isinstance(result, dict) and result.get("__interrupt__"):
            raise tasks.Waiting()  # paused for an approval; resumes from this checkpoint once the user decides
    except GraphRecursionError:
        raise tasks.Permanent(f"{agent.id} took more than {agent.max_steps} steps, so it was stopped.", "max_steps")
    return _jsonable(result)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
