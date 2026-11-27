"""Tool registry: the only way agents act. Each tool wraps existing Inkwell code behind a declared contract.

A tool declares an id, version, description, input and output schemas, a risk class, and a timeout. Agents call
tools through call(ctx, tool_id, args) with a ToolContext built by the runtime from the task's owner:

- **Identity comes from the context, never from arguments.** A tool receives `ctx.uid` (and a Workspace for it) and
  its arguments; schemas refuse any argument the tool didn't declare, so a model can't smuggle in a `uid`.
- **Grants.** ctx.grants is the set of tool ids the agent version may use (part of the agent version, ADR-0007).
  A call outside its grants is refused and audited as access.denied.
- **Risk classes.** read · write_internal · external_action · destructive.
  - In **shadow** mode only `read` tools run; anything else is refused (ShadowRefused), so a shadow run can't change
    the user's data or the outside world.
  - `external_action` tools run only through a per-action human approval (ADR-0005, infra/approvals.py): the first
    call creates an approval and pauses the run; the tool runs once, with the approved arguments, after the user
    approves. Outside an agent task they are refused (ApprovalRequired).
  - `destructive` tools aren't granted to agents.
- **Kill switches** covering the user or agent stop a call before it starts.
- **Telemetry.** Every call writes a `tool_call` span (tool id, version, risk, outcome, error class, and a redacted
  one-line summary of the result's size — never the arguments or result themselves).
- **Validation.** Arguments and results are checked against the declared schemas (a small JSON-schema subset: object
  properties/required/additionalProperties, string with maxLength/enum/pattern, integer/number with min/max, boolean,
  array with items/maxItems). A result that breaks its schema is an error, not passed on.

Timeouts are declared per tool and recorded; enforcement inside a synchronous Python call is cooperative (the tool's
own HTTP timeouts), since a thread can't be killed safely."""

import re
import time
from dataclasses import dataclass, field

from infra import audit, controls, telemetry

READ, WRITE_INTERNAL, EXTERNAL_ACTION, DESTRUCTIVE = "read", "write_internal", "external_action", "destructive"
RISKS = (READ, WRITE_INTERNAL, EXTERNAL_ACTION, DESTRUCTIVE)
MODES = ("shadow", "on")
TOOL_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class ToolError(Exception):
    error_class = "tool_error"
    permanent = False  # True: retrying the step can't help (the task queue fails it at once)


class ToolNotFound(ToolError):
    error_class = "tool_not_found"
    permanent = True


class ToolDenied(ToolError):
    error_class = "permission_denied"
    permanent = True


class ShadowRefused(ToolDenied):
    error_class = "shadow_refused"


class ApprovalRequired(ToolDenied):
    error_class = "approval_required"


class ApprovalRejected(ToolDenied):
    error_class = "approval_rejected"


class ApprovalExpired(ApprovalRejected):
    error_class = "approval_expired"


class OutcomeUnknown(ToolError):
    error_class = "outcome_unknown"
    permanent = True  # never repeated automatically


class InvalidArguments(ToolError):
    error_class = "invalid_arguments"
    permanent = True


class InvalidResult(ToolError):
    error_class = "invalid_result"


@dataclass(frozen=True)
class Tool:
    id: str
    version: str
    description: str
    input_schema: dict
    output_schema: dict
    risk: str
    fn: object = field(repr=False)
    timeout: float = 60.0


@dataclass(frozen=True)
class ToolContext:
    """Built by the runtime from the task (never from model output)."""
    uid: str
    grants: frozenset
    mode: str = "shadow"
    trace_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    agent_version: str | None = None

    def __post_init__(self):
        if not self.uid:
            raise ValueError("A tool context needs the user it acts for.")
        if self.mode not in MODES:
            raise ValueError(f"Unknown mode {self.mode!r}.")

    def workspace(self):
        from core.workspace import Workspace
        return Workspace(self.uid)


REGISTRY = {}


def register(tool):
    if not TOOL_ID.match(tool.id):
        raise ValueError(f"Bad tool id: {tool.id!r}")
    if tool.risk not in RISKS:
        raise ValueError(f"Bad risk class for {tool.id}: {tool.risk!r}")
    for s in (tool.input_schema, tool.output_schema):
        _check_schema(s)
    if tool.input_schema.get("type") != "object" or tool.input_schema.get("additionalProperties") is not False:
        raise ValueError(f"{tool.id}: inputs must be an object schema with additionalProperties false.")
    REGISTRY[tool.id] = tool
    return tool


def tool(id, version, description, input_schema, output_schema, risk, timeout=60.0):
    """Decorator: register fn(ctx, **args) as a tool."""
    def deco(fn):
        register(Tool(id, version, description, input_schema, output_schema, risk, fn, timeout))
        return fn
    return deco


def describe(grants=None):
    """Tool contracts (for building an agent's tool list), optionally only the granted ones. No functions included."""
    return [{"id": t.id, "version": t.version, "description": t.description, "input_schema": t.input_schema,
             "output_schema": t.output_schema, "risk": t.risk}
            for t in sorted(REGISTRY.values(), key=lambda t: t.id) if grants is None or t.id in grants]


# ---- schema checking ----------------------------------------------------------------------------------------------

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float),
          "null": type(None)}
_KEYS = {"type", "properties", "required", "additionalProperties", "items", "maxItems", "maxLength", "enum",
         "pattern", "minimum", "maximum", "description", "anyOf"}


def _check_schema(s):
    if not isinstance(s, dict) or set(s) - _KEYS:
        raise ValueError(f"Unsupported schema keys: {sorted(set(s) - _KEYS) if isinstance(s, dict) else s!r}")
    for sub in (s.get("properties") or {}).values():
        _check_schema(sub)
    if isinstance(s.get("items"), dict):
        _check_schema(s["items"])
    for sub in s.get("anyOf") or []:
        _check_schema(sub)


def validate(value, schema, path="$"):
    """Raise ValueError (with the path) if value doesn't match schema."""
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            try:
                return validate(value, sub, path)
            except ValueError:
                continue
        raise ValueError(f"{path}: matches none of the allowed shapes")
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = any(isinstance(value, _TYPES[x]) and not (x in ("integer", "number") and isinstance(value, bool))
                 for x in types)
        if not ok:
            raise ValueError(f"{path}: expected {t}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: not an allowed value")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{path}: wrong format")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path}: more than {schema['maxItems']} items")
        for i, v in enumerate(value):
            if isinstance(schema.get("items"), dict):
                validate(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in value:
                raise ValueError(f"{path}.{k}: required")
        if schema.get("additionalProperties", True) is False:
            extra = set(value) - set(props)
            if extra:
                raise ValueError(f"{path}: unexpected field(s) {', '.join(sorted(map(str, extra)))}")
        for k, v in value.items():
            if k in props:
                validate(v, props[k], f"{path}.{k}")


# ---- calling ------------------------------------------------------------------------------------------------------

def _deny(ctx, t, reason, exc):
    try:
        audit.record("access.denied", ctx.uid, target=f"tool:{t}", outcome="denied",
                     details={"reason": reason, "agent_id": ctx.agent_id, "task_id": ctx.task_id}, trace_id=ctx.trace_id or "")
    except audit.AuditError:
        pass  # still refused
    raise exc


def request_approval(ctx, tool_id, args):
    """For an external_action tool: create (or find) the approval for this exact call, with every check call() makes
    except running it. Returns the approval's status ("pending", "approved", "rejected", ...). Never in shadow mode."""
    if not isinstance(ctx, ToolContext):
        raise TypeError("Tools run only with a ToolContext.")
    t = REGISTRY.get(tool_id)
    if t is None:
        raise ToolNotFound(f"There is no tool called {tool_id}.")
    if t.risk != EXTERNAL_ACTION:
        raise ToolError(f"{tool_id} doesn't need approval.")
    if tool_id not in ctx.grants:
        _deny(ctx, tool_id, "not_granted", ToolDenied(f"This agent may not use {tool_id}."))
    if ctx.mode == "shadow":
        raise ShadowRefused(f"{tool_id} acts outside Inkwell, so it doesn't run in shadow mode.")
    if not ctx.task_id:
        raise ApprovalRequired(f"{tool_id} needs your approval, and approvals belong to an agent task.")
    if controls.stopped(ctx.uid, ctx.agent_id):
        raise ToolDenied("Agent work is stopped right now (kill switch).")
    try:
        validate(args, t.input_schema)
    except ValueError as e:
        raise InvalidArguments(f"{tool_id}: {e}") from None
    from infra import approvals
    return approvals.ensure(ctx, t, args)


def call(ctx, tool_id, args=None):
    """Run a granted tool for ctx.uid. Returns its (validated) result; raises a ToolError subclass otherwise."""
    if not isinstance(ctx, ToolContext):
        raise TypeError("Tools run only with a ToolContext.")
    args = {} if args is None else args
    t = REGISTRY.get(tool_id)
    with telemetry.span("tool_call", tool_id, tool_id=tool_id, task_id=ctx.task_id, agent_id=ctx.agent_id,
                        agent_version=ctx.agent_version) as sp:
        if t is None:
            raise ToolNotFound(f"There is no tool called {tool_id}.")
        if tool_id not in ctx.grants:
            _deny(ctx, tool_id, "not_granted", ToolDenied(f"This agent may not use {tool_id}."))
        if t.risk == DESTRUCTIVE:
            _deny(ctx, tool_id, "destructive", ToolDenied(f"{tool_id} can't be used by agents."))
        if ctx.mode == "shadow" and t.risk != READ:
            raise ShadowRefused(f"{tool_id} changes data, so it doesn't run in shadow mode.")
        if controls.stopped(ctx.uid, ctx.agent_id):
            raise ToolDenied("Agent work is stopped right now (kill switch).")
        try:
            validate(args, t.input_schema)
        except ValueError as e:
            raise InvalidArguments(f"{tool_id}: {e}") from None
        started = time.monotonic()

        def run(final_args):
            out = t.fn(ctx, **final_args)
            try:
                validate(out, t.output_schema)
            except ValueError as e:
                raise InvalidResult(f"{tool_id} returned something unexpected: {e}") from None
            return out
        if t.risk == EXTERNAL_ACTION:
            from infra import approvals
            result = approvals.gate(ctx, t, args, run)
        else:
            result = run(args)
        if sp is not None:
            n = len(result) if isinstance(result, (list, dict)) else None
            sp.update(summary=f"{t.id}@{t.version} ({t.risk}) ok in {round((time.monotonic() - started) * 1000)} ms"
                              + (f", {n} item(s)" if n is not None else ""))
        return result
