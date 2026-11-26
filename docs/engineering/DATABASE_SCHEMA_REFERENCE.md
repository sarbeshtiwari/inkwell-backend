# Database schema reference

**Status:** CURRENT IMPLEMENTATION (fields as written by the code at `a83d17e`) · **Last reviewed:** 2026-11-26

MongoDB has no enforced schema. This reference lists the fields the code writes, with the place that writes them.
Fields inside encrypted blobs (`data`, `meta`, `private`, `summary`) are not visible to the database. Their contents
are described only where the code makes them clear.

Overview, relationships and proposed collections: [../architecture/DATABASE_ARCHITECTURE.md](../architecture/DATABASE_ARCHITECTURE.md).

## Core and accounts

| Collection | Fields (type) | Indexes | Written by |
|---|---|---|---|
| `users` | `_id` (uuid hex = uid), `email_hash` (HMAC hex), `data` (sealed `{email, name}`), `created` (epoch float), `last_login` (float or null), `terms_accepted` (`{version, at}` or null), `pw` (`scrypt$n$r$p$salt$hash`), `pw_set` (float) | `email_hash` unique; `created` | `core/accounts.py:new_user` (:89), `set_password` (:121); `routes/auth.py` (`last_login`) |
| `sessions` | `_id` (`lookup_hash("session:"+token)`), `uid`, `created`, `seen` (float), `expire_at` (datetime, +30 days) | `expire_at` TTL 0; `uid` | `core/accounts.py:start_session` (:143), `current_uid` (:154), `end_session` (:169) |
| `otps` | Code document: `_id` (email hash), `purpose` (`login`/`signup`/`delete`), `code_hash`, `data` (sealed), `expires` (float), `attempts` (int), `sent_at`, `expire_at`. Cooldown document: `_id` `cool:<hash>`, `sent_at`, `expire_at` | `expire_at` TTL 0 | `routes/auth.py` (issue :83, cooldown :66, check :94–114) |
| `ratelimits` | `_id` (`lookup_hash("rl:"+key+window+bucket)`), `n` (int), `expire_at` | `expire_at` TTL 0 | `core/security.py:rate_limited` (:71) |
| `roles` | `_id` (uid), `role` (`user`/`super_admin`), `granted_by` (`config:ADMIN_EMAILS`, a uid, or system), `at`, `pending_audit` (array of `{id, action, actor, target, details, occurred_at}`; the audit outbox) | `role`; `pending_audit.id` sparse | `infra/rbac.py` (reconcile :49, revoke :97, grant :194) |
| `audit_log` | `seq` (int), `at`, `actor`, `action`, `target`, `outcome`, `request_id`, `trace_id`, `details` (PII-redacted), `key_id`, `prev_hash`, `hash` (HMAC) | `seq` unique; `(actor, at desc)`; `(action, at desc)`; `details.outbox_id` sparse | `infra/audit.py:record` (write concern majority, `j=True`) |
| `audit_meta` | `_id` `head`, `seq`, `hash`, `at` | — | `infra/audit.py` (`_advance_head`, `repair_head`) |

## Workspace and content

| Collection | Fields | Indexes | Written by |
|---|---|---|---|
| `kv` | `_id` (`<uid>:<key>`), `uid`, `key`, `data` (sealed value). Versioned keys add `version` (int64) and `wid` (32 hex) **[a83d17e]** | `uid` | `core/workspace.py`: `save` (:121, via `guarded_write`), `update` (:151), `create` (:207), `_insert` (:198) |
| `send_log` | `uid`, `rid`, `day` (ISO date), `ts`, `data` (sealed `{timestamp, channel, to, name, status, detail, preview, source?}`), optional `track`, `opens`, `first_open`, `last_open`, `mid` (hash of Message-ID), `to_hash`, `replied_at` | `(uid, day)`; `(uid, rid, ts desc)`; `(uid, ts desc)`; `track` sparse | `Workspace.log` (:229); `core/bounces.py:166`; `routes/site_pages.py` (open pixel); `features/replies.py:450` |
| `documents` | `uid`, `name_hash`, `size`, `meta` (sealed `{name}`), `file_id` (GridFS id or `{cld: [...], size}`), `created` | `(uid, name_hash)` unique | `Workspace.save_doc` (:325), `delete_doc` (:334) |
| `files.files` / `files.chunks` | GridFS standard fields; `filename` (uuid hex), `metadata` `{uid, kind}` (`document`/`whatsapp`) | GridFS defaults | `core/storage.py:put_file` |
| `wa_sessions` | `_id` (uid), `file_id`, `created`, `updated` | none found | `core/whatsapp_store.py:store_wa_blob` (:57) |

## Features

| Collection | Fields | Indexes | Written by |
|---|---|---|---|
| `leads` | `_id` (12 hex), `created`, `status`, `data` (sealed enquiry), `uid` (portfolio enquiries only) | `created desc`; `(uid, created desc)` sparse | `routes/leads.py` (:75, :119, :126); `features/site.py:961` |
| `notifications` | `uid`, `at`, `read` (bool), `data` (sealed `{title, body, link, kind}`), `expire_at` (+90 days) | `(uid, at desc)`; `expire_at` TTL 0 | `features/notify.py:notify` (:70) |
| `push_subs` | `_id` (`lookup_hash("push:"+endpoint)`), `uid`, `created`, `data` (sealed `{endpoint, keys}`) | `uid` | `features/notify.py` (:219, :230) |
| `mail_index` | `uid`, `mid` (sha256 prefix), `ts`, `key` (HMAC of company key), `cat`, `data` (sealed `{company, from, addr, subject, …}`) | `(uid, mid)` unique; `(uid, key, ts desc)`; `(uid, ts desc)` | `features/inbox.py` (:236, :277) |
| `replies` | `uid`, `mid` (HMAC), `rid`, `ts`, `auto` (bool), `handled` (bool), `intent`, `data` (sealed) | `(uid, ts desc)`; `(uid, mid)` unique; `(uid, rid)` | `features/replies.py` (:452, :557, :827) |
| `queue` | `_id` (12 hex), `uid`, `kind`, `due`, `status` (`queued`/`sending`/`sent`/`failed`/`cancelled`), `created`, `attempts`, `data` (sealed payload), `summary` (sealed), `claimed`, `detail`, `sent_at` | `uid`; `(status, due)` | `features/jobs.py` (`queue_doc` :627, `run_queue` :806) |
| `site` | `portfolio-slug:<slug>` → `{uid}`; `vapid` → `{data}`; `linkedin` → `{data: sealed {client_id, client_secret}}` | `_id` | `features/portfolio.py:40`; `features/notify.py:111`; `features/linkedin.py:76` |
| `site_images` | `_id` (uuid hex), `uid`, `type` (MIME), `created`, then either `url` + `public_id` (Cloudinary) or `data` (sealed base64) | `uid` | `features/site.py:868` |
| `site_stats` | `_id` (`<uid>:<date>`), `uid`, `day`, `views` (int) | `uid` | `features/site.py:903` |
| `search` | **UNKNOWN**: no writer found; listed only in `USER_COLLECTIONS` | — | — |

## Coordination **[a83d17e]**

| Collection | Fields | Indexes | Written by |
|---|---|---|---|
| `locks` | `_id` (`u:<uid>:<resource>` or `g:<resource>`), `uid`, `resource`, `token`, `owner`, `fence` (int64, only grows), `acquired_at`, `renewed_at`, `expires_at`, `released_at`, `last_write_at` | `(uid, resource)` | `infra/locks.py` (`_take` :207–220, renew :170, release :188, `guarded_write` :298) |
| `operations` | `_id` (uuid hex), `uid`, `kind`, `key`, `state`, `public` (plain, non-personal), `private` (sealed), `version`, `lock_token`, `fence`, `cancel_requested`, `cancel_requested_at`, `error`, `started_at`, `updated_at`, `finished_at`, `expire_at` | `(uid, kind, key, fence desc)`; `(state, updated_at)`; `expire_at` TTL 0 | `infra/operations.py` (`start` :206, `cancel` :289) |
| `schedules` | `_id` (`g:<name>` or `u:<uid>:<name>`), `next_at`, `last_at`, `uid`, `expire_at` (`once` markers) | `uid` sparse; `expire_at` TTL 0 | `infra/schedule.py` (`claim`, `soon`, `postpone`, `once`) |

## Index creation
- **Core collections:** created in `init_storage` (`core/accounts.py:58–81`), which also calls `ensure_indexes()` for
  locks, operations, schedule, audit and rbac.
- **Feature collections:** created at import-time startup by `features/notify.py:init`, `features/inbox.py:ensure_indexes`,
  `features/replies.py:init` (`app.py:78–80`) and `features/jobs.py:start_workers`.
