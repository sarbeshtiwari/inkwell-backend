# Agent execution lifecycle and the first workflow

**Status:**
- APPROVED DESIGN: the workflow and its constraints ([ADR-0010](../decisions/ADR-0010-shadow-mode-first-workflow.md),
  [ADR-0005](../decisions/ADR-0005-human-approval-for-external-actions.md)).
- PROPOSED: the state machine and data flow below.
- **Nothing here is implemented, and implementation is not authorized.**

**Last reviewed:** 2026-11-26

## Generic lifecycle of one agent run (PROPOSED)
1. **Task claimed** by a worker (lease) after kill-switch and budget checks
   ([WORKER_AND_QUEUE_ARCHITECTURE.md](WORKER_AND_QUEUE_ARCHITECTURE.md)).
2. **Run record created** (`agent_runs`). It records:
   - `agent_id`, `agent_version`, the instructions version, the tool grant set and `model_config_version`
     (requirement 1);
   - `trace_id` and `parent_span_id`.
3. **Graph executes** (LangGraph). It loads the checkpoint if one exists, then loops:
   1. plan;
   2. pick a tool (from its grants) or delegate;
   3. observe the typed result;
   4. update state and checkpoint.
   - Every model call goes through the gateway; every tool call through the registry. Each emits a span.
4. **Delegation:** the agent calls `delegate(agent_id, input)`, which creates a child task with the same `uid` and
   `trace_id`, `depth+1` and the ancestry list. The parent waits (`waiting_children`). Depth, child and cycle limits
   are enforced at creation.
5. **Approval:** an `external_action` tool creates an approval and the graph `interrupt()`s (`waiting_approval`). On a
   decision the task is re-queued and resumes from its checkpoint.
6. **Cancellation:** `cancel_requested` (by the user or Super Admin) or a kill switch is seen at the next checkpoint.
   The run stops between steps and records `cancelled`. Children are cancelled too.
7. **Finish:** the outputs are validated against the agent's output schema, a `final_output` span is written, the
   totals are written to the run and trace, and the parent is notified.
8. **Failure:** retryable errors re-queue with backoff (resuming from the checkpoint). Exhausted attempts go to
   `dead_letter`. An *outcome unknown* external action is never retried automatically.

## First workflow: Find and Prepare Applications

### User goal and entry point
- **Goal:** for example, "Find 10 strong backend roles this week and prepare applications."
- **Entry point (proposed):** `POST /api/agents/tasks` with `{workflow: "find_and_prepare", goal, constraints}`.
  - Constraints include roles, location, count, and whether to submit.
  - During shadow mode, the workflow is triggered alongside the existing `POST /api/ai-jobs/match` run, so the results
    can be compared.

### Delegation chain
```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant W as Web API
  participant O as Orchestrator
  participant S as Job Scout
  participant A as Job Analyst
  participant R as Researcher
  participant Wr as Writer
  participant Ap as Applicant
  U->>W: goal
  W->>O: task (uid, trace_id, budget, deadline, mode)
  O->>O: plan (sub-goals, counts)
  O->>S: delegate: find openings (criteria)
  S-->>O: deduped live jobs (ids, sources, liveness)
  O->>A: delegate: assess fit (jobs)
  A->>R: delegate (only if company facts are needed)
  R-->>A: validated company facts (with page quotes)
  A-->>O: ranked shortlist with reasons, flags (ghost/scam), score
  O->>Wr: delegate: prepare materials (top N)
  Wr-->>O: tailored resume (reorder-only), cover letter, form answers (drafts → review)
  O->>Ap: delegate: prepare submissions
  Ap-->>O: application packages (ready / needs_you)
  Ap->>U: approval request per application (shadow mode: none; never submits)
  U-->>Ap: approve / edit / reject (on mode only)
  Ap->>Ap: submit after approval (checkpoint, idempotency key)
  O-->>U: report (what was found, prepared, submitted, skipped and why)
```

### Data flow by step

| Step | Reads (via tools) | Writes | External effects |
|---|---|---|---|
| Orchestrator plan | Profile, rules, learned taste (`kv`) | Task tree | None |
| Job Scout | Public boards, career sites, My companies, optional Tavily/Brave with the user's keys | Candidate job list (run output) | Read-only HTTP to public sources |
| Job Analyst | Resume, rules, learned preferences, signals | Shortlist and reasons (run output; in `on` mode also the match record) | None |
| Researcher | Public company pages (validated quotes) | Research result | Read-only HTTP |
| Writer | Resume, job description, approved answers | Draft resume, cover letter, answers (answers go to `review`) | None |
| Applicant | Application packages | Approval requests; application record after a submission | **Form submission, only after approval, only in `on` mode** |

### State transitions
Task states as in [WORKER_AND_QUEUE_ARCHITECTURE.md](WORKER_AND_QUEUE_ARCHITECTURE.md#states). The Orchestrator
task sits in `waiting_children` during delegation and in `waiting_approval` while applications await the user.

### Error recovery
- A source that fails doesn't fail the Scout. It is recorded as a span with an error, as the existing match chain does
  per source.
- A Researcher failure lets the Analyst continue without company facts, and flags it.
- A Writer output rejected by `tailor_rules` keeps the original resume, as the existing behaviour does.
- On a worker crash, the run resumes from its checkpoint, and no external action is repeated (idempotency keys).

### Cancellation
- The user, or the Super Admin with `admin.control_agents`, cancels the root task. Cancellation propagates to its
  children.
- Pending approvals are withdrawn.

### Idempotency
- Each submission carries the key `hash(trace_id, "submit_application", job_id)`.
- The existing per-board blocking and "already applied" checks stay in force (`features/ai_jobs/routes.py:79`).

### Budgets
- A per-trace token and cost ceiling, inherited by every child.
- Per-agent run budgets.
- User daily and monthly, and global limits, all checked by the gateway.
- The existing apply limit of 20 per day (`APPLY_PER_DAY`) still applies.

### Execution traces
One trace per goal. Spans for every delegation, model call, tool call, approval, retry, error and the final report.

### Shadow mode
- **Shadow** (first):
  - the workflow runs beside the existing AI job match for the same input;
  - `write_internal` tools write only to shadow records;
  - the **Applicant never creates approvals and never submits**;
  - outputs are compared with the existing pipeline: overlap of found jobs, ranking agreement, rule compliance,
    zero-added-facts checks on resumes, cost and latency.
- **On, for the product owner's account only:** approvals are created; submissions happen only after approval.
- **Wider rollout:** a separate decision.
- **Flag storage:** a per-workflow, per-user `off`/`shadow`/`on` flag, in a `config` collection according to the
  proposal. **TO BE DEFINED.**

## Acceptance criteria for the workflow (PROPOSED; to be approved)
1. In shadow mode it makes **zero external effects**. Verified by tests with fake providers, and by the absence of
   approval or submission records.
2. Every run records versions (requirement 1), and has one trace whose spans cover every model and tool call.
3. Cross-user isolation tests pass for every collection the workflow touches.
4. Cancelling the root task stops all children at their next checkpoint.
5. A worker crash mid-run resumes from its checkpoint, without duplicate external actions.
6. Budgets: exceeding the trace ceiling stops the run with a clear status.
7. Output quality is measured by the Evaluator against agreed datasets. The thresholds are TO BE DEFINED.
8. In `on` mode, no submission happens without a matching approved approval record.
