# Day Issues / Payee — Remaining Implementation Phases

Follow-up to the Day Issues + Payee catalog work already landed.
Each phase is implemented one at a time. After each phase: summary → wait for go-ahead → next phase.

**Already done (baseline):**
- Payee catalog CRUD + IIF import (`Manage → Payees`)
- Day Issues tab (cashier + accountant) with bulk Map Selected → Payee + Item
- Sidebar badge + Table toolbar shortcut
- Submit blocked when Day Issues remain (`allow_submit_with_day_issues` toggle)
- Description mapping docs can store `payee`
- Cashier daily import skips one-by-one dialog and leaves rows for Day Issues

**Goal of this plan:** make Payee first-class in daily entry, Description Maps, and Verify — and retire the remaining one-by-one mapping dialogs.

---

## Phase 1 — Register: auto-fill Payee with Item from mapping  ✅ DONE

When the cashier types/accepts a description that already has a saved map, fill **both** Item and Payee (not Item only).

### Behaviour
- On description commit (same hook as today’s `_kick_auto_fill_item`):
  1. Look up description mapping.
  2. If mapping has `category_name` / item → fill Item cell (existing).
  3. If mapping has `payee` → fill Payee cell when Payee is currently blank.
- Do **not** overwrite a Payee the cashier already typed.
- Same rules as Item: only auto-fill empty cells; leave verified rows alone if already protected by edit mode.

### Files & changes
- `tahmeed/services/description_mapping_service.py`
  - Extend `resolve_category_for_description` **or** add
    `resolve_mapping_for_description(description) -> Optional[DescriptionMapping]`
    so callers get `category_name` + `payee` in one lookup.
- `tahmeed/services/cashier_service.py`
  - Replace/extend `resolve_item_name_for_description` with e.g.
    `resolve_mapping_fields_for_description(description) -> tuple[Optional[str], Optional[str]]`
    returning `(item_name, payee)`.
  - Keep item-only helper as a thin wrapper if other call sites need it.
- `tahmeed/ui/cashier/excel_grid.py`
  - Rename/extend `_auto_fill_item_from_mapping` → `_auto_fill_from_mapping`.
  - After setting Item, set `COL_PAYEE` when blank and mapping payee is non-empty.
- `tahmeed/ui/cashier/rejected_view.py` (if it auto-fills on description) — mirror the same behaviour.

### Tests
- Unit: mapping with payee returns both fields; mapping without payee returns item only.
- Grid-level (or service-level) test: blank payee gets filled; non-blank payee is preserved.

### Acceptance
- Enter a description that was mapped via Day Issues → Item **and** Payee populate.
- Typing over an existing Payee does not wipe it on description re-edit.

---

## Phase 2 — Register: Payee column autocomplete from Payee list  ✅ DONE

Payee suggestions come from the accountant-managed payee catalog (IIF-seeded), same interaction style as Ownership / APR BY (or Item).

### Behaviour
- Load active payee names on register open / reload settings.
- Payee column uses a completer delegate (contains / prefix search, uppercase normalize on commit — match existing `payee=…upper()` save path).
- Free text still allowed unless a future `restrict_payees` setting is added (**out of scope** for this phase — suggestions only, like People).

### Files & changes
- `tahmeed/ui/cashier/excel_grid.py`
  - Add `_payee_names: list` + `update_payees()` / `_load_payee_names()` using `payee_service.get_payee_names()`.
  - Attach `_ItemDelegate` (or shared completer delegate) to `COL_PAYEE`, same pattern as `COL_OWN` / `COL_APR`.
  - Call `_load_payee_names()` from `reload_settings` / init alongside people.
- `tahmeed/ui/cashier/dashboard.py` (and accountant table page if it refreshes people)
  - Optionally refresh payee cache when returning to Table (mirror people load).
- `tahmeed/ui/cashier/rejected_view.py` / `entry_form.py`
  - If those surfaces expose Payee, wire the same list for consistency.
- `tahmeed/services/payee_service.py`
  - Already has `search_payees_sync` / `get_payee_names` — reuse; warm cache on dashboard load if useful.

### Tests
- Completer source includes imported IIF names after `get_payee_names`.
- Saving a row still uppercases payee.

### Acceptance
- Typing in Payee shows catalog matches (e.g. `SPARE PARTS`, `PARKING DAR`).
- Unknown typed payee still saves (no hard block in this phase).

---

## Phase 3 — Description Maps UI: show and edit Payee  ✅ DONE

Description Maps must display and edit **Description → Payee + Item**, not Item alone.

### Behaviour
- Table columns: `#`, `Description`, `Payee`, `Item / Supplier` (or Item).
- Add / Edit dialog: Payee combo (from payee catalog) + Item combo (existing).
- Bulk **Re-assign Selected**: set the same Payee + Item on all selected description keys.
- Empty payee on legacy maps is allowed to display as `—`; editing should encourage filling it (warn if saving Item without Payee when Day Issues would treat it as Bad map).

### Files & changes
- `tahmeed/ui/accountant/manage_description_maps.py`
  - `_MappingEditorDialog`: add Payee field (typeahead over `get_payee_names`); include payee in `MappingAssignment` or parallel `result_payee`.
  - Table header + populate: Payee column.
  - `_save_assignment` / `_do_reassign`: call `save_mapping(..., payee=…)`.
- `tahmeed/services/mapping_assignment_service.py`
  - Extend `MappingAssignment` with `payee: str = ""`.
  - `apply_assignment_to_descriptions` passes payee into `save_mapping`.
- `tahmeed/models/description_mapping.py` — already has `payee`; ensure list/API round-trip (desktop `from_doc` already reads it).
- Backend: `MappingWrite.payee` already exists — confirm list responses include `payee` for older docs (default `""`).

### Tests
- Re-assign N maps updates `payee` + `category_name` on each.
- Editor requires Item; Payee optional-with-warn or required (prefer **required** to match Day Issues Save rules).

### Acceptance
- Description Maps page shows Payee.
- Bulk re-assign changes Payee + Item together.
- New map from this UI is picked up by Phase 1 auto-fill.

---

## Phase 4 — Retire one-by-one mapping dialogs (remaining flows)  ✅ DONE

**Decision:** Master imports use policy **A** (blocked until unmapped = 0).

Cashier Table import already skips the dialog. Remove the same pattern everywhere else so Day Issues (or Description Maps) is the only bulk mapping UX.

### Flows to change

| Flow | Current | Target |
|------|---------|--------|
| Cashier Table Import | Day Issues (done) | Keep |
| Accountant **Import Daily → Master** | `prompt_unmapped_descriptions` one-by-one | Skip unmapped → import / stage rows without item+payee; surface Day Issues (or block Master commit until mapped — see note) |
| Verify inbox `_resolve_items_for_transactions` | One-by-one `DescriptionMappingDialog` | **Do not prompt.** Block approve if missing item/payee (Phase 5). Optional: “Open Day Issues” button |
| Master Excel import (`master_expenses`) | One-by-one | Same as Import Daily: no dialog; leave unmapped for Day Issues / Description Maps |

**Note on Import Daily → Master:** Master rows may not sit on a cashier “day” the same way. Prefer:
1. If the upload is tied to a register / reconciled date → Day Issues for that date.
2. Else require mappings via Description Maps / a scoped Day Issues view before commit, **without** per-description modal loop.

### Files & changes
- `tahmeed/ui/accountant/daily_to_master_flow.py`
  - Remove `prompt_unmapped_descriptions` call.
  - On unmapped: either abort with message “Map in Day Issues first” **or** import with blank item/payee and open Day Issues (product choice — **recommend abort for Master**, soft-import for cashier register only).
- `tahmeed/ui/accountant/master_expenses.py` — same policy for master Excel import.
- `tahmeed/ui/accountant/verify_inbox.py`
  - Strip `_resolve_items_for_transactions` dialog loop (replaced by Phase 5 hard gate).
- `tahmeed/ui/dialogs/description_mapping_flow.py` / `description_mapping_dialog.py`
  - Keep temporarily for Description Maps “Add” if reused; otherwise deprecate call sites until unused, then delete in a cleanup PR.

### Decision (confirmed)
- **A:** Import Daily → Master **blocked** until unmapped count is 0 (user maps in Day Issues / Description Maps first).
- ~~B: Import with blanks and rely on Day Issues + Verify gate.~~

### Acceptance
- No modal “Map description to item” loop during Import Daily, Master import, or Verify.
- Cashier import still lands rows and raises Day Issues badge.

---

## Phase 5 — Verify: cannot approve without Item + Payee  ✅ DONE

Accountant Verify must not approve (single or bulk) entries that lack a mapped/assigned **Item** and **Payee**.

### Behaviour
- Before approve / bulk approve / re-approve:
  - Fail if `item`/`category_name` blank **or** `payee` blank.
- Apply saved mappings quietly when both fields exist on the map (fill blanks on the tx, then proceed) — **no dialog**.
- If still incomplete after applying maps: show one summary dialog  
  `N entries missing Item and/or Payee` + optional **Open Day Issues** for the date(s).
- Detail panel: hide / disable “map on approve” combo path; show warning chip when payee or item missing.
- Align with Day Issues: verified rows drop out of issues (already true once `verified=True`).

### Files & changes
- `tahmeed/services/description_mapping_service.py`
  - Add `transaction_needs_payee(payee)` and/or
    `transaction_needs_mapping(item, category_name, payee) -> bool`.
- `tahmeed/ui/accountant/verify_inbox.py`
  - Replace `_resolve_items_for_transactions` with `_ensure_item_and_payee(txs) -> bool`:
    - Load mappings for descriptions.
    - Bulk-patch missing item/payee from mapping when complete.
    - If any remain incomplete → warn + return False (no prompts).
  - Wire into `_do_approve`, `_do_bulk_approve`, re-approve paths.
  - Remove “— map on approve —” / category-only approve shortcut that bypasses payee.
- Backend (optional harden): reject verify API if payee/item empty — belt-and-suspenders after UI gate.

### Tests
- Approve blocked when item set but payee empty.
- Approve succeeds when mapping supplies both and rows are patched first.
- Bulk approve with mixed complete/incomplete fails as a batch (no partial silent approve) — match existing bulk semantics.

### Acceptance
- Cannot Approve Selected / Approve if any selected row lacks Item or Payee.
- No one-by-one mapping dialogs appear from Verify.

---

## Phase order & dependencies

```
Phase 1 (auto-fill payee) ──┐
Phase 2 (payee autocomplete)├── can run in parallel after baseline
Phase 3 (Description Maps UI)┘
         │
         ▼
Phase 4 (remove one-by-one dialogs) ── needs Phase 3 UX ready for accountants
         │
         ▼
Phase 5 (Verify gate) ── needs Phase 4 (no dialog fallback) + mapping payee data
```

Suggested ship order: **1 → 2 → 3 → 4 → 5**.

---

## Out of scope (later)

- ~~`restrict_payees` hard validation (catalog-only payees)~~ — implemented (QuickBooks-style Cancel / Add Payee when Restrict payees is on)
- Seeding description maps from QuickBooks `description_memory.json` (Payee + Account → Payee + Item)
- Ignoring descriptions (explicitly excluded earlier)
- Changing CoA / Item import behaviour

---

## Quick checklist per phase

| Phase | Done when |
|-------|-----------|
| 1 | Description entry fills Item **and** Payee from map |
| 2 | Payee column suggests catalog names |
| 3 | Description Maps shows/edits/bulk-sets Payee |
| 4 | No remaining one-by-one map dialogs in import/verify |
| 5 | Verify refuses approve without Item + Payee ✅ |
