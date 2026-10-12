# Cashier / Accountant Trash & Delete — Plan

After each phase: mark it done here, stop, and wait for “go next phase”.

---

## Agreed product rules

| Row state | Cashier deletes… | Result |
|-----------|------------------|--------|
| **Draft** (saved, not submitted) | Soft-trash | **Cashier Trash** — cashier can **Restore** or **Delete permanently** |
| **Submitted, unverified** (in Verify) | Soft-trash | Leaves register + Verify → **Cashier Trash** (no accountant gate) |
| **Verified** (in Master) | Request delete only | Accountant **Verify → Deleted** → on **Confirm**: leave Master + enter **Trash** |

- Never hard-delete from the Daily Register Delete button (except pending-edit **clones**, which stay discardable).
- Cashier Trash and Accountant Trash both list `trashed: True` (scoped by role).
- Verified-path trash: cashier **Request Restore**; accountant restores or purges.
- Draft / unverified soft-trash: cashier may restore or purge without accountant.

---

## Phases

### Phase 1 — Backend: soft-trash unverified deletes  ✅ DONE

**Goal:** Saved but unverified rows (`verified=False`) are soft-trashed
(`trashed=True`) instead of hard-deleted. Verified path unchanged
(`deletion_requested`). Pending-edit clones still hard-deleted.

**Done when:**

- [x] `POST .../request-delete` on unverified → `{"result":"trashed"}`, doc kept with trash flags
- [x] Verified → still `deletion_requested`
- [x] Pending-edit clone → still hard `deleted`
- [x] Register excludes `trashed`; Verify inbox base query now excludes `trashed` too
- [x] Desktop delete feedback mentions Trash for soft-trash
- [x] Backend tests updated

**Shipped in:** `backend/app/transactions_api.py` (`request_or_delete_transaction`);
`excel_grid.py` / `entry_form.py` result handling; `tests/test_transactions_api.py`.

**Out of scope this phase:** Cashier Trash Restore/Purge buttons; UI parity with
accountant Trash tab.

---

### Phase 2 — Cashier Trash actions for soft-trashed unverified  ✅ DONE

**Goal:** Cashier Trash can **Restore** and **Delete permanently** for rows the
cashier soft-trashed (`verified=False` + `trashed=True`). Verified-path trash
(`verified=True` + `trashed=True`) keeps **Request Restore** only.

**Done when:**

- [x] API: cashier restore-from-trash (immediate) + permanent delete for own
      unverified trashed rows
- [x] Cashier Trash UI: Restore / Delete Permanent for eligible rows;
      Request Restore for accountant-confirmed verified trash
- [x] Subtitle / status copy updated
- [x] Tests cover both action classes

**Shipped in:** `transactions_api` `bulk-restore-own` / `bulk-purge-own`
(+ request-restore gated to `verified=True`); `cashier_service`;
`CashierTrashWidget`; `test_cashier_trash_restore_api.py`.

---

### Phase 3 — Verify / Master / messaging consistency  ✅ DONE

**Goal:** Submitted soft-trash disappears from Verify; confirmed verified delete
still leaves Master and lands in Trash; copy in register delete dialogs matches
the three cases.

**Done when:**

- [x] Verify Deleted / inbox never shows `trashed` rows
- [x] Confirm deletion still sets `trashed` on verified path
- [x] Cashier + form messages distinguish draft/submitted trash vs verified request
- [x] Tests / smoke for verify list after submitted soft-trash

**Shipped in:** confirm/success copy in `excel_grid.py` + `entry_form.py`;
inbox already excludes `trashed` (Phase 1); `DELETION_BASE_MATCH` /
`build_verified_query` already exclude limbo+trash; `test_trash_delete_phase3.py`.

---

### Phase 4 — Cashier Trash UI parity + harden  ✅ DONE

**Goal:** Cashier Trash tab feels like Accountant Trash (table actions layout),
with role-correct buttons only. Badge counts and reload after register delete.

**Done when:**

- [x] Layout / actions aligned with accountant trash patterns
- [x] Badge / refresh after soft-trash from register
- [x] Regression tests green for trash + request-delete matrix

**Shipped in:** `CashierTrashWidget` (accountant table density + toolbar);
sidebar `set_trash_badge`; `DailyRegister.trash_changed` /
`EntryForm.trash_changed` → dashboard badge + live trash refresh;
regression suite (8 passed).

---

## Progress log

| Phase | Status | Notes |
|-------|--------|-------|
| 1 | Done | Soft-trash unverified; verified gate unchanged |
| 2 | Done | Cashier restore/purge for own soft-trash |
| 3 | Done | Verify/Master messaging + delete copy |
| 4 | Done | UI parity + trash badge / refresh |
