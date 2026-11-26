# Backup and recovery

**Status:** Partly documented. Most production facts are **UNKNOWN**. · **Last reviewed:** 2026-11-26

## What must be protected

| Asset | Why | Where it lives |
|---|---|---|
| `SECRET_KEY` | Derives every data key. Losing it makes all sealed data unreadable | Render env (`sync: false`); locally `.env` / `data/secret.key` |
| Audit keys (`AUDIT_KEYS`, or `k0` from `SECRET_KEY`) | Losing them means old audit events can't be verified (they remain readable) | Env |
| MongoDB `reachout` | All application data | Atlas |
| Cloudinary objects | Uploaded files (encrypted) | Cloudinary |
| Code | 7 backend commits and the documentation exist only locally | Developer Mac |

## Known backups (local, developer Mac)
- `data/backups/live-20261125-154014`: a BSON dump of the live database, taken before data was copied to live on
  2026-11-25.
- `data/backups/env-before-secret-fix`, `start.command.before-replset`, and an older local copy.
- These are under `data/`, which is git-ignored and not encrypted beyond the sealed fields. Treat them as sensitive.

## Unknown
- Atlas snapshot and backup policy (it depends on the tier).
- Cloudinary retention.
- Whether `SECRET_KEY` and the audit keys are stored anywhere offline.
- Restore procedure: never rehearsed.

## Recommendations (not executed)
1. **OPS-01:** get the 7 unpushed commits and the documentation off the Mac. Options are a `git bundle` kept
   elsewhere, or a push to a branch Render doesn't deploy (verify which branch Render tracks first). This is the
   product owner's decision.
2. **OPS-02:** record where `SECRET_KEY` and the audit keys are kept offline.
3. **OPS-03:** verify the Atlas backup policy as part of Stage 0.1 (read-only).
4. **OPS-04:** rehearse a restore into a non-production database.

The rollback of releases is a separate procedure (runbook Step R) and is never a data restore.
