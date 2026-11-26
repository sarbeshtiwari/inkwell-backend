# ADR-0002: LangGraph for durable agent execution

- **Status:** Approved in direction
  - It was recommended in `inkwell-agentic-architecture.md`, whose overall direction was approved on 2026-11-25.
  - It has not been decided on its own.
- **Date:** 2026-11-25
- **Implemented:** No. `langgraph` is not in `requirements.txt`.

## Context
Agents must keep execution state, resume after a crash, pause for human approval, and delegate to sub-agents. The
backend already depends on `langchain-core==1.6.6` and `langchain-ollama==1.1.0`. Today `langchain-core` is used only
to chain functions; there is no agent runtime.

## Decision (as proposed)
- Use LangGraph graphs per agent, with a MongoDB checkpointer.
- Use `interrupt()` for human approval, and sub-graphs or child tasks for delegation.
- Model calls go through Inkwell's own model gateway, never straight to providers
  ([ADR-0004](ADR-0004-platform-managed-model-keys.md)).

## Alternatives considered (from the proposal)
- **CrewAI or AutoGen:** less control over state, approvals and tracing.
- **A hand-written loop:** checkpointing and interrupts would have to be rebuilt.

## Consequences
- New dependencies: `langgraph`, plus a MongoDB checkpointer package (the proposal names
  `langgraph-checkpoint-mongodb`). **TO BE DEFINED:** package versions, compatibility with `langchain-core` 1.6.6
  and `pymongo` 4.18.2, and whether the checkpointer supports the transactional guarantees Inkwell needs. The first
  Phase 1 spike should check this (task PH1-07 in the
  [master task register](../project/MASTER_TASK_REGISTER.md)).
- Checkpoints hold user content. They must be encrypted or access-controlled like other user data
  ([../engineering/TENANT_ISOLATION.md](../engineering/TENANT_ISOLATION.md)).

## Related
[../architecture/AGENT_EXECUTION_LIFECYCLE.md](../architecture/AGENT_EXECUTION_LIFECYCLE.md),
[ADR-0003](ADR-0003-separate-agent-worker.md)
