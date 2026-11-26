# Agent specifications

**Status:**
- PROPOSED. The agent set and the first workflow are APPROVED in direction
  ([ADR-0010](../decisions/ADR-0010-shadow-mode-first-workflow.md)).
- **No agent exists in code.**
- These specifications are not approved schemas or prompts.

**Last reviewed:** 2026-11-26

## Agents

| Agent | Phase | In first workflow? | Goal (from `inkwell-agentic-architecture.md` §3) | External actions |
|---|---|---|---|---|
| [Orchestrator](ORCHESTRATOR_SPECIFICATION.md) | 2 | Yes | Turn a user goal into a plan, delegate sub-tasks, track them to completion, report | None itself |
| [Job Scout](JOB_SCOUT_SPECIFICATION.md) | 2 | Yes | Find relevant openings across sources, dedupe, keep only live ones | None |
| [Job Analyst](JOB_ANALYST_SPECIFICATION.md) | 2 | Yes | Decide fit and whether a job is worth it, explain why, flag ghost or scam jobs | None |
| [Researcher](RESEARCHER_SPECIFICATION.md) | 2 (when needed) | Yes, conditional | Validated company facts | None |
| [Writer](WRITER_SPECIFICATION.md) | 2 | Yes | Tailored resume, cover letter, form answers, outreach drafts | None (drafts only) |
| [Applicant](APPLICANT_SPECIFICATION.md) | 2 | Yes | Submit applications where allowed | **Submit form: always approval** |
| [Communicator](COMMUNICATOR_SPECIFICATION.md) | 3 | No | Handle the inbox loop: who replied, what they want, draft and (if approved) send replies and follow-ups | **Send email/WhatsApp: always approval** |
| [Coach](COACH_SPECIFICATION.md) | 3 | No | Interview preparation and learning guidance | None |
| [Evaluator](EVALUATOR_SPECIFICATION.md) | 4 (gate needed earlier) | Offline | Measure output quality, regressions, replays; gate promotions | None |

## Rules shared by every agent
These come from the approved requirements; they are not restated in every spec.

| Rule | Source |
|---|---|
| Works only through granted tools with a `ToolContext(uid)`; never sees keys, credentials, the raw database or a shell | Requirement 10, [ADR-0006](../decisions/ADR-0006-tenant-isolation.md) |
| Every run records agent version, instructions version, tool grants and model config version | Requirement 1 |
| All model calls go through the gateway; budgets are checked before each call | [ADR-0004](../decisions/ADR-0004-platform-managed-model-keys.md), [ADR-0008](../decisions/ADR-0008-budgets-and-execution-controls.md) |
| Delegation-depth, child and cycle limits; deadlines; max steps | Requirement 3 |
| Stops at the next checkpoint on cancel or kill switch | Requirement 5 |
| Every model call, tool call, delegation, retry, approval, error and final output emits a span | Requirement 9 |
| Email, WhatsApp and application submission always need explicit per-action user approval | Requirement 7 |
| No version becomes production-active without evaluation and explicit approval | Requirement 8 |
| Job postings and web pages are untrusted data. Instructions inside them are ignored (existing `GUARD` in `ranking.py:138`; the README notes lines aimed at AI tools are shown and ignored) | Existing behaviour |
| Never invents facts about the user. Resumes are reorder-only (`tailor_rules`) | Existing behaviour |

## Specification template
Each specification has these 18 sections:
1. purpose;
2. responsibilities;
3. must never;
4. inputs;
5. outputs;
6. tools;
7. tool permissions;
8. model requirements;
9. memory requirements;
10. planning and execution lifecycle;
11. delegation;
12. human approval;
13. errors and retries;
14. execution limits;
15. cost controls;
16. observability;
17. evaluation criteria;
18. acceptance tests.

Sections that can't be specified from the available evidence say **TO BE DEFINED**. Numbers (limits, thresholds) are
proposals.
