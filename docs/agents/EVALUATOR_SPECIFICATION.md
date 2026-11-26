# Evaluator specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 4 (the promotion gate is needed before any configuration goes production-active) ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Measure the output quality of agents and model configurations, catch regressions, replay historical tasks, and produce the evaluation results that gate promotion. In the product owner's words: technical execution success must not be confused with output quality.

## 2. Responsibilities
- Run evaluation datasets against candidate `agent_versions` and `model_configs`.
- Compare against the active version.
- Replay recorded tasks (shadow traces) with fake external effects.
- Write `evaluation_results`.
- Report pass or fail against thresholds.

## 3. Must never
- Run with real external effects.
- Promote anything itself. Promotion needs an explicit approval (`admin.manage_models`).
- Use other users' content in datasets without a policy that allows it.

## 4. Inputs
A candidate version, a dataset version, and the baseline version.

## 5. Outputs
`evaluation_results`: scores per criterion, pass or fail, dataset version, cost and latency.

## 6. Available tools
Replay harness (NEW), fake providers, the existing deterministic checks (`tailor_rules`, research `validate`, rules gate) used as automatic graders.

## 7. Tool permissions
`read` only, and against evaluation datasets only.

## 8. Model requirements
Grader models, if used, go through the gateway and are themselves versioned.

## 9. Memory requirements
Stores results. It doesn't need agent memory.

## 10. Planning and execution lifecycle
Load the dataset, run the candidate (offline), grade, compare with the baseline, write the result.

## 11. Delegation
None.

## 12. Human approval
Not for running. Promotion after a passing result needs explicit human approval (requirement 8).

## 13. Errors and retries
A failed evaluation run is reported as failed, never as passed.

## 14. Execution limits
TO BE DEFINED.

## 15. Cost controls
Evaluation runs have their own budget, separate from users' budgets (TO BE DEFINED).

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`.

## 17. Evaluation criteria
- Per agent, see each spec's section 17.
- Hard gates: zero added facts (Writer), zero unapproved submissions (Applicant), claim precision (Researcher).
- **Datasets and thresholds: TO BE DEFINED, and a PENDING DECISION.**

## 18. Acceptance tests
- A candidate below threshold can't be marked active.
- A replay produces no external effects.
- Results record the dataset and version ids.
