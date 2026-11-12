# Receipt notify + temp map storage

Phased build for: immediate ownership notify on save, temp browse folder for unmapped item, atomic relocate when Day Issues / Master / table mapping lands.

## Goals

1. **Save → notify** — Master (and register) save with ownership + receipt-needed opens/refreshes that day’s request and pushes Expo **immediately** (non-blocking).
2. **Mapping hooks** — Day Issues, Master table edits after save, and register patches that stamp **item/payee** trigger browse relocate.
3. **Temp folder** — Approved (or matched) browse views without an item sit under `parked/unmapped/…` until item exists.
4. **Atomic + background** — Canonical bytes stay in `files/`; browse copies relocate without losing the file; failed notify/relocate retry via scheduler.

## Phases

| Phase | Scope | Status |
| --- | --- | --- |
| **1** | `ensure_receipt_request` + immediate push; hook Master bulk-patch/update; fire-and-forget notify | **Done** — `immediate_notify_pending` + `schedule_receipt_side_effects` |
| **2** | Materialize into `parked/unmapped/` when item missing; tracked paths | **Done** — `parked/unmapped/<file>`; `browse_placement` |
| **3** | On map (Master, Day Issues apply-description-mapping, tx patch) rematerialize | **Done** — all three surfaces schedule side effects |
| **4** | Scheduler drains pending notify + rematerialize; stuck recovery | **Done** — reminders flush immediates; maintenance rematerialize drain |

## Triggers (mapping)

| Surface | API | Relocate? | Notify? |
| --- | --- | --- | --- |
| Master Save | `POST /v1/accountant/master/bulk-patch` (+ single/bulk update) | Yes if item/date changed | Yes if ownership + receipt-needed |
| Day Issues | `POST /v1/transactions/apply-description-mapping` | Yes (item/payee stamp) | No (no ownership edit) |
| Register row save | `PATCH` / bulk-patch transactions | Yes if item changed | Yes (existing ensure + immediate) |

## Storage

```text
<root>/
  files/                      # canonical (unchanged)
  parked/unmapped/<file>      # browse view when linked/approved but no item
  approved/YYYY/MM/<item>/…   # browse view when item present
  quarantine/
```

Relocate = link/copy to new path → `$set` materialized_paths → unlink old unmapped path (best-effort). Never delete canonical `files/`.

## Background

- After write APIs: `asyncio.create_task` for notify + rematerialize (errors logged).
- Flags: `receipt_requests.immediate_notify_pending`, `receipt_files.rematerialize_pending`.
- Scheduler maintenance slot drains both flags (Phase 4).
