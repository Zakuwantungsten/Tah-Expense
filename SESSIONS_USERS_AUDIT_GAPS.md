# Sessions, user admin actions, and remaining audit gaps

Implementation plan for:

1. Live **login sessions** (view + force logout) in Admin Security
2. Full **user admin actions** (ban, deactivate, force logout, delete, reset password)
3. **Remaining audit gaps** (reconciliation, transactions, categories/rules, etc.)

Do **not** edit this file as code; implement against it in ordered phases below.

---

## Current state (what already exists)

| Capability | Status | Where |
|------------|--------|--------|
| Activate / deactivate user | Partial | `PATCH /v1/users/{id}` + Users tab toggle; deactivating already revokes sessions when `active=false` |
| Reset password | Partial | Edit dialog / `PATCH` with `password`; revokes other sessions on self change-password; admin reset revokes all that user’s sessions |
| Delete user | **Missing** | No `DELETE /v1/users/{id}` |
| Ban (distinct from inactive) | **Missing** | Only `active` boolean |
| List sessions | **Missing** | `auth_sessions` stored but no list/revoke API or UI |
| Force logout one / all | **Missing** as admin action | Logout only revokes own session; password change / deactivate revoke as side effects |
| Session policy settings | **Missing** | Refresh lifetime is env `REFRESH_TOKEN_DAYS` only |
| Audit: auth / users / settings | Done | `auth.*`, `user.*`, `settings.update` + Audit Log tab |
| Audit: accountant / imports / feeds / attachments | Partial | Server `_audit` helpers in those modules |
| Audit: reconciliation, transactions, categories/rules/fleet/people/diesel/backup | **Done** (Phase 3) | |

Session document shape today ([`issue_session`](backend/app/api.py)):

```text
_id, user_id, refresh_hash, created_at, last_used_at, expires_at,
revoked_at, user_agent, ip
```

JWT access tokens carry `sid` = session `_id`. Revoking a session makes the next authenticated request fail with `session_revoked` (already enforced in [`dependencies.current_user`](backend/app/dependencies.py)).

---

## Phase 1 — Session APIs and Security “Sessions” UI

**Status: implemented** (APIs, Security tab sessions panel, max/idle session settings, tests).

### 1.1 Backend endpoints (admin only)

Add router under `/v1/sessions` (or `/v1/auth/sessions`):

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/v1/sessions` | List active (and optionally recent revoked) sessions. Query: `user_id`, `active_only=true`, `limit` |
| `POST` | `/v1/sessions/{session_id}/revoke` | Force-logout one session (`revoked_at=now`) |
| `POST` | `/v1/sessions/revoke-user/{user_id}` | Force-logout **all** active sessions for a user |
| `POST` | `/v1/sessions/revoke-all` | Emergency: revoke every active session except caller’s (optional confirm header) |

Response fields (never return `refresh_hash`):

```json
{
  "id": "...",
  "user_id": "...",
  "username": "...",
  "full_name": "...",
  "role": "...",
  "ip": "...",
  "user_agent": "...",
  "created_at": "...",
  "last_used_at": "...",
  "expires_at": "...",
  "revoked_at": null,
  "is_current": false
}
```

Join `users` for username/role. Mark `is_current` when `session_id == request.state.session._id`.

**Guards**

- Admin only (`Admin` dependency).
- Cannot revoke the **last active admin’s only session** in a way that locks all admins out without bypass — prefer: allow revoke but never deactivate/delete last admin (sessions can be revoked; admin can log in again). Exception: `revoke-all` must keep **caller’s** session.
- Audit every revoke: `session.revoke`, `session.revoke_user`, `session.revoke_all`.

### 1.2 Enrich sessions on login / refresh

On successful refresh, update `last_used_at` (already done). Optionally store `X-Device-Mac` on the session document at login (`device_mac`) so the Sessions table can show device.

### 1.3 Admin Security tab — Sessions section

In [`tahmeed/ui/admin/security_tab.py`](tahmeed/ui/admin/security_tab.py) (or a nested `SessionsPanel`):

- Table: User, Role, IP, Device MAC, User-Agent (short), Created, Last used, Status
- Actions per row: **Force logout**
- Toolbar: Refresh, filter by user, **Force logout all for selected user**
- Confirm dialogs for destructive actions
- Note: access JWT may remain valid until expiry (≤ `ACCESS_TOKEN_MINUTES`, default 15); refresh is killed immediately. Document this in the UI help text.

### 1.4 Session-related security settings

Add to `system_settings` (admin-only writes, already gated by `security_*` / new `session_*` prefix):

| Key | Default | Purpose |
|-----|---------|---------|
| `security_max_sessions_per_user` | `0` (unlimited) | On login, if active sessions ≥ N, revoke oldest before issuing new |
| `security_idle_session_days` | `0` (off) | Scheduler or login check: revoke sessions with `last_used_at` older than N days |
| `security_show_sessions_in_security_tab` | `true` | UI toggle only |

Wire `security_max_sessions_per_user` inside `issue_session`. Idle prune can run inside the backup/maintenance scheduler tick or on login.

---

## Phase 2 — User admin actions (ban, delete, force logout, reset password)

**Status: implemented** (APIs, Users tab Actions menu, login/current_user guards, tests).

### 2.1 Semantics

| Action | Effect | Login allowed? | Sessions |
|--------|--------|----------------|----------|
| **Activate** | `active=true`, clear ban fields | Yes | Unchanged |
| **Deactivate** | `active=false` | No | Revoke all |
| **Ban** | `active=false`, `banned=true`, `banned_at`, `ban_reason` | No (message: banned) | Revoke all |
| **Unban** | `banned=false`, optionally leave inactive until re-activated | Depends on `active` | Unchanged |
| **Force logout** | No user doc change | Yes (if active) | Revoke all (or one) |
| **Reset password** | New hash | Yes (if active) | Revoke all |
| **Delete** | Soft-delete preferred | No | Revoke all |

### 2.2 User document fields to add

```text
banned: bool = false
banned_at: datetime | null
banned_by: ObjectId | null
ban_reason: str = ""
deleted_at: datetime | null   # soft delete
```

Login / `current_user` checks:

1. Reject if `deleted_at` set → `user_deleted`
2. Reject if `banned` → `user_banned` (include reason in message, not details to other users)
3. Reject if not `active` → `user_inactive` (existing)

### 2.3 API

| Method | Path | Notes |
|--------|------|-------|
| `PATCH` | `/v1/users/{id}` | Extend with `banned`, `ban_reason` (admin only for ban fields) |
| `POST` | `/v1/users/{id}/ban` | Body: `{ "reason": "..." }` — sets banned + inactive + revoke sessions |
| `POST` | `/v1/users/{id}/unban` | Clears ban; leaves `active` false until explicit activate (safer) |
| `POST` | `/v1/users/{id}/force-logout` | Alias of revoke-all sessions for that user |
| `POST` | `/v1/users/{id}/reset-password` | Body: `{ "password": "..." }` — policy check + revoke sessions (clearer than overloaded PATCH) |
| `DELETE` | `/v1/users/{id}` | Soft-delete: `deleted_at=now`, `active=false`, revoke sessions |

**Guards (all mutations)**

- Only **admin** may ban / delete / force-logout / reset another **admin**.
- Accountants may continue to manage cashier/accountant accounts for create/update/activate (existing rules); **ban / delete / force-logout-all / reset-password** = **admin only** (recommended for this phase).
- Cannot ban/deactivate/delete the **last active admin**.
- Cannot delete yourself (must transfer admin first).
- Soft-deleted users omitted from `GET /v1/users` by default; `?include_deleted=1` for admin recovery later (optional).

### 2.4 Audit actions

| Action string | When |
|---------------|------|
| `user.ban` | Ban |
| `user.unban` | Unban |
| `user.deactivate` / `user.activate` | Toggle active (prefer explicit over generic `user.update` when only active changes) |
| `user.force_logout` | Force logout |
| `user.reset_password` | Admin reset |
| `user.delete` | Soft delete |
| `session.revoke` / `session.revoke_user` | Session UI |

### 2.5 Desktop Users tab

Enhance [`tahmeed/ui/admin/users_tab.py`](tahmeed/ui/admin/users_tab.py):

- Status column: Active / Inactive / **Banned** / Deleted
- Actions menu (or buttons): Edit, Reset password, Activate/Deactivate, **Ban…**, **Force logout**, **Delete…**
- Ban dialog: required reason (min length)
- Delete: type username to confirm
- Force logout: confirm; call sessions/user endpoint
- Keep password policy validation aligned with Security settings (min length / complexity)

Desktop service helpers in [`tahmeed/services/user_service.py`](tahmeed/services/user_service.py): `ban_user`, `unban_user`, `force_logout_user`, `delete_user`, `reset_password` (dedicated).

---

## Phase 3 — Remaining audit gaps — DONE

Goal: every **mutating** API writes one `operation_events` row via [`record_operation_event`](backend/app/audit_ops.py) (server-side; do not rely on desktop `record_event` alone).

### 3.1 Priority order (completed)

1. **Auth / sessions / users** — covered by Phases 1–2  
2. **`transactions_api.py`** — create/update/delete, request-delete, discard, restore, resubmit, submit-day, drafts, pending-edit  
3. **`reconciliation_api.py`** — upload delete, save rows, stations, status patches  
4. **Categories / subtables / rules / mappings** in [`api.py`](backend/app/api.py) — `category.*`, `subtable.*`, `rule.*`, `mapping.*`  
5. **Fleet / people** — `fleet.upsert` / `fleet.delete`, `people.upsert` / `people.delete`  
6. **Diesel cash item config** — `diesel.items_update`; **backup restore** — `backup.restore` (alongside `restore_audit`)

### 3.2 Action naming convention

```text
{domain}.{verb}           e.g. txn.delete, category.create, rule.patch
{domain}.{verb}_bulk      e.g. txn.trash_bulk
outcome: ok | fail
entity_type + entity_ids
details: small JSON (ids counts, not full PII/passwords)
```

### 3.3 Implementation pattern

```python
await record_operation_event(
    db,
    action="txn.delete",
    actor=user,
    request=request,
    entity_type="transaction",
    entity_ids=[str(oid)],
    details={"reason": "..."},
)
```

Wrap in the existing best-effort helper (never fail the business write if audit insert fails). Prefer **one** server write; remove redundant desktop `record_event` for the same action once server covers it (or keep desktop only for offline-era paths — prefer server).

### 3.4 Audit Log UI follow-ups (light) — DONE

- Preset filter chips: All / Auth / Users / Transactions / Settings  
- Export CSV of the filtered view (admin Audit Log)  
- Deep-link from Users → “Sessions for this user…” / “Audit for this user…”

---

## Phase 4 — Tests and rollout

### Tests

- List sessions as admin; accountant gets 403  
- Revoke one session → subsequent request with that JWT/`sid` → 401 `session_revoked`  
- Force logout user → all sessions revoked; new login works  
- Ban → login returns banned; sessions cleared  
- Soft delete → omitted from list; login rejected  
- Last-admin protections  
- Audit rows for ban / revoke / delete  
- Transaction + reconciliation audit smoke tests (one happy path each)

### Rollout

1. Deploy API with session + user endpoints (no UI yet) — verify with curl  
2. Ship desktop Security Sessions panel + Users actions  
3. Enable max-sessions policy only after confirming office workflows  
4. Land audit gap patches module-by-module (transactions → reconciliation → catalog)

### Ops notes

- Force logout is **not** instantaneous for in-flight access tokens (up to 15 minutes). Lower `ACCESS_TOKEN_MINUTES` if tighter kill is required.  
- `TAHMEED_SECURITY_BYPASS` does not restore revoked sessions; it only skips IP/MAC/lockout.  
- Soft-deleted users remain in Mongo for referential integrity of historical `actor_id` on audit rows.

---

## Suggested file touch list

| Area | Files |
|------|--------|
| Session API | New `backend/app/sessions_api.py`; include in `api.py` |
| User ban/delete | [`backend/app/api.py`](backend/app/api.py), [`backend/app/schemas.py`](backend/app/schemas.py) |
| Audit helper usage | [`backend/app/audit_ops.py`](backend/app/audit_ops.py), `transactions_api.py`, `reconciliation_api.py`, category/rule routes |
| Security UI | [`tahmeed/ui/admin/security_tab.py`](tahmeed/ui/admin/security_tab.py) |
| Users UI | [`tahmeed/ui/admin/users_tab.py`](tahmeed/ui/admin/users_tab.py) |
| Desktop services | [`tahmeed/services/user_service.py`](tahmeed/services/user_service.py), new `session_service.py` |
| Docs | This file + short section in [`SECURITY_IMPLEMENTATION.md`](SECURITY_IMPLEMENTATION.md) |
| Tests | `backend/tests/test_sessions_api.py`, extend `test_api_correctness.py` / `test_audit_api.py` |

---

## Implementation order (when coding starts)

1. Session list + revoke APIs + audit events  
2. Security tab Sessions panel + force logout  
3. User ban / force-logout / delete / dedicated reset-password APIs + Users tab actions  
4. Session policy settings (`max_sessions`, idle days)  
5. Audit gaps: transactions → reconciliation → categories/rules → fleet/people  
6. Tests + SECURITY_IMPLEMENTATION.md cross-links  

Audit Log tab already exists; new events will show up there automatically once written.
