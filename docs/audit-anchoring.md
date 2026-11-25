# Audit log: external anchoring (design, not yet implemented)

**Status:** design only. Until this is built and verified, the audit log is *tamper-evident*, not immutable.

## Why
`infra/audit.py` chains every event with an HMAC and keeps a head checkpoint (`audit_meta`). That detects edits,
deletions, reordering and truncation by anyone who does **not** hold a signing key. Someone with a signing key and
write access to MongoDB could rewrite history and produce a chain that verifies. Anchoring copies the head to a place
that person can't change, so any rewrite becomes visible when compared with the anchors.

## Design
1. **What is exported:** `audit.checkpoint()`, i.e. `{seq, hash, at}` plus `key_id`, the deployment name and the export
   time. The anchor is signed with a separate **anchoring key** (Ed25519) that the app server cannot read: it lives in
   the job's own secret store.
2. **Where:** a storage bucket with **object lock in compliance mode** (e.g. AWS S3 Object Lock, Backblaze B2 Object
   Lock, or GCS bucket lock) in an account separate from the app's, with a retention period of at least 400 days. One
   object per export: `anchors/<YYYY>/<MM>/<DD>/<seq>.json`. Optionally also send the head hash to a public
   transparency log (e.g. Sigstore Rekor) for third-party timestamping.
3. **When:** every 15 minutes, and immediately after high-risk events (role changes, model promotions, kill-switch
   changes once those exist). Run by the Phase 1 worker's scheduler under a leader lock, so it runs once per interval.
4. **Credentials:** the export job uses **write-only** credentials (no delete, no overwrite, no retention change). The
   web service doesn't get these credentials.
5. **Verification job (daily, and on demand from the admin console):** for every anchor, check that `audit_log[seq].hash`
   equals the anchored hash, then run `audit.verify()`. Any mismatch raises a critical alert and is recorded.
6. **Failure handling:** if an export fails, alert and retry with backoff. Normal operation is not blocked, but the
   Observatory shows "anchoring overdue".

## Acceptance tests (before the log may be called immutable)
- Rewriting an anchored event, even with the signing key, is detected by the verification job.
- Export credentials cannot delete or overwrite an anchor (the bucket refuses).
- Restoring the database from an older backup is detected (the newest anchor's seq is higher than the head).
- The anchoring signature verifies with the public key published in this repository.

## Crash recovery (implemented)
Writing an event is two steps (`infra/audit.py`):
1. **Claim and insert.** The writer reads the newest event, signs the next event as `seq = newest + 1`, and inserts
   it. A unique index on `seq` lets only one writer succeed for each number; the others get a duplicate-key error
   and retry on the new newest event. The sequence number is claimed *by* the insert, never reserved beforehand.
2. **Move the checkpoint.** `audit_meta.head` is set to the new `(seq, hash)` with a conditional update
   (`seq < new seq`), so it only moves forward.

What a crash (process killed) does:
- **Before the insert:** nothing was stored and no number was reserved, so nothing is missing. The next writer uses
  the same number.
- **After the insert, before the checkpoint:** the event is stored but the checkpoint lags. `verify()` shows the lag.
  It is closed either by the next successful write (which moves the checkpoint past it) or at application start, when
  `ensure_indexes()` runs `repair_head()`. `repair_head()` walks forward from the checkpoint and adopts only events
  whose sequence, previous hash and signature all verify, so it never advances to a forged or broken event and never
  moves backwards.
- Other writers are never blocked: nothing is locked, and a dead writer holds no reservation.

Tested by killing a real writer process (SIGKILL) at both points, alone and while three other processes write
concurrently (`tests/test_step1_fixes.py`).

## Revocation while the audit log is unavailable (implemented)
Removing admin rights must not wait for the audit log. `rbac.revoke()` makes one atomic update to the user's role
document. It sets the role to `user` and pushes the audit event into that document's `pending_audit` list (an
outbox), so the revocation and its record are stored together or not at all. It then writes the event to the audit
log and removes it from the outbox.

If the audit log is unavailable:
- access is already gone: roles are read from the database on every request;
- the event stays in the outbox;
- an operational alert goes out (`audit.alert`: a CRITICAL log line on the `inkwell.alert` logger, the log stream,
  and a Sentry event when Sentry is configured).

`rbac.flush_pending_audit()` writes outbox events in the order they happened. Each carries `outbox_id` and the
original `occurred_at`. It runs at every application start and before every role reconciliation. An event already
in the log (a crash between writing it and clearing the outbox) is found by its `outbox_id` and is not written
twice.

Grants are different: they are audited *first*, and if the audit can't be stored, no access is given.

## Key loss and recovery (applies today)
- **Audit signing keys** (`AUDIT_KEYS`, and the SECRET_KEY-derived `k0`): if a key is lost, events signed with it can
  still be read but can no longer be verified. Store every key that ever signed events offline, separately from
  database backups. Never delete a retired key.
- **Rotation:** add a new `id:secret` to `AUDIT_KEYS`, set `AUDIT_KEY_ID` to it and restart. New events use the new
  key; old events keep their `key_id` and are verified with their original key. Nothing is re-signed.
- **Suspected key compromise:** rotate immediately, export and anchor the current head, and treat events signed with
  the compromised key after the suspected time as unverified.
