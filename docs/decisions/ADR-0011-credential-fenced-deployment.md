# ADR-0011: Credential-fenced production deployments

- **Status:** Accepted (documentation only), 2026-11-26.
  - Executing any part is **not approved**: Stage 0.2, 0.5, 0.6 and production credential fencing.
  - The first migration from `8b59986` to `a83d17e` is **blocked**.
- **Date:** 2026-11-26
- **Implemented:** Not executed

## Context
- Render overlaps the old and new instance during a deploy. The old instance's background loops keep writing.
- `8b59986` coordinates in process memory and `a83d17e` in MongoDB, so running them together is unsafe
  ([../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md](../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md)).

## Decision (as documented and accepted)
Every deploy is sequential until a soft gate (Stage 3) and a compatibility declaration (Stage 5) exist and are tested:
1. close HTTP ingress;
2. stop the old instance, **with termination evidence**;
3. *fence* its database user (no write role), and record the fence evidence;
4. only then let the new release write.

The full definitions are in the five deployment documents. This ADR doesn't restate or reinterpret them:
- [RENDER_FREE_DEPLOYMENT_RUNBOOK.md](../deployment/RENDER_FREE_DEPLOYMENT_RUNBOOK.md)
- [DEPLOYMENT_GATE_ARCHITECTURE.md](../deployment/DEPLOYMENT_GATE_ARCHITECTURE.md)
- [DEPLOYMENT_WRITE_PATH_INVENTORY.md](../deployment/DEPLOYMENT_WRITE_PATH_INVENTORY.md)
- [DEPLOYMENT_FAILURE_ANALYSIS.md](../deployment/DEPLOYMENT_FAILURE_ANALYSIS.md)
- [DEPLOYMENT_COMPATIBILITY_MATRIX.md](../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md)

## The blocker
- Stage 0.5 needs termination evidence: a post-exit record that Stage 0.5 accepts.
- A liveness observation never qualifies. That includes zero database connections, Render's Suspended status, or
  elapsed time.
- Results from the fake-provider tests don't qualify either.
- No such evidence source is known today, so Stage 0.5 and the first migration are **BLOCKED**.

## Alternatives considered
- **Concurrent (overlapping) deploys.** Unsafe for `8b59986` → `a83d17e`.
- **Deploying without fencing.** Not accepted.

## Consequences
- Production stays on `8b59986` until the blocker is resolved. The only ways out are a product-owner decision or new
  evidence from Render.
- Agent code developed locally doesn't depend on this ADR. The worker service will need its own fencing treatment.

## Related
[../operations/DEPLOYMENT_STRATEGY.md](../operations/DEPLOYMENT_STRATEGY.md)
