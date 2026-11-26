# Communicator specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 3 ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Handle the inbox loop: who replied, what they want, and drafting replies and follow-ups that are sent only if the user approves.

## 2. Responsibilities
- Classify replies; the existing logic is regex-based.
- Track the follow-ups that are due.
- Draft replies and follow-ups.
- Request approval for each send.

## 3. Must never
- Send email or WhatsApp without per-message approval.
- Use WhatsApp on the hosted service (`WHATSAPP_ENABLED=0` in production; it is against WhatsApp's terms).
- Exceed the daily send limit (`DAILY_LIMIT`).

## 4. Inputs
Detected replies (`replies`), the application tracker, the user's templates and profile, and `ToolContext`.

## 5. Outputs
Classifications, draft messages, approval requests, and send outcomes.

## 6. Available tools
`replies.understand` and `draft_reply`, `apps` classification, `tracker.due_followups`, `notify`. Send: the reply send, the queue, `core/sending`.

## 7. Tool permissions
Classification and drafting are `read` / `write_internal`. **Send is `external_action`: approval `always`.**

## 8. Model requirements
- Any active `model_configs` version assigned to this agent, through the gateway.
- Structured JSON output validated against the agent's output schema.
- The provider and model are TO BE DEFINED. The proposal suggests cheaper models for high-volume steps.

## 9. Memory requirements
Thread context through tools. Long-term memory TO BE DEFINED.

## 10. Planning and execution lifecycle
TO BE DEFINED (Phase 3).

## 11. Delegation
May delegate to the Writer.

## 12. Human approval
Always, per message.

## 13. Errors and retries
- Existing SMTP behaviour: one reconnect.
- A sent message whose outcome is unknown is never resent automatically.

## 14. Execution limits
`DAILY_LIMIT`; the existing reply-send rate limit is 60 per hour.

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED.

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`.

## 17. Evaluation criteria
TO BE DEFINED: classification accuracy against user corrections, and draft acceptance rate.

## 18. Acceptance tests
No message is sent without an approval; the daily limit holds; WhatsApp is refused when it is disabled.
