# Approval and permission model

**Status:**
- Existing consent mechanisms: CURRENT IMPLEMENTATION.
- Agent approval model: APPROVED DESIGN ([ADR-0005](../decisions/ADR-0005-human-approval-for-external-actions.md)),
  not implemented.

**Last reviewed:** 2026-11-26

## Requirement (approved 2026-11-25)
> "Sending emails, sending WhatsApp messages and submitting applications must always require explicit user approval.
> Agents must never bypass this requirement."

## Existing consent and approval mechanisms (not agent-related)

| Mechanism | What it gates | Where | Kind |
|---|---|---|---|
| Campaign send | Sending a campaign is a user action (`POST /api/send`); daily limit `DAILY_LIMIT` | `routes/send.py`, `core/sending.py` | Per action |
| Reply send | The user sends each reply (`POST /api/replies/<key>/send`); drafts are never sent automatically | `features/replies.py:759` | Per action |
| Queued emails and posts | Queued by the user, then sent at their due time by `send-queue` | `features/jobs.py` | Per action, deferred |
| **Auto-apply consent** | `auto_apply_consent` timestamp in settings, set through `PUT /api/ai-jobs/details`; required by `POST /api/ai-jobs/apply` and `finish` | `features/ai_jobs/runs.py:205`, `routes.py:74`, `:117` | **Standing consent** (covers later submissions) |
| Rules gate | Jobs that break the user's rules or fall below the minimum score return 409 unless the user passes `anyway` | `features/ai_jobs/routes.py:49–67` | Per action |
| Apply limits | 20 applications per day (`APPLY_PER_DAY`); never on LinkedIn, Naukri, Indeed or Glassdoor (`siteapply.NEVER`); account sites only in Watch mode; stops at CAPTCHA, email codes and unanswered questions (`needs_you`) | `features/ai_jobs/apply.py`, `siteapply.py` | Hard limits |
| Answer bank | Only `approved` answers are typed into forms. Learned and model-drafted answers wait in `review` | `features/ai_jobs/answers.py:42–58` | Per item |
| Learned rule suggestions | Wait for the user to accept them | `features/ai_jobs/learning.py` | Per item |
| Browser extension | Fills forms in the user's own browser and **never submits** | `features/extension.py` | — |
| Follow-ups | Drafts only, never sent | `features/ai_jobs/tracker.py` | — |

## Agent approval model (PROPOSED DESIGN)

### Tool risk classes

| Class | Examples (existing code that would become tools) | Approval |
|---|---|---|
| `read` | Board readers, `sitejobs.discover`/`fetch`, `ranking.rank`, `signals.liveness`, `research` (public pages) | None |
| `write_internal` | Saving a match, a draft cover letter or a tailored resume to the user's own workspace | None in `on` mode. In shadow mode, writes go only to shadow records |
| `external_action` | Email send, WhatsApp send, application submit (`apply.apply_one(…, submit=True)`), LinkedIn post, GitHub write, reply send | **Always a per-action approval** |
| `destructive` | Deleting user data | Not granted to agents in the first workflow |

### Approval flow
```mermaid
sequenceDiagram
  participant A as Agent run (worker)
  participant T as Tool layer
  participant DB as approvals / tasks
  participant U as User (app)
  A->>T: call external_action tool (payload)
  T->>DB: insert approval {uid, task_id, tool, payload (sealed), idempotency_key, status: pending}
  T-->>A: interrupt() — run paused, checkpoint saved
  U->>DB: approve / edit / reject (only the owning user)
  DB-->>A: resume task from checkpoint
  A->>T: execute with approved payload (checkpoint() first; idempotency key checked)
  T->>DB: record outcome (once)
```

### Rules
- An approval is per action and per payload. Editing the payload creates a new approval.
- Agents can't approve their own requests. A Super Admin can't approve on a user's behalf. **TO BE CONFIRMED**:
  this follows from metadata-only visibility.
- **Standing consent never counts as approval for agents.** The existing `auto_apply_consent` is not reused.
- The executing tool calls `operations`-style `checkpoint()` right before acting (cancellation and lease check). It
  records the outcome even if the run's lease was lost, so a later run never repeats it.
- An approval expires after a period **TO BE DEFINED**. An expired approval is treated as rejected.

## Permissions for agents and tools (PROPOSED DESIGN)
- **Grants:** `tool_grants` (agent × tool × approval policy) are part of an immutable `agent_versions` record. Changing
  them means publishing a new version, which needs evaluation and approval
  ([ADR-0007](../decisions/ADR-0007-versioned-agent-and-model-configs.md)).
- **Admin permissions already reserved:** `admin.control_agents`, `admin.manage_models`, `admin.manage_budgets` and
  `admin.read_metadata`.

## Pending decision
Should the existing auto-apply move from standing consent to per-action approval? This is **PENDING DECISION D-07**.
