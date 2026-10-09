# API-only migration — desktop → `https://api.meedexpe.com`

**Goal:** Desktop talks only to the HTTPS API. MongoDB stays on the server (`127.0.0.1`). Users need no Tailscale and no local config. Cloudflare Tunnel exposes the API at [`https://api.meedexpe.com`](https://api.meedexpe.com) (domain [`meedexpe.com`](http://www.meedexpe.com/)).

**Rule:** Do not start a major phase until the previous phase’s acceptance checks pass. Within a phase, finish one **sub-phase**, stop for inspection, then continue.

**Build bake (already done):** production `API_BASE_URL=https://api.meedexpe.com`. Phase 11 removes desktop `MONGODB_URI` from production builds.

---

## Architecture target

```
Desktop installer (baked API URL)
        │  HTTPS
        ▼
https://api.meedexpe.com          ← Cloudflare Tunnel (cloudflared)
        │
        ▼
API container / process           ← 127.0.0.1:8000 on server
        │
        ▼
MongoDB                           ← 127.0.0.1:27017 (not public)
```

---

## Conventions (every new endpoint)

| Rule | Detail |
|------|--------|
| Prefix | All app routes under `/v1/...` (health stays `/health/live`, `/health/ready`) |
| Auth | Bearer access token unless marked `auth: false` |
| Roles | `admin`, `accountant`, `cashier` as today; manager-only = `admin` + `accountant` |
| IDs | Path params are Mongo ObjectId hex strings |
| Pagination | Prefer `{ items, total, limit, skip }` (or existing list+count pair) |
| Errors | Existing `{ error: { code, message, details, request_id } }` shape |
| JSON | Use `json_safe` / ISO datetimes; never leak `password_hash` |
| Desktop | Keep service function signatures where possible; swap `get_db()` for `api_client.request(...)` |
| Attachments | Multipart upload/download; binary never stored only on the client disk long-term |

---

## Baseline — already on the API (no phase work)

Desktop services already use `api_client`. Keep stable; only fix gaps if a caller still hits Mongo.

### Auth — `/v1/auth`

| Operation | Method | Path | Auth |
|-----------|--------|------|------|
| Login | `POST` | `/v1/auth/login` | no |
| Bootstrap status | `GET` | `/v1/auth/bootstrap-status` | no |
| Bootstrap first admin | `POST` | `/v1/auth/bootstrap` | no |
| Refresh | `POST` | `/v1/auth/refresh` | refresh body |
| Logout | `POST` | `/v1/auth/logout` | yes |
| Current user | `GET` | `/v1/auth/me` | yes |
| Change password | `POST` | `/v1/auth/change-password` | yes |

### Users — `/v1/users`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List | `GET` | `/v1/users` | manager |
| Create | `POST` | `/v1/users` | manager |
| Update | `PATCH` | `/v1/users/{user_id}` | manager |

### Categories — `/v1/categories`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List / filter | `GET` | `/v1/categories` | authenticated |
| Create | `POST` | `/v1/categories` | manager |
| Cashier create | `POST` | `/v1/categories/cashier-create` | cashier+ |
| Patch | `PATCH` | `/v1/categories/{item_id}` | manager |
| Delete | `DELETE` | `/v1/categories/{item_id}` | manager |

### Subtables — `/v1/subtables`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List | `GET` | `/v1/subtables` | authenticated |
| Create | `POST` | `/v1/subtables` | manager |
| Cashier create | `POST` | `/v1/subtables/cashier-create` | cashier+ |
| Patch | `PATCH` | `/v1/subtables/{item_id}` | manager |
| Delete | `DELETE` | `/v1/subtables/{item_id}` | manager |

### Keyword rules — `/v1/keyword-rules`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List | `GET` | `/v1/keyword-rules` | authenticated |
| Create | `POST` | `/v1/keyword-rules` | manager |
| Patch | `PATCH` | `/v1/keyword-rules/{item_id}` | manager |
| Delete | `DELETE` | `/v1/keyword-rules/{item_id}` | manager |

### Description mappings — `/v1/description-mappings`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List | `GET` | `/v1/description-mappings` | authenticated |
| Upsert | `PUT` | `/v1/description-mappings` | authenticated |
| Delete one | `DELETE` | `/v1/description-mappings/{item_id}` | manager |
| Delete all | `DELETE` | `/v1/description-mappings` | manager |

### Fleet — `/v1/trucks`, `/v1/trailers`, `/v1/motor_vehicles`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List | `GET` | `/v1/{kind}` | authenticated |
| Upsert | `PUT` | `/v1/{kind}/{number}` | manager |
| Delete | `DELETE` | `/v1/{kind}/{number}` | manager |

### People — `/v1/people`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List | `GET` | `/v1/people` | authenticated |
| Upsert | `PUT` | `/v1/people/{name}` | manager |
| Delete | `DELETE` | `/v1/people/{name}` | manager |

### Backups — `/v1/backups`

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| List jobs | `GET` | `/v1/backups` | manager |
| Restore | `POST` | `/v1/backups/restore` | manager |

### Accountant (partial)

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| Notification counts | `GET` | `/v1/accountant/notification-counts` | manager |

### Health

| Operation | Method | Path | Auth |
|-----------|--------|------|------|
| Liveness | `GET` | `/health/live` | no |
| Readiness | `GET` | `/health/ready` | no |

---

## Gap inventory (still uses desktop `get_db()`)

| Desktop module | Mongo collections | Phase |
|----------------|-------------------|-------|
| ~~`settings_service.py`~~ | `system_settings` | 1 — done |
| ~~`audit_service.py`~~ | `operation_events` | 1 — done |
| ~~`cashier_service.py`~~ | `transactions` | 2 — done |
| ~~`accountant_service.py` (verify / reject / deletion / trash)~~ | `transactions`, `users` | 3 — done |
| `accountant_service.py` (master / reports / diesel cash / overview) | `transactions`, `users`, `categories` | 4 |
| `attachment_service.py` | `transactions.attachments` + local files | 5 |
| `reconciliation_service.py` | `reconciliation_entries`, `recon_stations` | 6 |
| `daily_import_service.py` | `transactions`, `import_idempotency` | 7 |
| `master_import_service.py` | `transactions`, `import_idempotency` | 7 |
| `accountant_service.py` (feeds / uploads / skipped) | `imported_feeds`, `separate_expenses`, `skipped_import_rows`, `import_idempotency` | 8 |
| `accountant_service.py` (truck / fuel overview) | aggregates across above | 9 |
| `chart_of_accounts_service.py` | `categories`, `category_subtables`, `keyword_rules` | 10 |
| `connectivity_service.py` (`_probe_mongo`) | direct Mongo ping | 11 |
| `tahmeed/db/connection.py` | client bootstrap | 11 |

---

## Phase 1 — System settings + audit

**Why first:** Small surface; settings are read by cashier/accountant UI; audit should record API-side actions.

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **1.1** | Backend `/v1/settings` endpoints + tests | Done |
| **1.2** | Desktop `settings_service.py` → `api_client` | Done |
| **1.3** | Backend `/v1/audit` endpoints + tests | Done |
| **1.4** | Desktop `audit_service.py` → `api_client` | Done |
| **1.5** | Phase 1 acceptance checklist | **Done** |

### Backend endpoints

#### Settings — `/v1/settings`

| Desktop function | Method | Path | Role | Notes |
|------------------|--------|------|------|-------|
| `get_all_settings` | `GET` | `/v1/settings` | authenticated | Merge DB values over `DEFAULTS` |
| `get_setting` | `GET` | `/v1/settings/{key}` | authenticated | 404 if unknown key and no default |
| `set_setting` | `PUT` | `/v1/settings/{key}` | authenticated | Body: `{ "value": ... }` (cashiers write labels too) |

Known keys (from `DEFAULTS`): `default_currency`, `confidence_threshold`, `restrict_items`, `defer_item_to_verify`, `restrict_trucks`, `duplicate_check_days`, `allowed_truck_labels`, `diesel_cash_items`, `export_restrict_surfaces`.

#### Audit — `/v1/audit`

| Desktop function | Method | Path | Role | Notes |
|------------------|--------|------|------|-------|
| `record_event` | `POST` | `/v1/audit/events` | authenticated | Body mirrors current event fields; server stamps `created_at` / actor |

### Desktop work

- Rewrite `settings_service.py` and `audit_service.py` to `api_client`.
- Any UI that called them keeps the same Python API.

### Acceptance

- [x] All settings CRUD round-trips via API with Mongo only on the server.
- [x] Audit event insert works authenticated.
- [x] No `get_db()` left in those two modules.
- [x] Backend tests: `tests/test_settings_api.py` + `tests/test_audit_api.py` (13 passed).
- [x] Local dev uses loopback Mongo (`tahmeed_expense_dev`) — see `LOCAL_DEV.md`.

---

## Phase 2 — Cashier transactions + register

**Collection:** `transactions`  
**Router:** `/v1/transactions` (+ cashier-specific actions)

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **2.1** | Backend core CRUD: create, get-by-date, get-by-id(s), patch, delete + tests | Done |
| **2.2** | Backend register workflow: submit-day, day-order, request-delete, pending-edit, duplicates | Done |
| **2.3** | Backend browse / overview / meta / descriptions | Done |
| **2.4** | Backend rejected / discarded / drafts | Done |
| **2.5** | Desktop `cashier_service` → API for core CRUD (2.1) | Done |
| **2.6** | Desktop → API for register workflow (2.2) | Done |
| **2.7** | Desktop → API for browse + rejected/drafts (2.3–2.4) | Done |
| **2.8** | Phase 2 acceptance (no `get_db()` in `cashier_service`) | **Done** |

### Core CRUD

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `save_transaction` | `POST` | `/v1/transactions` | cashier+ |
| `update_transaction` | `PATCH` | `/v1/transactions/{tx_id}` | cashier+ |
| `delete_transaction` | `DELETE` | `/v1/transactions/{tx_id}` | cashier+ (draft/unsubmitted only; else use request-delete) |
| `get_transactions_by_ids` (shared) | `GET` | `/v1/transactions?ids=...` | authenticated |
| `get_transactions_by_date` | `GET` | `/v1/transactions/by-date?date=YYYY-MM-DD&cashier_id=` | cashier+ |
| `search_transactions` | `GET` | `/v1/transactions/search?...` | cashier+ |
| `get_transactions_by_category` | `GET` | `/v1/transactions/by-category?category=&...` | authenticated |
| `get_transactions_flat` | `GET` | `/v1/transactions/flat?...` | authenticated |

### Register workflow

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `submit_day_for_verify` | `POST` | `/v1/transactions/submit-day` | cashier | Body: `{ "date": "YYYY-MM-DD" }` → `{ "updated": N }` |
| `recount_day_order` | `POST` | `/v1/transactions/day-order` | cashier | Body: `{ "date", "ordered_ids": [...] }` |
| `next_day_order` | `GET` | `/v1/transactions/next-day-order?date=` | cashier |
| `request_or_delete_transaction` | `POST` | `/v1/transactions/{tx_id}/request-delete` | cashier | Soft delete-request vs hard delete for drafts |
| `insert_pending_edit` | `POST` | `/v1/transactions/{tx_id}/pending-edit` | cashier | Creates/refreshes pending-edit doc |
| `check_for_duplicates` | `POST` | `/v1/transactions/check-duplicates` | cashier | Body: candidate fields |

### Overview / browse helpers

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_overview_stats` | `GET` | `/v1/transactions/overview-stats` | cashier+ |
| `get_daily_summaries` | `GET` | `/v1/transactions/daily-summaries?...` | cashier+ |
| `get_browse_items` | `GET` | `/v1/transactions/browse/items?...` | cashier+ |
| `get_browse_descriptions` | `GET` | `/v1/transactions/browse/descriptions?...` | cashier+ |
| `get_available_months` | `GET` | `/v1/transactions/meta/months` | cashier+ |
| `get_available_years` | `GET` | `/v1/transactions/meta/years` | cashier+ |
| `search_descriptions` / `ensure_description_cache` | `GET` | `/v1/transactions/descriptions?prefix=&limit=` | cashier+ |
| `resolve_item_name_for_description` | `GET` | `/v1/transactions/resolve-item?description=` | cashier+ |

### Rejected / discarded / drafts

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_rejected_transactions_for_cashier` | `GET` | `/v1/transactions/cashier/rejected?...` | cashier |
| `get_discarded_transactions_for_cashier` | `GET` | `/v1/transactions/cashier/discarded?...` | cashier |
| `discard_transactions` | `POST` | `/v1/transactions/cashier/discard` | cashier | Body: `{ "ids": [...] }` |
| `restore_discarded_transactions` | `POST` | `/v1/transactions/cashier/restore-discarded` | cashier |
| `delete_discarded_transactions` | `POST` | `/v1/transactions/cashier/delete-discarded` | cashier |
| `resubmit_rejected_transactions` | `POST` | `/v1/transactions/cashier/resubmit` | cashier |
| `count_draft_transactions` | `GET` | `/v1/transactions/drafts/count?...` | cashier |
| `get_draft_day_summaries` | `GET` | `/v1/transactions/drafts/day-summaries?...` | cashier |
| `get_draft_transactions` | `GET` | `/v1/transactions/drafts?...` | cashier |
| `discard_draft_transactions` | `POST` | `/v1/transactions/drafts/discard` | cashier |

### Desktop work

- Rewrite `cashier_service.py` to API only.
- Keep in-memory description cache helpers that call the search endpoint.

### Acceptance

- [x] Every public `cashier_service` function uses `api_client` (no `get_db()` / Motor / `db.transactions`).
- [x] No `get_db()` in `cashier_service.py`.
- [x] Backend tests: `tests/test_transactions_api.py` (12 passed) covering CRUD, register workflow, browse/meta, rejected/discarded/drafts.
- [x] In-memory description cache still warms via `/v1/transactions/descriptions`.

---

## Phase 3 — Accountant verify / reject / edit / deletion / trash

**Router:** `/v1/accountant/...` (extend existing router)

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **3.1** | Backend inbox + counts + meta + cashier-names | Done |
| **3.2** | Backend approve / reject / re-approve / category | Done |
| **3.3** | Backend rejected queue + meta | Done |
| **3.4** | Backend deletion requests | Done |
| **3.5** | Backend trash + verified lookup + get-by-ids | Done |
| **3.6** | Desktop inbox / counts / meta → API | Done |
| **3.7** | Desktop approve / reject / actions → API | Done |
| **3.8** | Desktop rejected / deletions / trash → API | Done |
| **3.9** | Phase 3 acceptance | **Done** |

### Inbox & counts

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_unverified_transactions` | `GET` | `/v1/accountant/inbox?...` | manager |
| `get_unverified_filtered` | `GET` | `/v1/accountant/inbox` (filters query) | manager |
| `count_unverified_filtered` | `GET` | `/v1/accountant/inbox/count?...` | manager |
| `get_pending_count` | `GET` | `/v1/accountant/counts/pending` | manager |
| `get_edited_transactions` | `GET` | `/v1/accountant/inbox/edited?...` | manager |
| `count_edited_transactions` | `GET` | `/v1/accountant/inbox/edited/count?...` | manager |
| `get_edited_count` | `GET` | `/v1/accountant/counts/edited` | manager |
| `get_unverified_trucks` | `GET` | `/v1/accountant/inbox/meta/trucks` | manager |
| `get_unverified_items` | `GET` | `/v1/accountant/inbox/meta/items` | manager |
| `get_unverified_descriptions` | `GET` | `/v1/accountant/inbox/meta/descriptions` | manager |
| `get_unverified_cashier_ids` | `GET` | `/v1/accountant/inbox/meta/cashiers` | manager |
| `get_cashier_names` | `GET` | `/v1/accountant/cashier-names?ids=` | manager |

### Approve / reject / re-approve

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `approve_transaction` | `POST` | `/v1/accountant/transactions/{tx_id}/approve` | manager |
| `bulk_approve_transactions` | `POST` | `/v1/accountant/transactions/bulk-approve` | manager |
| `reject_transaction` | `POST` | `/v1/accountant/transactions/{tx_id}/reject` | manager |
| `bulk_reject_transactions` | `POST` | `/v1/accountant/transactions/bulk-reject` | manager |
| `re_approve_transaction` | `POST` | `/v1/accountant/transactions/{tx_id}/re-approve` | manager |
| `bulk_re_approve_transactions` | `POST` | `/v1/accountant/transactions/bulk-re-approve` | manager |
| `return_to_inbox` | `POST` | `/v1/accountant/transactions/{tx_id}/return-to-inbox` | manager |
| `update_transaction_category` | `PATCH` | `/v1/accountant/transactions/{tx_id}/category` | manager |
| `bulk_update_transaction_category` | `POST` | `/v1/accountant/transactions/bulk-category` | manager |

### Rejected queue

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_rejected_transactions` | `GET` | `/v1/accountant/rejected?...` | manager |
| `count_rejected_transactions` | `GET` | `/v1/accountant/rejected/count?...` | manager |
| `get_rejected_count` | `GET` | `/v1/accountant/counts/rejected` | manager |
| `get_rejected_trucks` / `items` / `descriptions` / `cashier_ids` | `GET` | `/v1/accountant/rejected/meta/{trucks\|items\|descriptions\|cashiers}` | manager |

### Deletion requests

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_deletion_requested_filtered` | `GET` | `/v1/accountant/deletions?...` | manager |
| `count_deletion_requested_filtered` | `GET` | `/v1/accountant/deletions/count?...` | manager |
| `get_deletion_requested_count` | `GET` | `/v1/accountant/counts/deletions` | manager |
| Meta trucks/items/descriptions/cashiers | `GET` | `/v1/accountant/deletions/meta/...` | manager |
| `confirm_deletion` | `POST` | `/v1/accountant/deletions/{tx_id}/confirm` | manager |
| `bulk_confirm_deletions` | `POST` | `/v1/accountant/deletions/bulk-confirm` | manager |
| `restore_deletion` | `POST` | `/v1/accountant/deletions/{tx_id}/restore` | manager |
| `bulk_restore_deletions` | `POST` | `/v1/accountant/deletions/bulk-restore` | manager |

### Trash

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_trashed_transactions` | `GET` | `/v1/accountant/trash?...` | manager |
| `count_trashed_transactions` | `GET` | `/v1/accountant/trash/count?...` | manager |
| `get_trash_count` | `GET` | `/v1/accountant/counts/trash` | manager |
| `restore_from_trash` | `POST` | `/v1/accountant/trash/{tx_id}/restore` | manager |
| `bulk_restore_from_trash` | `POST` | `/v1/accountant/trash/bulk-restore` | manager |
| `permanently_delete_trashed` | `DELETE` | `/v1/accountant/trash/{tx_id}` | manager |
| `bulk_permanently_delete_trashed` | `POST` | `/v1/accountant/trash/bulk-delete` | manager |

### Verified lookup (shared)

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_verified_transactions` | `GET` | `/v1/accountant/verified?...` | manager |
| `get_transactions_by_ids` | `GET` | `/v1/transactions?ids=` | authenticated | Reuses Phase 2 shared endpoint |

### Desktop work

- Move verify-inbox / reject / deletion / trash call sites in `accountant_service.py` to API.

### Acceptance

- [x] All Phase 3 desktop entry points (48 functions) use `api_client` — no `get_db()` in those call paths.
- [x] Backend tests: inbox / actions / rejected / deletions / trash (**18 passed**).
- [x] `/v1/accountant/notification-counts` shares `pending_inbox_count_query()` with `/counts/pending`.
- [x] Master / reports / overview / feeds in `accountant_service.py` remain for Phase 4+.

---

## Phase 4 — Master expenses, reports, diesel cash, overview KPIs

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **4.1** | Backend master read: list / count / totals / years / month-totals / trucks / categories | Done |
| **4.2** | Backend master column-values + update / bulk-update | Done |
| **4.3** | Backend category / supplier reports | Done |
| **4.4** | Backend diesel cash | Done |
| **4.5** | Backend overview dashboard | Done |
| **4.6** | Desktop master → API | Done |
| **4.7** | Desktop reports → API | Done |
| **4.8** | Desktop diesel cash → API | Done |
| **4.9** | Desktop overview → API | Done |
| **4.10** | Phase 4 acceptance | **Done** |

### Master expenses

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_master_transactions` | `GET` | `/v1/accountant/master?...` | manager |
| `count_master_transactions` | `GET` | `/v1/accountant/master/count?...` | manager |
| `get_master_totals` | `GET` | `/v1/accountant/master/totals?...` | manager |
| `get_master_month_totals` | `GET` | `/v1/accountant/master/month-totals?year=` | manager |
| `get_master_available_years` | `GET` | `/v1/accountant/master/years` | manager |
| `get_master_trucks` | `GET` | `/v1/accountant/master/meta/trucks?year=` | manager |
| `get_master_categories` | `GET` | `/v1/accountant/master/meta/categories?year=` | manager |
| `get_master_column_values` | `GET` | `/v1/accountant/master/meta/column-values?...` | manager |
| `update_master_transaction` | `PATCH` | `/v1/accountant/master/{tx_id}` | manager |
| `bulk_update_master_transactions` | `POST` | `/v1/accountant/master/bulk-update` | manager |

### Category / supplier reports

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_category_lifetime_usage` | `GET` | `/v1/accountant/reports/category/{name}/lifetime` | manager |
| `get_category_report_totals` | `GET` | `/v1/accountant/reports/category/{name}/totals?...` | manager |
| `get_category_report_transactions` | `GET` | `/v1/accountant/reports/category/{name}/transactions?...` | manager |
| `get_category_report_opening_balance` | `GET` | `/v1/accountant/reports/category/{name}/opening-balance?...` | manager |
| `get_category_report_descriptions` | `GET` | `/v1/accountant/reports/category/{name}/descriptions?...` | manager |
| `get_category_report_column_values` | `GET` | `/v1/accountant/reports/category/{name}/column-values?...` | manager |
| `get_categories_usage_totals` | `POST` | `/v1/accountant/reports/categories/usage-totals` | manager | Body: `{ "names": [...] }` |
| `get_supplier_payments_totals` | `GET` | `/v1/accountant/reports/supplier-payments/totals?year=` | manager |

### Diesel cash

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_diesel_cash_item_names` | `GET` | `/v1/accountant/diesel-cash/items` | manager |
| `set_diesel_cash_item_names` | `PUT` | `/v1/accountant/diesel-cash/items` | manager |
| `get_diesel_cash_month_summaries` | `GET` | `/v1/accountant/diesel-cash/month-summaries?year=` | manager |
| `get_diesel_cash_transactions` | `GET` | `/v1/accountant/diesel-cash/transactions?...` | manager |
| `count_diesel_cash_transactions` | `GET` | `/v1/accountant/diesel-cash/transactions/count?...` | manager |
| `get_diesel_cash_totals` | `GET` | `/v1/accountant/diesel-cash/totals?...` | manager |
| `get_diesel_entries` | `GET` | `/v1/accountant/diesel-cash/entries?...` | manager |

### Overview dashboard

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_overview_dashboard` | `GET` | `/v1/accountant/overview?year=` | manager | Prefer one composite payload |
| `get_overview_kpis` | `GET` | `/v1/accountant/overview/kpis?year=` | manager |
| `get_overview_category_breakdown` | `GET` | `/v1/accountant/overview/categories?year=` | manager |
| `get_overview_receipt_breakdown` | `GET` | `/v1/accountant/overview/receipts?year=` | manager |
| `get_overview_fuel_liters` | `GET` | `/v1/accountant/overview/fuel-liters?year=` | manager |
| `get_overview_month_totals` | `GET` | `/v1/accountant/overview/month-totals?year=` | manager |

### Acceptance

- [x] All Phase 4 desktop entry points (**31 functions**) use `api_client` — no `get_db()` in those call paths.
- [x] Backend tests: master / reports / diesel / overview (**12 passed**).
- [x] Desktop tests: master edit / category filters / suppliers / diesel cash / overview fuel (**39 passed** combined with backend = **51**).
- [x] Export restriction helpers still read via Phase 1 `settings_service` → `/v1/settings/...`.
- [x] Master Expenses, category Quick Reports, diesel cash, overview load and mutate via API.

---

## Phase 5 — Attachments

Attachments today store files under a local desktop path and metadata on the transaction. Move **storage to the server** (disk under API host or object storage later). Desktop only uploads/downloads via HTTP.

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **5.1** | Backend storage config (`ATTACHMENTS_DIRECTORY`) + list + download file | **Done** |
| **5.2** | Backend upload (multipart) + remove | **Done** |
| **5.3** | Backend cleanup-orphans | **Done** |
| **5.4** | Desktop list / open (download-to-cache) → API | **Done** |
| **5.5** | Desktop add / remove → API | **Done** |
| **5.6** | Desktop cleanup → API (optional; server job primary) | **Done** |
| **5.7** | Phase 5 acceptance | **Done** |

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_attachments` | `GET` | `/v1/transactions/{tx_id}/attachments` | authenticated |
| `add_attachment` | `POST` | `/v1/transactions/{tx_id}/attachments` | cashier+ | `multipart/form-data` |
| `remove_attachment` | `DELETE` | `/v1/transactions/{tx_id}/attachments/{attachment_id}` | cashier+ / manager |
| Download / open | `GET` | `/v1/transactions/{tx_id}/attachments/{attachment_id}/file` | authenticated |
| `cleanup_attachment_orphans` | `POST` | `/v1/transactions/attachments/cleanup-orphans` | manager | Server-side job; optional from desktop |

Local helpers (`attachments_root`, `staging_root`, `purge_stale_staging`) are thin client cache/staging only; authoritative storage is on the API host.

### Acceptance

- [x] Attach / list / open / remove works without desktop Mongo or shared Tailscale disk paths.
- [x] Backend tests cover list / upload / download / remove / cleanup (**13 passed**).
- [x] `attachment_service.py` uses `api_client` — no `get_db()` in attach paths.
- [x] Desktop attachment + `api_client` tests (**14 passed**; combined with backend = **27**).

---

## Phase 6 — Reconciliation (bonds & stations)

**Collections:** `reconciliation_entries`, `recon_stations`  
**Router:** `/v1/reconciliation`  
**Tables:** `bonds` \| `rpa_schedule` · **Entity:** `sm_burhani`

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **6.1** | Backend ops: query builders, plate search, totals pipelines, station helpers | **Done** |
| **6.2** | Backend API: rows + all-records + years + counts + totals | **Done** |
| **6.3** | Backend API: uploads list / detail / delete | **Done** |
| **6.4** | Backend API: existing-keys + save rows + patch status | **Done** |
| **6.5** | Backend API: stations GET / ensure / add (+ bonds seed) | **Done** |
| **6.6** | Desktop `reconciliation_service.py` → API | **Done** |
| **6.7** | Desktop `accountant_service` recon save path | **Done** |
| **6.8** | Phase 6 acceptance | **Done** |

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_reconciliation_rows` | `GET` | `/v1/reconciliation/{table}/rows?...` | manager |
| `count_reconciliation_rows` | `GET` | `/v1/reconciliation/{table}/rows/count?...` | manager |
| `get_reconciliation_totals` | `GET` | `/v1/reconciliation/{table}/totals?...` | manager |
| `get_recon_uploads` | `GET` | `/v1/reconciliation/{table}/uploads` | manager |
| `get_recon_all_records` | `GET` | `/v1/reconciliation/{table}/all?...` | manager |
| `count_recon_all_records` | `GET` | `/v1/reconciliation/{table}/all/count?...` | manager |
| `get_recon_all_totals` | `GET` | `/v1/reconciliation/{table}/all/totals?...` | manager |
| `get_recon_available_years` | `GET` | `/v1/reconciliation/{table}/years?...` | manager |
| `get_recon_upload_stations` | `GET` | `/v1/reconciliation/{table}/uploads/{upload_id}/stations` | manager |
| `get_recon_upload_records` | `GET` | `/v1/reconciliation/{table}/uploads/{upload_id}/records?...` | manager |
| `count_recon_upload_records` | `GET` | `/v1/reconciliation/{table}/uploads/{upload_id}/records/count?...` | manager |
| `get_recon_upload_totals` | `GET` | `/v1/reconciliation/{table}/uploads/{upload_id}/totals?...` | manager |
| `delete_recon_upload` | `DELETE` | `/v1/reconciliation/{table}/uploads/{upload_id}` | manager |
| `get_existing_recon_keys` | `POST` | `/v1/reconciliation/{table}/existing-keys` | manager |
| `save_reconciliation_rows` | `POST` | `/v1/reconciliation/{table}/rows` | manager |
| `set_row_status` | `PATCH` | `/v1/reconciliation/{table}/rows/{row_id}` | manager |
| `get_recon_stations` | `GET` | `/v1/reconciliation/{table}/stations` | manager |
| `ensure_recon_stations` | `POST` | `/v1/reconciliation/{table}/stations/ensure` | manager |
| `add_recon_station` | `POST` | `/v1/reconciliation/{table}/stations` | manager |
| `get_reconciliation_entries` (accountant alias) | same as rows/list | | |

### Acceptance

- [x] Reconciliation UI fully API-backed; no desktop Mongo in `reconciliation_service` / Bonds–RPA UI paths.
- [x] Backend tests: ops + reconciliation API (**17 passed**).
- [x] Desktop tests: RPA multi-table + plate search (**14 passed**; combined = **31**).
- [x] Skipped-row release for bonds/RPA uses API `save_reconciliation_rows`.
- [x] Truck overview reads `reconciliation_entries` via truck-overview API (Phase 9).

---

## Phase 7 — Daily + master Excel imports

Parsing Excel can stay on the desktop (CPU/local file). **Persistence** must hit the API.

Idempotency for these flows uses unique indexes on `transactions`
(`daily_import_id` + `import_row_key`, and `master_import_source` + `master_serial`) —
not a separate `import_idempotency` collection.

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **7.1** | Backend `import_ops`: row keys, indexes, `insert_many_idempotent`, verified-doc builders, issue query, uploads pipeline | **Done** |
| **7.2** | Backend API: daily `POST /commit` | **Done** |
| **7.3** | Backend API: daily uploads list / records / delete / cleanup-null-ids | **Done** |
| **7.4** | Backend API: daily issues list / count / clear | **Done** |
| **7.5** | Backend API: master `POST /commit` | **Done** |
| **7.6** | Desktop `daily_import_service` persist paths → API (parse stays local) | **Done** |
| **7.7** | Desktop `master_import_service` commit → API (parse/mapping stays local) | **Done** |
| **7.8** | Phase 7 acceptance | **Done** |

### Daily import — `/v1/imports/daily`

| Desktop function | Method | Path | Role | Notes |
|------------------|--------|------|------|-------|
| `preview_daily_import` / mapping helpers | *(local)* | — | — | Parse/map on client |
| `commit_daily_to_master` | `POST` | `/v1/imports/daily/commit` | manager | Body: staged rows + meta; server writes txs + idempotency |
| `list_daily_uploads` | `GET` | `/v1/imports/daily/uploads` | manager |
| `get_daily_upload_records` | `GET` | `/v1/imports/daily/uploads/{upload_id}/records?...` | manager |
| `delete_daily_upload` | `DELETE` | `/v1/imports/daily/uploads/{upload_id}` | manager |
| `cleanup_null_daily_import_ids` | `POST` | `/v1/imports/daily/cleanup-null-ids` | manager |
| `get_issue_transactions` | `GET` | `/v1/imports/daily/issues?...` | manager |
| `count_issue_transactions` | `GET` | `/v1/imports/daily/issues/count?...` | manager |
| `clear_issue_flags` | `POST` | `/v1/imports/daily/issues/{tx_id}/clear` | manager |

### Master import — `/v1/imports/master`

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `preview_master_import` / mapping | *(local)* | — | — |
| `commit_master_import` | `POST` | `/v1/imports/master/commit` | manager |

### Acceptance

- [x] Commit + upload history + issue clearing work without desktop Mongo.
- [x] Idempotency keys enforced server-side (unique indexes on `transactions`).
- [x] `daily_import_service` / `master_import_service` persist paths use `api_client` — no `get_db()` for commit/uploads/issues.
- [x] Backend tests cover daily + master import ops/API (**16 passed**).

---

## Phase 8 — Imported feeds & separate expenses

**Collections:** `imported_feeds`, `separate_expenses`, `skipped_import_rows`  
(Idempotency uses unique indexes + `insert_many_idempotent` — not a separate Mongo collection.)  
**Router pattern:** `/v1/feeds/{feed_key}/...` with feed-specific aliases where UX names matter.

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **8.1** | Backend `feed_ops`: registry, shared search/query/sort/save helpers, excel date normalize | **Done** |
| **8.2** | Backend `feeds_api` skeleton + **toll-plaza** end-to-end | **Done** |
| **8.3** | Backend parking Congo (+ deposits) | **Done** |
| **8.4** | Backend RahnTech + Zambia parking | **Done** |
| **8.5** | Backend insurance (`comesa`, `third-party`) | **Done** |
| **8.6** | Backend diesel (4 stations) | **Done** |
| **8.7** | Backend Kimvi + Congo (`separate_expenses`) | **Done** |
| **8.8** | Backend Afritrack | **Done** |
| **8.9** | Backend skipped import rows + reupload | **Done** |
| **8.10** | Desktop `accountant_service` feed/skipped paths → API | **Done** |
| **8.11** | Phase 8 acceptance | **Done** |

Generic operations every feed needs:

| Pattern | Method | Path |
|---------|--------|------|
| List page | `GET` | `/v1/feeds/{feed}/records?...` |
| Count | `GET` | `/v1/feeds/{feed}/records/count?...` |
| Totals | `GET` | `/v1/feeds/{feed}/totals?...` |
| Available years | `GET` | `/v1/feeds/{feed}/years` |
| List uploads | `GET` | `/v1/feeds/{feed}/uploads` |
| Upload records | `GET` | `/v1/feeds/{feed}/uploads/{upload_id}/records?...` |
| Upload count/totals | `GET` | `/v1/feeds/{feed}/uploads/{upload_id}/...` |
| Delete upload | `DELETE` | `/v1/feeds/{feed}/uploads/{upload_id}` |
| Existing keys | `POST` | `/v1/feeds/{feed}/existing-keys` |
| Save / import | `POST` | `/v1/feeds/{feed}/import` |

### Feed keys (map from `accountant_service`)

| Feed / area | Desktop functions (representative) | `feed` key |
|-------------|--------------------------------------|------------|
| Generic imported feed | `get_imported_feed`, `count_imported_feed`, `get_imported_feed_totals`, `get_existing_feed_keys`, `save_imported_feed` | `imported` (+ `feed_type` query) |
| Toll plaza | `get_toll_plaza_*`, `delete_toll_plaza_upload` | `toll-plaza` |
| Parking Congo | `get_parking_congo_*`, deposits helpers | `parking-congo` |
| Rahntech | `get_rahntech_*` | `rahntech` |
| Zambia parking | `get_zambia_parking_*`, sheet helpers | `zambia-parking` |
| Insurance | `get_insurance_*`, `get_existing_insurance_keys` | `insurance` |
| Diesel feeds | `get_diesel_*`, upload/hash helpers | `diesel` (+ `feed_type`) |
| Kimvi | `get_kimvi_*`, `save_kimvi_import`, sheet helpers | `kimvi` |
| Congo | `get_congo_*`, `save_congo_import` | `congo` |
| Afritrack | `get_afritrack_*` | `afritrack` |

### Skipped import rows

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `save_skipped_import_rows` | `POST` | `/v1/feeds/skipped` | manager |
| `list_skipped_import_rows` | `GET` | `/v1/feeds/skipped?...` | manager |
| `count_skipped_import_rows` | `GET` | `/v1/feeds/skipped/count?...` | manager |
| `update_skipped_import_truck` | `PATCH` | `/v1/feeds/skipped/{doc_id}` | manager |
| `delete_skipped_import_rows` | `POST` | `/v1/feeds/skipped/delete` | manager |
| `reupload_skipped_import_rows` | `POST` | `/v1/feeds/skipped/reupload` | manager |

Implement feed-by-feed inside this phase; each feed is a checklist item before marking Phase 8 done.

### Acceptance

- [x] Every feed UI used in production can list/import/delete via API (`feed_service` → `/v1/feeds/...`).
- [x] No `get_db()` remaining for feed/skipped paths in desktop feed UI services.
- [x] Backend feed + skipped tests: **20 passed**; desktop feed helper tests: **25 passed**.

---

## Phase 9 — Truck overview + fuel overview

Server-side aggregates (today private loaders in `accountant_service`).

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **9.1** | Backend truck overview ops + records/count/summary API | **Done** |
| **9.2** | Backend fuel overview ops + records/count/summary/column-filters API | **Done** |
| **9.3** | Desktop truck/fuel overview → API | **Done** |
| **9.4** | Phase 9 acceptance | **Done** |

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `get_truck_overview_records` | `GET` | `/v1/accountant/truck-overview/records?...` | manager |
| `count_truck_overview_records` | `GET` | `/v1/accountant/truck-overview/records/count?...` | manager |
| `get_truck_overview_summary` | `GET` | `/v1/accountant/truck-overview/summary?...` | manager |
| `get_fuel_overview_records` | `GET` | `/v1/accountant/fuel-overview/records?...` | manager |
| `count_fuel_overview_records` | `GET` | `/v1/accountant/fuel-overview/records/count?...` | manager |
| `get_fuel_overview_summary` | `GET` | `/v1/accountant/fuel-overview/summary?...` | manager |
| `get_fuel_overview_column_filter_cache` | `GET` | `/v1/accountant/fuel-overview/column-filters?...` | manager |

### Acceptance

- [x] Truck and fuel overview screens fully API-backed (`/v1/accountant/truck-overview/*`, `/v1/accountant/fuel-overview/*`).
- [x] Backend truck/fuel overview tests: **9 passed**; desktop source/filter helpers still covered.

---

## Phase 10 — Chart of accounts import

**Collections:** `categories`, `category_subtables`, `keyword_rules`, `description_mappings`  
**Desktop today:** `tahmeed/services/chart_of_accounts_service.py` parses Excel locally, then wipes + inserts via `get_db()` (transaction when available). Description mappings are already cleared via `DELETE /v1/description-mappings`.

### Sub-phases (inspect after each)

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| **10.1** | Backend ops: palette, row → category doc builder, wipe + insert helpers | **Done** |
| **10.2** | Backend `POST /v1/imports/chart-of-accounts` (+ request schema, audit) | **Done** |
| **10.3** | Backend tests (replace / empty / insert counts) | **Done** |
| **10.4** | Desktop `chart_of_accounts_service` persist → API (Excel parse stays local); CLI script remains parse+API | **Done** |
| **10.5** | Phase 10 acceptance | **Done** |

| Desktop function | Method | Path | Role |
|------------------|--------|------|------|
| `_parse_chart_of_accounts` | *(local)* | — | — | openpyxl on desktop |
| `import_chart_of_accounts` | `POST` | `/v1/imports/chart-of-accounts` | manager | Body: `{ "rows": [...], "replace_existing": true }`; server wipes categories / subtables / keyword_rules / description_mappings (when replace) then inserts category docs |

### Notes

- Keep Excel parsing on the desktop (same pattern as daily/master/feeds).
- Server owns the transactional replace so Mongo is not required on the desktop.
- Prefer clearing description mappings **inside** the API replace (not a second desktop call), matching today’s wipe semantics.
- Audit action stays `coa.replace`.

### Acceptance

- [x] Chart import succeeds with desktop Mongo disabled for CoA paths.
- [x] `chart_of_accounts_service` has no `get_db()` / `run_in_transaction`.
- [x] Backend tests cover replace + empty-rows rejection (**5 passed**).
- [x] Server clears description mappings on replace (no second desktop call).

---

## Phase 11 — Desktop cutover (remove Mongo from the app)

Desktop talks only to the API. Mongo stays on the server (and local loopback for API/dev scripts). `tahmeed/db/connection.py` may remain for admin scripts / tests that opt in with `MONGODB_URI`; frozen builds must not require it.

### 11.1 Dead Mongo helpers in `accountant_service`

- Delete unused `get_db()` loaders (truck/fuel overview row builders, master/cashier query resolvers that only served pre-API paths).
- Keep pure helpers still covered by unit tests (`_exclude_supplier_payments`, overview filter/query helpers, etc.).

### 11.2 Verify-inbox category fallback

- Remove the Mongo `categories.find` fallback in `verify_inbox.py`; empty API result stays empty (error already shown).

### 11.3 Connectivity = API only

- Drop `_probe_mongo` / direct Motor ping from `connectivity_service.py`.
- `online` = successful `GET /health/ready` (503 `database_unavailable` = offline).
- Update banner / status-bar copy (no “MongoDB Atlas”); update unit tests.

### 11.4 App lifecycle + env / docs

- `main.py`: stop calling `close_db()` on exit.
- Strip `MONGODB_URI` / `DB_NAME` from `.env.production.example` and release docs.
- Root `.env.example` / `LOCAL_DEV.md`: desktop needs `API_BASE_URL` only; Mongo remains for the local API process (`backend/.env`).

### 11.5 Acceptance

- [x] No `get_db` / `get_client` imports under `tahmeed/` outside `tahmeed/db/` (scripts may still use connection).
- [x] Connectivity unit tests pass; banner reflects API only.
- [ ] Packaged app can run with missing/empty `MONGODB_URI` against `https://api.meedexpe.com` (installer rebuild when shipping).

---

## Phase 12 — Server: Docker API + Cloudflare Tunnel + DNS

Do **not** stop Docker or Tailscale Serve to add the tunnel. Both proxies can
target `127.0.0.1:8000` until public health is green. Operator runbook:
[`deployment/docker/CLOUDFLARE.md`](deployment/docker/CLOUDFLARE.md).

### 12.1 Docs (repo)

- [x] [`deployment/docker/CLOUDFLARE.md`](deployment/docker/CLOUDFLARE.md) — tunnel install, overlap, cutover, rollback.
- [x] [`deployment/docker/README.md`](deployment/docker/README.md) — Cloudflare primary ingress; API-only client bake.
- [x] [`deployment/v1/README.md`](deployment/v1/README.md) — same; Tailscale Serve demoted to optional overlap.

### 12.2 Live server (operator)

1. **DNS:** Cloudflare zone for `meedexpe.com` (Namecheap nameservers → Cloudflare if needed).
2. **Hostname:** `api.meedexpe.com` → Cloudflare Tunnel → `http://127.0.0.1:8000`.
3. **cloudflared:** host systemd connector; leave existing API containers running.
4. **Mongo:** confirm `127.0.0.1:27017` only; API env uses loopback URI.
5. **Firewall:** deny public `8000` and `27017`.
6. **Verify:** `curl -fsS https://api.meedexpe.com/health/ready` (no Tailscale on the client).
7. **Cut Tailscale Serve** after green health (`sudo tailscale serve reset`); keep Tailscale node for admin if desired.
8. **Optional:** `git pull` / `scripts/deploy.sh` only when refreshing backend image — not required for tunnel bring-up.
9. **Desktop:** rebuild installer with `API_BASE_URL=https://api.meedexpe.com` and no `MONGODB_URI`.

### Acceptance

- [x] Deployment docs describe Cloudflare Tunnel as production ingress (Tailscale Serve optional overlap only).
- [ ] Production API reachable via `https://api.meedexpe.com` (operator verify on server).
- [ ] Mongo not reachable from the public internet or via a public hostname (operator verify `ss` / UFW).
- [ ] Tailscale Serve cut after public health is green (operator).

---

## Suggested implementation order (summary)

| Phase | Focus | Depends on |
|------:|-------|------------|
| — | Baseline master-data API | done |
| 1 | Settings + audit | — |
| 2 | Cashier transactions | 1 |
| 3 | Verify / reject / deletion / trash | 2 |
| 4 | Master / reports / diesel cash / overview | 3 |
| 5 | Attachments | 2 |
| 6 | Reconciliation | — (can parallel after 1) |
| 7 | Daily / master import commits | 2–4 |
| 8 | Feeds | 6 optional |
| 9 | Truck / fuel overview | 4 + 8 |
| 10 | Chart of accounts import | baseline categories API |
| 11 | Remove desktop Mongo | 1–10 |
| 12 | Cloudflare + DNS cutover | API deploy; usable from Phase 2 onward for staging |

Phases 5, 6, and 10 can run in parallel with 3–4 if needed. Phase 12 staging tunnel can stand up early so Phase 2+ is tested against the real domain.

---

## Per-phase definition of done

For every phase:

1. Backend endpoints implemented + covered by API tests where practical.
2. Desktop service(s) switched to `api_client` (no `get_db()` in those modules).
3. Manual smoke of the related UI against a loopback or tunnel API.
4. Checklist in this file marked complete.
5. Short note in `releases/notes.md` when a user-visible build ships the phase.

---

## Out of scope / non-goals

- Exposing MongoDB through Cloudflare or any public hostname.
- Requiring Tailscale or WARP on end-user PCs after Phase 11.
- Rewriting Excel parsers to run on the server (optional later; commits must be server-side).

---

## Status

| Item | State |
|------|-------|
| This plan document | Approved (Phase 1 started) |
| `API_BASE_URL=https://api.meedexpe.com` baked | Done |
| Local dev (loopback Mongo + API) | Done — see `LOCAL_DEV.md` |
| Phase 1.1 Backend settings API | Done |
| Phase 1.2 Desktop settings → API | Done |
| Phase 1.3 Backend audit API | Done |
| Phase 1.4 Desktop audit → API | Done |
| Phase 1.5 Acceptance | **Done** |
| **Phase 1 (settings + audit)** | **Complete** |
| Phase 2.1 Backend core transaction CRUD | Done |
| Phase 2.2 Backend register workflow | Done |
| Phase 2.3 Backend browse / overview / meta | Done |
| Phase 2.4 Backend rejected / discarded / drafts | Done |
| Phase 2.5 Desktop core CRUD → API | Done |
| Phase 2.6 Desktop register workflow → API | Done |
| Phase 2.7 Desktop browse + rejected/drafts → API | Done |
| Phase 2.8 Acceptance | **Done** |
| **Phase 2 (cashier transactions)** | **Complete** |
| Phase 3.1 Backend inbox + counts + meta | Done |
| Phase 3.2 Backend approve / reject / category | Done |
| Phase 3.3 Backend rejected queue + meta | Done |
| Phase 3.4 Backend deletion requests | Done |
| Phase 3.5 Backend trash + verified + by-ids | Done |
| Phase 3.6 Desktop inbox / counts / meta → API | Done |
| Phase 3.7 Desktop approve / reject / actions → API | Done |
| Phase 3.8 Desktop rejected / deletions / trash → API | Done |
| Phase 3.9 Acceptance | **Done** |
| **Phase 3 (verify / reject / deletion / trash)** | **Complete** |
| Phase 4.1 Backend master read | Done |
| Phase 4.2 Backend master column-values + updates | Done |
| Phase 4.3 Backend category / supplier reports | Done |
| Phase 4.4 Backend diesel cash | Done |
| Phase 4.5 Backend overview dashboard | Done |
| Phase 4.6 Desktop master → API | Done |
| Phase 4.7 Desktop reports → API | Done |
| Phase 4.8 Desktop diesel cash → API | Done |
| Phase 4.9 Desktop overview → API | Done |
| Phase 4.10 Acceptance | **Done** |
| **Phase 4 (master / reports / diesel / overview)** | **Complete** |
| Phase 5.1 Backend storage + list + download | **Done** |
| Phase 5.2 Backend upload + remove | **Done** |
| Phase 5.3 Backend cleanup-orphans | **Done** |
| Phase 5.4 Desktop list / open → API | **Done** |
| Phase 5.5 Desktop add / remove → API | **Done** |
| Phase 5.6 Desktop cleanup → API | **Done** |
| Phase 5.7 Acceptance | **Done** |
| **Phase 5 (attachments)** | **Complete** |
| Phase 6.1 Backend recon ops | **Done** |
| Phase 6.2 Backend rows / all-records | **Done** |
| Phase 6.3 Backend uploads | **Done** |
| Phase 6.4 Backend write paths | **Done** |
| Phase 6.5 Backend stations | **Done** |
| Phase 6.6 Desktop recon service → API | **Done** |
| Phase 6.7 Desktop accountant recon save | **Done** |
| Phase 6.8 Acceptance | **Done** |
| **Phase 6 (reconciliation)** | **Complete** |
| Phase 7.1 Backend import ops | **Done** |
| Phase 7.2 Backend daily commit | **Done** |
| Phase 7.3 Backend daily uploads | **Done** |
| Phase 7.4 Backend daily issues | **Done** |
| Phase 7.5 Backend master commit | **Done** |
| Phase 7.6 Desktop daily import → API | **Done** |
| Phase 7.7 Desktop master import → API | **Done** |
| Phase 7.8 Acceptance | **Done** |
| **Phase 7 (daily / master imports)** | **Complete** |
| Phase 8.1 Backend feed ops | **Done** |
| Phase 8.2 Backend feeds API + toll-plaza | **Done** |
| Phase 8.3 Backend parking Congo | **Done** |
| Phase 8.4 Backend RahnTech + Zambia | **Done** |
| Phase 8.5 Backend insurance | **Done** |
| Phase 8.6 Backend diesel | **Done** |
| Phase 8.7 Backend Kimvi + Congo | **Done** |
| Phase 8.8 Backend Afritrack | **Done** |
| Phase 8.9 Backend skipped rows | **Done** |
| Phase 8.10 Desktop feeds → API | **Done** |
| Phase 8.11 Acceptance | **Done** |
| **Phase 8 (feeds / separate expenses)** | **Done** |
| Phase 9.1 Backend truck overview | **Done** |
| Phase 9.2 Backend fuel overview | **Done** |
| Phase 9.3 Desktop truck/fuel → API | **Done** |
| Phase 9.4 Acceptance | **Done** |
| **Phase 9 (truck / fuel overview)** | **Done** |
| Phase 10.1 Backend CoA ops | **Done** |
| Phase 10.2 Backend CoA import API | **Done** |
| Phase 10.3 Backend CoA tests | **Done** |
| Phase 10.4 Desktop CoA → API | **Done** |
| Phase 10.5 Acceptance | **Done** |
| **Phase 10 (chart of accounts)** | **Done** |
| Phase 11.1 Dead Mongo helpers | **Done** |
| Phase 11.2 Verify-inbox fallback | **Done** |
| Phase 11.3 Connectivity API-only | **Done** |
| Phase 11.4 Env / lifecycle / docs | **Done** |
| Phase 11.5 Acceptance | **Done** (installer rebuild deferred to ship) |
| **Phase 11 (desktop Mongo cutover)** | **Done** |
| Phase 12.1 Deployment docs (Cloudflare) | **Done** |
| Phase 12.2 Live DNS / tunnel / Serve cut | **Pending** (operator on server) |
| **Phase 12 (Cloudflare + DNS)** | **Docs done; live cutover pending** |
