# Merged View Refactor — Notepad

Temporary working notes (issues → root cause → intended behavior → fix sketch).
Not a product phase plan; use while implementing, then delete or fold into release notes.

---

## Product intent (rephrased)

**Merged** is a **day browser for everyone**, not a second entry form.

| Mode | What you see | What you do |
|------|----------------|-------------|
| **My entries** | Only **your** saved (+ typed/unsaved) rows for the open Reconciled Date | Enter data, Save draft, edit, cut/paste, delete **your** rows |
| **Merged** | **All cashiers’ saved** rows for that same day (Cashier column visible) | Browse, search, copy, right‑click actions that still make sense; reorder only if we keep that as a deliberate merge tool — **no new data entry** |

Toggle My ↔ Merged should feel like changing a filter: **same day, reload scope, no “Save as draft?” friction**.

---

## Issue A — Merged is treated like an entry grid

### Rephrased

Merged still behaves like My: blank editable rows under the saved block, typing creates “unsaved work,” Save/local draft apply in merged mode. That fights the “view everyone’s saved day” job.

### Root cause (code)

- `_populate` always appends `DEFAULT_EDITABLE_ROWS` blank rows after saved txs (`excel_grid._populate` ~1599–1607).
- `_init_editable_rows` makes those cells editable regardless of `_merged_mode`.
- Local drafts keyed by `merged=True|False` (`register_draft_service.draft_path`) — Merged can accumulate its own recovery file.
- Save path still inserts new rows in merged (`_do_save_body` / `day_order` special cases).

### Intended behavior

- Merged: show **saved rows only** (or saved + minimal non-editable padding). No typing new expenses here.
- My: keep blank rows + entry + Save draft as today.
- Optional later: accountant/cashier “reorder drafts in Merged” stays behind Edit — but **not** free-form entry.

### Fix sketch

- When `_merged_mode`: `_populate` with `editable_tail=0` (or read-only filler).
- Block paste/type into “new” rows in Merged; hide or no-op Save for new inserts in Merged (updates/reorder only if we keep Edit).
- Do not restore/create **merged** local drafts for typed new rows (or stop capturing `new_rows` while merged).

---

## Issue B — Switching My ↔ Merged triggers save-draft / unsaved friction

### Rephrased

Clicking Merged (or back to My) should just show that day’s scope. Today it often asks to save/discard draft because unsaved typed rows (or dirty edits) are still on the grid.

### Root cause (code)

- `set_merged_mode` (~1402): if `has_unsaved_work()` → `_prompt_unsaved_work("Save as draft before switching mode?")`.
- Unsaved work includes blank-area typed rows **and** edit-mode dirty saved rows.
- Local draft files differ by mode (`_my.json` vs `_merged.json`); after a forced save/switch, restore can miss the other mode’s file → feels broken.

### Intended behavior

- Mode toggle = **scope reload only**.
- If My has unsaved typed rows: **auto-flush to My local draft**, switch to Merged **without dialog**, load all cashiers’ saved rows.
- Returning to My: restore My local draft onto My grid (already have `_restore_local_draft`).
- Dirty **edit-mode** changes: either (1) same — stash to local draft and switch, or (2) soft warn only if Edit is active with dirty saved rows. Prefer (1) for smoothness unless we want an explicit “leave Edit first.”

### Fix sketch

- Replace prompt in `set_merged_mode` with: `_flush_local_draft()` for current mode → set mode → `_load_date` (no save dialog).
- Never clear the **other** mode’s local draft on switch.
- Action bar `sync_mode` already handles cancel; simplify because cancel path becomes rare.

---

## Issue C — Cut / Insert Cut Cells misaligns columns (wrong data in wrong columns)

### Rephrased

Cut → Insert Cut Cells (or paste of cut/copied rows) puts values in the wrong columns — classic shift (Description in Item, amounts in wrong places, Cashier blank/wrong).

### Root cause (code) — two clipboard shapes

1. **Row cut** (`_cut_rows`) uses `_serialize_row` / `_row_value_map`:
   - Full column index map → `_paste_row_maps` writes by **column index** (correct when maps are used).

2. **Copy** (`_copy`) for full-row selection builds TSV from columns **excluding** `READONLY_COLS` (`COL_SNO`, `COL_CASHIER`):
   - TSV cell 0 = Date (table col 1), not S/NO.

3. **`_paste_rows`** (TSV path, used when maps missing or clipboard is `_ROWS_CLIP_PREFIX` without maps):
   ```text
   values = { col: cell for col, cell in enumerate(cells) if col != COL_SNO }
   ```
   - `enumerate` assumes TSV index == table column index.
   - If TSV omitted S/NO (from `_copy`), Date lands in col 0 (skipped) / next cells shift → **off-by-one**.
   - Cashier column present in Merged but omitted from copy TSV → further skew if anything assumes full width.

4. **Insert Cut Cells** for cell-kind cuts just calls `_paste()`; for row-kind uses maps (better). Failures happen when falling through to line-based `_paste_rows` or when copy/paste crosses My (Cashier hidden) ↔ Merged (Cashier shown).

5. Merged+Edit insert position (`insert_at = max(cur, 0)`) vs My (`max(cur, saved_count)`) interacts with remove-then-insert shifting — mostly OK when maps work; broken when TSV path used.

### Intended behavior

- Cut/Insert of rows always moves **by column identity** (maps), never by fragile TSV index alone.
- Copy TSV and paste TSV must share one convention (prefer: include all data cols by absolute index, empty placeholders for S/NO + Cashier; or map header names).
- My ↔ Merged: Cashier is display-only; never shift other fields when Cashier visibility changes.

### Fix sketch

- Prefer `_paste_row_maps` whenever cut buffer has `maps`.
- Fix `_paste_rows` to align TSV to columns the same way `_copy` emits them (skip READONLY consistently, or always full-width with empties).
- Align `_copy` full-row TSV with `_serialize_row` (same columns, same order).
- Add regression tests: copy full row → paste_rows; cut insert with Cashier visible; My→Merged visibility.

---

## Issue D — Can delete someone else’s entry in Merged

### Rephrased

In Merged you see every cashier’s rows. Deleting a row that is **not yours** must be blocked in the UI (and ideally API). Today the context menu offers “Delete Saved Entry” for any saved row without an ownership check.

### Root cause (code)

- `_populate_context_menu` / `_delete_saved_rows`: any `row < _saved_count` can be deleted; no `tx.cashier_id == self._user._id` filter.
- Backend `POST …/request-delete` (~1429): loads doc by id and soft-trashes if unverified — **does not require** `doc.cashier_id == actor._id`. So UI bypass still deletes others.

### Intended behavior

- Merged (and My): delete / trash only **own** rows.
- Context menu: omit Delete for others’ rows; optional Copy still available.
- Multi-select: delete only the subset you own; warn if selection mixed.
- API: refuse trash/delete when actor is cashier and `cashier_id` ≠ actor (accountants may keep broader rights if product says so).

### Fix sketch

- Helper `_owns_saved_row(row) -> bool`.
- Filter `saved_sel` in context menu and `_delete_saved_rows`.
- Harden `request_or_delete_transaction` for cashier role.

---

## Issue E — Right-click “meaning” must stay useful

### Rephrased

Refactoring Merged to view-first must **not** gut the context menu. Keep actions that still make sense; drop or gate ones that assume “I’m entering rows here.”

### Current menu split (`_populate_context_menu`)

| Situation | Actions today |
|-----------|----------------|
| Full-row saved selection, not Edit | Delete Saved Entry/ies |
| Single saved cell, not Edit | Delete Saved Entry |
| Else (editable / edit mode) | Copy, Cut, Paste, Insert Cut Cells, Insert Row Above/Below, Delete Selected Row(s), Undo/Redo |

### Intended behavior (Merged)

- Always useful: **Copy**, Search (toolbar), maybe Attach if own row.
- Own saved row: **Delete** (trash) as today.
- Others’ rows: **no Delete**; Copy OK.
- Insert Row / Paste into blank / new entry: **off in Merged** (no entry surface).
- Cut / Insert Cut Cells on **own drafts** for day reorder: keep only if we still support Merged Edit reorder; otherwise My-only.

### Fix sketch

- Branch menu on `_merged_mode` + ownership + edit mode.
- Document in this file what we keep when implementing.

---

## Recommended work order

1. **D** — ownership on delete (UI + API) — small, safety.
2. **B** — smooth mode switch without save prompt — medium.
3. **A** — Merged = no data entry / no blank editable entry block — medium.
4. **C** — cut/copy/paste column alignment — medium, needs tests.
5. **E** — trim context menu to match A–D.

---

## Code map (quick)

| Area | Where |
|------|--------|
| Mode toggle UI | `dashboard._ActionBar` My/Merged |
| Mode switch + unsaved prompt | `excel_grid.set_merged_mode`, `_save_then_switch_mode` |
| Load scope | `_load_date_body` → `get_transactions_by_date(..., merged=)` |
| Blank entry rows | `_populate`, `_init_editable_rows`, `DEFAULT_EDITABLE_ROWS` |
| Cut / insert / paste | `_cut`, `_cut_rows`, `_insert_cut_cells`, `_paste_row_maps`, `_paste_rows`, `_paste`, `_copy` |
| Context menu / delete | `_populate_context_menu`, `_delete_saved_rows`, `request_or_delete_transaction` |
| Local drafts by mode | `register_draft_service.draft_path` |
| Existing cut tests | `tests/test_excel_grid_cut_insert.py` |

---

## Open product choices (decide while fixing)

1. **Merged Edit / reorder** — Keep cut-insert reorder of draft rows in Merged (WhatsApp day order), or My-only?
2. **Dirty edit-mode on switch** — Silent stash vs require Exit Edit?
3. **Accountant Merged** — Same view-only + delete rules, or accountant may delete any?

Default assumption until told otherwise:

1. Keep Merged Edit **only for reordering saved drafts** (no new typed rows).
2. Silent stash local draft on mode switch.
3. Cashiers: own rows only; accountants: follow existing broader delete policy if any (confirm).

---

## Progress

| Issue | Noted | Fixed |
|-------|-------|-------|
| A Entry surface in Merged | Yes | **Done** |
| B Mode-switch save prompt | Yes | **Done** |
| C Cut/insert column skew | Yes | **Done** |
| D Delete others’ rows | Yes | **Done** |
| E Context menu alignment | Yes | **Done** |

### D shipped

- UI: `_owns_saved_row` / `_owned_saved_rows`; context menu, Delete key, `_delete_saved_rows` filter to own rows (accountants/admins: all).
- API: cashiers get 403 on another cashier’s `request-delete`; accountants may still delete any.
- Client maps 403 → `"forbidden"`.
- Tests: `tests/test_merged_view_ownership.py` + backend ownership cases.

### B shipped

- `set_merged_mode`: no save/discard dialog; flush current-mode local draft, flip mode, reload.
- Removed `_save_then_switch_mode` path.
- Tests: `tests/test_merged_mode_switch.py`.

### A shipped

- `_populate`: Merged → saved rows only (no blank entry tail); My keeps `DEFAULT_EDITABLE_ROWS`.
- Guards: `_cell_editable`, `_append_editable_rows`, insert/delete blank rows, paste below saved, import, Save (no new inserts), local draft capture/restore of `new_rows`, New table forces My.
- Reorder of saved drafts via cut-insert maps still allowed in Merged Edit.
- Tests: `tests/test_merged_no_entry.py`.

### C shipped

- Full-row `_copy` uses `_serialize_row` (absolute columns, incl. S/NO + Cashier).
- `_tsv_cells_to_column_map` for paste_rows (full-width + legacy data-only).
- Tests: `tests/test_merged_cut_paste_columns.py`.

### E shipped

- Merged browse: Copy + Delete own only (no Insert Row / Paste entry).
- Merged Edit: Copy/Cut/Paste/Insert Cut Cells for reorder; still no blank-row insert.
- My: full entry menu unchanged in spirit; Copy always present.
- Tests: `tests/test_merged_context_menu.py`.

**All notepad issues A–E implemented.**
