# ADR-0007: Versioned agent and model configurations, evaluated before promotion

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** No

## Decision
The product owner's wording (mandatory requirements 1, 2 and 8):

> "Record the exact agent version, instructions version, tool configuration and model configuration used in every
> execution."

> "Implement measurable quality evaluations, regression tests and historical task replay. Technical execution success
> must not be confused with actual output quality."

> "No new model or agent configuration should become production-active without evaluation and explicit approval."

## Design (proposed)
- `agent_versions` and `model_configs` documents are immutable once published.
- Each run records:
  - `agent_id`, `agent_version`, the instructions version, the tool grant set, `model_config_version`;
  - the model id the provider actually returned.
- Status lifecycle: `candidate` → `approved` → `active` → `retired`.
- Promoting to `active` needs a passing evaluation result for that version and an audited approval by a holder of
  `admin.manage_models`. That permission already exists in `infra/rbac.py`.

## Consequences
The evaluation datasets and thresholds are **TO BE DEFINED**
([../agents/EVALUATOR_SPECIFICATION.md](../agents/EVALUATOR_SPECIFICATION.md)).

## Related
[../architecture/MODEL_GATEWAY_ARCHITECTURE.md](../architecture/MODEL_GATEWAY_ARCHITECTURE.md)
