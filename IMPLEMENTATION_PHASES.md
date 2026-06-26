# Implementation Phases — Edit Mode, Save/Export Controls & Cascade Update System

Navigation map for implementing [DESIGN_EDIT_MODE_CASCADE.md](DESIGN_EDIT_MODE_CASCADE.md).
Each phase is implemented one at a time. After each phase: summary → wait for go-ahead → next phase.

---

## Phase 1 — Data Layer (Model + Services)  ✅ DONE

Foundation that everything else builds on. No UI changes — safe to land first.

**Files & changes:**
- `tahmeed/models/transaction.py`
  - Add fields: `edited_after_verification: bool = False`, `last_edited_at: Optional[datetime]`, `last_edited_by: Optional[ObjectId]`
  - Update `to_doc()` and `from_doc()` (with defaults for legacy docs)
- `tahmeed/services/cashier_service.py`
  - Add `update_transaction(tx_id, updates: dict) -> bool`
- `tahmeed/services/accountant_service.py`
  - Extend `_build_inbox_query` to optionally filter on `edited_after_verification`
  - Add `get_edited_transactions(...)` + `count_edited_transactions(...)`
  - Add `re_approve_transaction(tx_id, accountant_id)` + `bulk_re_approve_transactions(...)`
  - Make the "New" inbox query exclude edited rows (`edited_after_verification != True`)

**Acceptance:** code imports cleanly; new fields round-trip through to_doc/from_doc; new service functions exist.

---

## Phase 2 — Remove Auto-Save  ✅ DONE

Strip the recently-added auto-save machinery so explicit Save can replace it.

**Files & changes:**
- `tahmeed/ui/cashier/excel_grid.py`
  - Remove `_autosave_row`, `_on_current_cell_changed`, `_on_retry_timer`, `_freeze_row`
  - Remove `_retry_queue`, `_retry_timer` from `__init__` and `_populate`
  - Remove `currentCellChanged` connection in `_build_ui`
  - Keep `_build_transaction_from_row`, `save_rows`, `_do_save`
- `tahmeed/ui/accountant/verify_inbox.py`
  - Remove `app_signals.transaction_saved.connect(self._debounce.start)` line
- `tahmeed/signals.py` — keep file (repurposed in later phase if needed)

**Acceptance:** app still runs; typing rows no longer auto-saves; explicit `save_rows()` still works.

---

## Phase 3 — Edit Mode + Save/Export/Edit Buttons (Cashier)  ✅ DONE

The cashier-facing UI: explicit controls and full edit mode.

**Files & changes:**
- `tahmeed/ui/cashier/excel_grid.py`
  - Add state: `_edit_mode: bool`, `_dirty_rows: set[int]`, `_dirty_originals: dict[int, Transaction]`
  - Add `toggle_edit_mode()`, `_enter_edit_mode()`, `_exit_edit_mode(discard)`
  - Add `export_xlsx()` / `export_csv()` (respect search/column filters → visible rows only)
  - Modify `_on_item_changed` — in edit mode, track dirty saved rows + visual indicator
  - Modify `_do_save` — INSERT new rows + UPDATE dirty saved rows (verified vs unverified paths)
  - Modify `navigate_to_date` — prompt about unsaved changes
- `tahmeed/ui/cashier/dashboard.py`
  - Add action bar (Edit toggle / Save / Export) between header and register
  - Edit button reflects active/inactive state

**Acceptance:** Edit toggles editability + yellow bg; Save inserts/updates; verified edits revoke approval; Export writes xlsx; date-nav prompt works.

---

## Phase 4 — Accountant Edited Sub-Tab  ✅ DONE

The accountant-facing review of edited-after-verification rows.

**Files & changes:**
- `tahmeed/ui/accountant/verify_inbox.py`
  - Add New | Edited sub-tab bar with count badges
  - Tab switch handler swaps the query (`get_unverified_filtered` vs `get_edited_transactions`)
  - "Edited" tab shows `last_edited_at` + `last_edited_by` context + orange highlight
  - Approve on Edited tab → `re_approve_transaction` (clears `edited_after_verification`)

**Acceptance:** two tabs with counts; edited rows show edit context; re-approve clears flag and cascades to Master Expenses on next load.

---

*Cascade (section 9–10 of the design doc) is implicit — no extra code; all downstream views query the DB fresh.*
