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

### Phase 1 — Shared density source of truth  ⬜

**Goal:** One place defines compact tokens; consumers of
`separate_expenses._table_style` / `_ROW_H` get denser chrome immediately.

**Touch:** `tahmeed/ui/accountant/separate_expenses.py`
(`_ROW_H`, `_HDR_H`, `_table_style` font/padding).

**Done when:**

- [ ] Shared `_ROW_H` / `_HDR_H` / stylesheet match the target table above.
- [ ] Tabs that only import those helpers (Payees, People, Description Maps,
      Day Issues, Fleet, Reconciliation, etc.) pick up new defaults without
      local overrides fighting them.
- [ ] Daily Register Table unchanged.

**Out of scope this phase:** Items/Suppliers local `_TABLE_SS`, master ledger
local SS, Filter VALUE / Description ▾.

---

### Phase 2 — Fix pt → px cell fonts on shared consumers  ⬜

**Goal:** Cells no longer override stylesheet with `QFont("Segoe UI", 11)`
(points). Use pixel fonts so density matches CSS.

**Touch:** `manage_description_maps.py`, `manage_payees.py`,
`manage_people.py`, `day_issues.py` (and any sibling `_cell` helpers still
using point-size 11).

**Done when:**

- [ ] Shared manage / Day Issues cells use `setPixelSize` at the target size.
- [ ] Description Maps visually matches Payees/People density.

---

### Phase 3 — Align local-copy tables (Items, Suppliers, etc.)  ⬜

**Goal:** Tabs with their own `_ROW_H` / `_TABLE_SS` match the same targets.
Still **not** Daily Register Table.

**Touch (as needed):**

- `manage_items.py`, `manage_suppliers.py` (32 → target)
- `verify_inbox.py`, `fuel_consumption.py`, `category_tables.py`
- `master_ledger_table.py`, `item_quick_report.py` (density only; keep ▾)
- Cashier list views that are **not** `excel_grid` (e.g. drafts / rejected /
  `transactions_table` sub-tables if they use the taller sizes)

**Done when:**

- [ ] Those tables use the same row height + px fonts as Phase 1.
- [ ] `excel_grid.py` untouched.

---

### Phase 4 — Description Maps layout parity  ⬜

**Goal:** Description Maps table layout matches the manage-tab pattern after
density (columns, stretch, header behavior, toolbar spacing) — not a new
design language.

**Touch:** `manage_description_maps.py` (and compare to `manage_payees.py`).

**Done when:**

- [ ] Same density as Phase 1–2.
- [ ] Layout feels aligned with Payees (stretch Description, interactive
      Item/Payee, footer/count chrome consistent).
- [ ] Existing re-assign / delete / scroll-load behavior unchanged.

---

### Phase 5 — Day Issues: polish Filter VALUE…  ⬜

**Goal:** Toolbar **Filter VALUE…** stays and behaves like automation’s
description VALUE multi-select (searchable checklist if missing, clear
affordance, count chip when active).

**Touch:** `tahmeed/ui/cashier/day_issues.py` (+ small tests).

**Done when:**

- [ ] Filter VALUE… still filters by `description_key`.
- [ ] Active filter is obvious; Clear VALUE Filter works.
- [ ] Works with SEARCH (AND).
- [ ] Tests cover filter + search interaction.

---

### Phase 6 — Day Issues: Description ▾ header filter  ⬜

**Goal:** Description column gets the Excel-style ▾ popup used on Table /
master (sort optional; checklist + search + Apply/Clear required).

**Touch:** `day_issues.py` + reuse `excel_column_filter` /
`sortable_ledger_header` patterns; wire to the **same** description-key set as
Filter VALUE… so both stay in sync.

**Done when:**

- [ ] ▾ on Description opens checklist of current issue descriptions.
- [ ] Apply / Clear updates the same `_value_filter` (or shared store) as
      Filter VALUE….
- [ ] Opening Filter VALUE… reflects checks from ▾ and vice versa.
- [ ] Daily Register Table filter code paths not required to change.
- [ ] Tests cover ▾ apply/clear ↔ VALUE filter parity.

---

### Phase 7 — Smoke tests + visual checklist  ⬜

**Goal:** Regression safety and a short manual checklist.

**Done when:**

- [ ] Existing Day Issues selection / map tests still pass.
- [ ] New density/filter tests green.
- [ ] Manual: Description Maps, Payees, Items, Day Issues look denser; Table
      register looks unchanged; both Day Issues filters work together.

---

## Progress log

| Phase | Status | Notes |
|-------|--------|-------|
| 1 | Pending | Shared `_ROW_H` / `_table_style` |
| 2 | Pending | pt → px on shared consumers |
| 3 | Pending | Items / Suppliers / local SS copies |
| 4 | Pending | Description Maps layout parity |
| 5 | Pending | Polish Filter VALUE… |
| 6 | Pending | Description ▾ + sync with VALUE |
| 7 | Pending | Tests + checklist |

---

## Explicit non-goals

- Daily Register Table (`excel_grid`) fonts, row height, or header filters
- Changing Day Issues mapping / apply / scope rules (Phases 1–7 of
  `DAY_ISSUES_PLAN.md` stay as shipped)
- Ignore / Unignore
