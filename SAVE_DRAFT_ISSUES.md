# Save / Draft Issues — Investigation & Fix Plan

User-reported problems with Daily Register **Save**, **server Drafts**,
**Merged view** leave prompts, and **Discard whole draft**.

After each issue: mark status here, then fix with the context below.

---

## Glossary (two different “drafts”)

| Kind | Where | Purpose |
|------|--------|---------|
| **Server draft** | Mongo `transactions` with `register_status="draft"` | Saved-but-not-submitted; shows in sidebar **Drafts** |
| **Local draft** | `%LOCALAPPDATA%\meedexpe\register_drafts\*.json` | Crash recovery of unsaved grid cells (keyed by user + date + My/Merged) |

Cashiers click toolbar **Save** expecting a **server draft**. Incomplete rows
often never reach Mongo; they only sit in the grid / local draft — which feels
like “Save did nothing.”

---

## Issue list (from report)

| # | Symptom (user) | Status |
|---|----------------|--------|
| 1 | Enter data → Save: no “saving…” UI; data does not appear saved | Fixed |
| 2 | Switch to Merged: told work isn’t saved / prompted to save draft | Fixed |
| 3 | Only mapped rows (with Item/Payee) end up in Drafts | Fixed |
| 4 | Discard whole draft flaky: sometimes no-op, sometimes only one row | Fixed |

---

## Issue 1 — Save has no feedback and looks like it doesn’t save

### Symptoms

- Toolbar Save click: no overlay / “Saving…” label.
- After Save, incomplete rows stay editable; Drafts badge may not move.
- Successful saves also give **no success toast** (reload is silent).

### Root cause

1. **Busy UI is too weak**  
   `DailyRegister.save_busy_changed` → `QbTxnToolbar.set_mutation_busy` only
   **disables** Save/Submit/Import. No `LoadingOverlay` text (unlike Day Issues
   “Saving filled mappings…”). Fast validation failures never look busy.

2. **Clean save is silent**  
   `_do_save_body` on success: clear local draft → reload → `return True`.
   Comment: `# else: clean save — reload silently, no popup`.

3. **Incomplete rows often never insert** (ties to Issue 3)  
   `_build_transaction_from_row` raises if Item blank (unless
   `defer_item_to_verify` or daily-import meta). Default setting is **Off**.
   Failures become “Save — partial errors” or, if every row is skipped oddly,
   “Nothing to save.”

### Key code

- `tahmeed/ui/cashier/excel_grid.py` — `save_rows`, `_do_save`, `_do_save_body`
- `tahmeed/ui/widgets/qb_txn_toolbar.py` — `set_mutation_busy`
- `excel_grid._build_transaction_from_row` (~1794) Item gate
- Setting `defer_item_to_verify` default `False` in `settings_service` / `api.py`

### Fix size

**Medium.** Overlay + optional short success message; keep button disable.
Item-optional-on-draft is Issue 3 (do together so Save actually works).

### Fix plan

- [x] Show register loading overlay while `_do_save` runs (“Saving draft…”).
- [x] After clean save: “Draft saved” confirmation (skipped when nested under Submit).
- [x] On validation errors: keep dialog; return `False` (see Issue 2).
- [x] Never clear local draft on “Nothing to save” if typed rows remain.

---

## Issue 2 — Merged view still shows unsaved / prompts save draft

### Symptoms

- Enter rows → Save (or try) → switch My → Merged.
- Prompt: “You have unsaved changes. Save as draft before switching mode?”
- Or after “save”, Merged still missing rows / leave prompts fire again.

### Root cause

1. **Incomplete rows never left the grid** (Issue 3) → `has_unsaved_work()` stays
   True → mode switch always prompts.

2. **`_do_save` returns `True` on partial errors**  
   ```text
   if errors:
       … warning …
       _flush_local_draft()
       return True   # callers think save succeeded
   ```
   `_save_then_switch_mode` only aborts when `ok` is False, so it **switches
   mode even when rows failed to save**.

3. **Local draft files are mode-keyed**  
   Path: `{user}_{date}_my.json` vs `{user}_{date}_merged.json`.  
   After a failed/partial save in My, then switch to Merged, restore looks at
   the **merged** file (often empty). Unsaved My rows “disappear” from the
   grid but the My local file may still exist → confusing leave prompts later.

4. **`_save_then_navigate` ignores save result**  
   Always navigates after `await self._do_save()` even if save returned False.

### Key code

- `set_merged_mode` / `_save_then_switch_mode` / `_save_then_navigate`
- `has_unsaved_work`, `_flush_local_draft` / `draft_path(..., merged=)`
- `_prompt_unsaved_work`

### Fix size

**Medium.** Mostly correctness in save return value + navigate/switch guards;
optional local-draft key strategy later.

### Fix plan

- [x] `_do_save_body`: return `False` when any row failed validation/network.
- [x] `_save_then_switch_mode` / `_save_then_navigate`: only proceed when save
      ok and `not has_unsaved_work()`.
- [x] Successful save clears My **and** Merged local draft files for the date
      (`clear_register_drafts_for_date`).
- [x] Incomplete rows now saveable (Issue 3) so mode switch need not prompt.

---

## Issue 3 — Drafts inbox only has mapped (Item/Payee) rows

### Symptoms

- Rows filled via Description Maps / with Item (+ Payee) appear under **Drafts**.
- Unmapped typed/pasted rows do not appear in Drafts after Save.

### Root cause

**Product conflict with Day Issues.**

Day Issues plan: incomplete rows (missing Item and/or Payee) are first-class —
saved **or** unsaved. Save path currently **blocks blank Item**:

```python
allow_blank_item = self._defer_item_to_verify or bool(meta.get("daily_import_id"))
if not item_name and not allow_blank_item:
    raise ValueError("Item is required…")
```

`defer_item_to_verify` defaults to **False**. Payee is never required for insert.
So “mapped” ≈ “has Item” ≈ the only rows that become server drafts.

Daily import rows can save without Item (`daily_import_id` meta); typed rows cannot.

### Key code

- `excel_grid._build_transaction_from_row`
- `settings_service` / accountant Manage Items “Description-only entries”
- Day Issues: incomplete saved drafts are expected in `collect_day_issues`

### Fix size

**Medium / product-sensitive.** Align Save-as-draft with Day Issues: allow blank
Item (and blank Payee) when writing `register_status="draft"`. Keep stricter
rules for Submit-for-Verify if desired (already gated by Day Issues).

### Fix plan

- [x] Allow blank Item on **Save draft** for table rows (typed + paste).
- [x] Truck-required / restrict-items already no-ops when Item blank.
- [x] Unit test: description-only row builds `register_status="draft"`.
- [x] Entry form still uses `defer_item_to_verify` for Item-required gate.

---

## Issue 4 — Discard whole draft flaky / deletes one row

### Symptoms

- Choose discard whole draft: sometimes nothing useful happens.
- Sometimes only **one** row is deleted instead of the whole day.

### Root cause

1. **Selection drives whole vs partial**  
   `should_discard_whole_day`: empty selection or full selection → whole day;
   any proper subset → delete only those IDs.  
   Qt often leaves **one** row selected → partial discard of one ID.
   `_populate_rows` already `clearSelection()` + `clearCurrentIndex()` to fight
   auto-select; focus/click can still re-select before Discard.

2. **Empty `day_row_ids` disables whole-day path**  
   If detail rows haven’t loaded yet (`_rows == []`), `should_discard_whole_day`
   is False → message “Select a draft day…” even though a day **is** selected
   in the left pane — feels like “discard does nothing.”

3. **API / scope**  
   `_do_discard_day` uses `discard_draft_day`; cashier scope without
   `allow_any_cashier` only deletes own rows. `n == 0` shows ownership warning.
   Accountant “show all” path needs `allow_any_cashier=True`.

### Key code

- `tahmeed/ui/cashier/drafts_view.py` — `should_discard_whole_day`,
  `discard_target_ids`, `_on_discard`, `_do_discard_day`, `_populate_rows`
- `tests/test_drafts_discard_targets.py`
- Backend `POST /v1/transactions/drafts/discard-day` + `DRAFT_BASE_MATCH`

### Fix size

**Small–medium.** Prefer explicit whole-day when a day is selected and the
button says “Discard whole day”; don’t trust a single accidental row selection.

### Fix plan

- [x] Day selected + empty detail rows → whole-day API (by date).
- [x] Exactly one of many rows selected → confirm Whole day / This row only.
- [x] Button label stays “Discard whole day” for that ambiguous case.
- [x] Tests updated for empty rows + ambiguous single selection.

---

## Recommended fix order

1. **Issue 3** — allow incomplete draft saves (unblocks 1 & 2).
2. **Issue 1** — save overlay + feedback + safer local-draft clear.
3. **Issue 2** — save return / navigate / mode-switch correctness.
4. **Issue 4** — discard whole-day selection robustness.

---

## Progress log

| Issue | Inspect | Fix | Notes |
|-------|---------|-----|-------|
| 1 | Done | Done | Overlay “Saving draft…” + success toast; safer local clear |
| 2 | Done | Done | Partial save returns False; navigate/switch guarded |
| 3 | Done | Done | Blank Item OK on register Save draft |
| 4 | Done | Done | Whole-day when rows unloaded; confirm on single-row select |

**Shipped in:** `excel_grid` save/overlay/return-value; `register_draft_service.clear_register_drafts_for_date`; `drafts_view` discard helpers; tests `test_defer_item_to_verify`, `test_drafts_discard_targets`.

---

## Out of scope (unless needed while fixing)

- Changing Submit-for-Verify rules beyond existing Day Issues gate.
- Merging My/Merged local draft files into one schema (nice-to-have).
- Accountant Description Maps / Day Issues map UI (already shippable).
