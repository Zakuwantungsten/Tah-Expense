# Compact density + Day Issues description filters

Bring manage / ledger / Day Issues tables closer to current QuickBooks
density, and give Day Issues **both** description filters. **Daily Register
Table (`excel_grid`) is out of scope** — leave its Calibri 11pt look alone.

After each phase: mark it done here, stop, and wait for “go next phase”.

---

## Agreed product rules

- **Density:** slightly smaller type + shorter rows (QB-like), not a redesign.
- **Scope:** all accountant/cashier list tables that share or copy the compact
  table look — **except** Daily Register Table.
- **Description Maps:** same density **and** same table layout pattern as the
  other manage tabs after the density change.
- **Day Issues filters (both):**
  1. Keep / polish toolbar **Filter VALUE…** (automation-style multi-select).
  2. Add **Description ▾** header filter (same Excel checklist UX as Table /
     master ledger Description column).
- Both filters share the same underlying description-key selection model where
  practical (applying either updates the visible set; Clear clears both).
- **No** changes to `excel_grid.py` row height / font / header filters.

### Target tokens (shared)

| Token | Current (typical) | Target |
|-------|-------------------|--------|
| Row height `_ROW_H` | 28 (Items/Suppliers 32) | **24** |
| Header height `_HDR_H` | 26–28 | **22** |
| Cell font | CSS 11px, but many cells use **11pt** QFont | **10px** (`setPixelSize(10)`) |
| Header font | 10px | **9px** |
| Cell padding | `2px 8px` | `1px 6px` |

---

## Phases

### Phase 1 — Shared density source of truth  ✅ DONE

**Goal:** One place defines compact tokens; consumers of
`separate_expenses._table_style` / `_ROW_H` get denser chrome immediately.

**Touch:** `tahmeed/ui/accountant/separate_expenses.py`
(`_ROW_H`, `_HDR_H`, `_table_style` font/padding).

**Done when:**

- [x] Shared `_ROW_H` / `_HDR_H` / stylesheet match the target table above.
- [x] Tabs that only import those helpers (Payees, People, Description Maps,
      Day Issues, Fleet, Reconciliation, etc.) pick up new defaults without
      local overrides fighting them.
- [x] Daily Register Table unchanged.

**Shipped in:** `_ROW_H=24`, `_HDR_H=22`; `_table_style` 10px cells / 9px
header / tighter padding; Afritrack footer uses `_ROW_H`.

**Out of scope this phase:** Items/Suppliers local `_TABLE_SS`, master ledger
local SS, Filter VALUE / Description ▾. Cells that still set
`QFont(..., 11)` pt are Phase 2.

---

### Phase 2 — Fix pt → px cell fonts on shared consumers  ✅ DONE

**Goal:** Cells no longer override stylesheet with `QFont("Segoe UI", 11)`
(points). Use pixel fonts so density matches CSS.

**Touch:** `manage_description_maps.py`, `manage_payees.py`,
`manage_people.py`, `day_issues.py` (and any sibling `_cell` helpers still
using point-size 11).

**Done when:**

- [x] Shared manage / Day Issues cells use `setPixelSize` at the target size.
- [x] Description Maps visually matches Payees/People density.

**Shipped in:** `_CELL_FONT_PX` + `_table_cell_font()` on
`separate_expenses`; `_cell` / `_editable_cell` on Description Maps, Payees,
People, Day Issues use it.

---

### Phase 3 — Align local-copy tables (Items, Suppliers, etc.)  ✅ DONE

**Goal:** Tabs with their own `_ROW_H` / `_TABLE_SS` match the same targets.
Still **not** Daily Register Table.

**Touch (as needed):**

- `manage_items.py`, `manage_suppliers.py` (32 → target)
- `verify_inbox.py`, `fuel_consumption.py`, `category_tables.py`
- `master_ledger_table.py`, `item_quick_report.py` (density only; keep ▾)
- Cashier list views that are **not** `excel_grid` (e.g. drafts / rejected /
  `transactions_table` sub-tables if they use the taller sizes)

**Done when:**

- [x] Those tables use the same row height + px fonts as Phase 1.
- [x] `excel_grid.py` untouched.

**Shipped in:** Items/Suppliers/Verify/Fuel/Category/Master Expenses+Ledger/
Item Quick Report/Truck Overview/transactions_table/cashier_category_view →
24 / 22 / 10px; mono cells use `setPixelSize`; shared `_cell` in
`separate_expenses` + fleet/fuel mono fonts aligned. Skipped register-like
`rejected_view` Calibri 11pt (matches Table look).

---

### Phase 4 — Description Maps layout parity  ✅ DONE

**Goal:** Description Maps table layout matches the manage-tab pattern after
density (columns, stretch, header behavior, toolbar spacing) — not a new
design language.

**Touch:** `manage_description_maps.py` (and compare to `manage_payees.py`).

**Done when:**

- [x] Same density as Phase 1–2.
- [x] Layout feels aligned with Payees (stretch Description, interactive
      Item/Payee, footer/count chrome consistent).
- [x] Existing re-assign / delete / scroll-load behavior unchanged.

**Shipped in:** Header chrome (+ Add in header, hint beside it); toolbar =
search + bulk actions; table headers `# / Description / Item / Payee` with
Stretch Description + Interactive others; Payees-style status bar frame
(scroll-load status, no pager). Test header assertion updated.

---

### Phase 5 — Day Issues: polish Filter VALUE…  ✅ DONE

**Goal:** Toolbar **Filter VALUE…** stays and behaves like automation’s
description VALUE multi-select (searchable checklist if missing, clear
affordance, count chip when active).

**Touch:** `tahmeed/ui/cashier/day_issues.py` (+ small tests).

**Done when:**

- [x] Filter VALUE… still filters by `description_key`.
- [x] Active filter is obvious; Clear VALUE Filter works.
- [x] Works with SEARCH (AND).
- [x] Tests cover filter + search interaction.

**Shipped in:** searchable `ValueFilterDialog` (Select All visible, preserve
checks across search); `None` = pre-check all; `resolve_value_filter_selection`;
toolbar `VALUE · N` chip + Clear only when active; button becomes Edit VALUE…
when active.

---

### Phase 6 — Day Issues: Description ▾ header filter  ✅ DONE

**Goal:** Description column gets the Excel-style ▾ popup used on Table /
master (sort optional; checklist + search + Apply/Clear required).

**Touch:** `day_issues.py` + reuse `excel_column_filter` /
`sortable_ledger_header` patterns; wire to the **same** description-key set as
Filter VALUE… so both stay in sync.

**Done when:**

- [x] ▾ on Description opens checklist of current issue descriptions.
- [x] Apply / Clear updates the same `_value_filter` (or shared store) as
      Filter VALUE….
- [x] Opening Filter VALUE… reflects checks from ▾ and vice versa.
- [x] Daily Register Table filter code paths not required to change.
- [x] Tests cover ▾ apply/clear ↔ VALUE filter parity.

**Shipped in:** `ExcelFilterHeaderView` on Description; helpers
`value_filter_from_descriptions` /
`description_displays_for_value_filter`; ▾ ↔ toolbar VALUE chrome share
`_value_filter`; A→Z / Z→A sort on Description.

---

### Phase 7 — Smoke tests + visual checklist  ✅ DONE

**Goal:** Regression safety and a short manual checklist.

**Done when:**

- [x] Existing Day Issues selection / map tests still pass.
- [x] New density/filter tests green.
- [x] Manual checklist recorded below (`excel_grid` confirmed untouched in git).

**Automated smoke (40 passed):**
`test_day_issues_selection`, `styles`, `map_apply`, `save_filled`,
`cashier_scope`, `live_rows`, `all_days`, `description_maps_reassign`,
`register_column_filters`, `verify_item_payee_gate`.

**Manual checklist (please confirm in the running app):**

- [ ] Description Maps / Payees / Items look denser (shorter rows, smaller type)
- [ ] Daily Register **Table** still Calibri 11pt / prior look
- [ ] Day Issues **Filter VALUE…** searchable + chip when active
- [ ] Day Issues Description **▾** Apply/Clear stays in sync with VALUE
- [ ] Day Issues SEARCH + VALUE still AND together; map actions still work

---

## Progress log

| Phase | Status | Notes |
|-------|--------|-------|
| 1 | Done | Shared `_ROW_H=24` / `_HDR_H=22` / 10px+9px stylesheet |
| 2 | Done | `_table_cell_font()` 10px on Maps / Payees / People / Day Issues |
| 3 | Done | Local `_TABLE_SS` / `_ROW_H` copies → 24/22/10px |
| 4 | Done | Maps chrome aligned with Payees/People manage pattern |
| 5 | Done | Searchable VALUE dialog + active chip |
| 6 | Done | Description ▾ synced with VALUE filter |
| 7 | Done | 40 related tests green; manual checklist for UI spot-check |

---

## Explicit non-goals

- Daily Register Table (`excel_grid`) fonts, row height, or header filters
- Changing Day Issues mapping / apply / scope rules (Phases 1–7 of
  `DAY_ISSUES_PLAN.md` stay as shipped)
- Ignore / Unignore
