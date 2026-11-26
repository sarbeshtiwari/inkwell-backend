# ADR-0010: "Find and Prepare Applications" first, in shadow mode

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** No

## Decision
The product owner's wording:

> "Proceed with Find and Prepare Applications. […] Orchestrator → Job Scout → Job Analyst → Researcher (when needed)
> → Writer → Applicant (human approval required before submission). Start with shadow execution, compare results
> against the existing implementation, then enable it for my account before considering wider rollout."

## Rollout stages
1. **Shadow:** the agent workflow runs beside the existing AI job match. It performs no external actions and doesn't
   change user-visible state. Its outputs are stored and compared.
2. **On for the product owner's account only.**
3. **Wider rollout:** a separate decision.

The proposal calls for a feature flag per workflow (`off` / `shadow` / `on`, per user). It's **not implemented**, and
how the flag is stored is TO BE DEFINED.

## Related
[../architecture/AGENT_EXECUTION_LIFECYCLE.md](../architecture/AGENT_EXECUTION_LIFECYCLE.md) (the workflow end to
end), [../agents/README.md](../agents/README.md)
