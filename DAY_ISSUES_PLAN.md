# Day Issues — Automation Parity Plan

Guidance for implementing Day Issues so it matches Expense Automation
(without Ignore), including unsaved register rows and Verify redirect.

After each phase: mark it done here, stop, and wait for “go next phase”.

---

## Agreed product rules

- Day Issues = register rows missing **Item and/or Payee** (saved **or** unsaved).
- When a row gets both Item and Payee, it **leaves Day Issues dynamically**.
- **Cashier:** own rows only. **Accountant:** all cashiers.
- Mapping writes **Description Maps** and applies Payee + Item onto matching
  saved **and** unsaved rows.
- **Not** Master Expenses rows.
- **All days** = incomplete register issues across days (not complete rows, not Master).
- Verify approve blocked if Item/Payee missing → point accountant to Day Issues.
- **No** Ignore / Unignore.

---

## Phases

### Phase 1 — Surface unsaved + saved incomplete rows  ✅ DONE

**Goal:** Day Issues (and badge) show every incomplete description for the
selected day from **saved DB rows and unsaved/staged Table rows**. Completing
Item + Payee on a row removes it on refresh (live when editing the Table).

**Done when:**

- [x] Import / type rows without Item+Payee → appear in Day Issues for that date
      (even before Save).
- [x] Filling Item+Payee on the Table (or after map apply to saved rows) drops
      those descriptions out of Day Issues / badge after refresh.
- [x] Master / verified-only rows still excluded (open register rows only).
- [x] Tests cover unsaved (no `_id`) incomplete vs complete behavior.

**Out of scope this phase:** cashier-vs-accountant filter, apply-to-unsaved on
map, All days UI, inline edit, search/filter/checkboxes, Verify redirect polish.

**Shipped in:** `day_issues_service.merge_transactions_for_day_issues` +
`extra_transactions`; `DailyRegister.snapshot_transactions_for_day_issues` /
`day_issues_row_source` / `day_issues_source_changed`; cashier + accountant
dashboards wired; `tests/test_day_issues_live_rows.py`.

---

### Phase 2 — Cashier vs accountant scope  ✅ DONE

**Goal:** Cashier Day Issues = that cashier only. Accountant = all cashiers.
Badge counts follow the same rule.

**Done when:**

- [x] Cashier never sees another cashier’s incomplete descriptions.
- [x] Accountant sees everyone’s for the scoped day(s).

**Shipped in:** `collect_day_issues(..., cashier_id=)`;
`filter_rows_for_cashier_scope`; Day Issues widget scopes by `user.role`;
cashier/accountant badge refresh; map-apply respects cashier scope;
`tests/test_day_issues_cashier_scope.py`.

---

### Phase 3 — Map applies to saved + unsaved + Description Maps  ✅ DONE

**Goal:** Map Selected / Map All saves Description Maps and stamps Payee + Item
onto matching **saved and unsaved** rows for the day; issues clear dynamically.

**Done when:**

- [x] Maps appear under accountant **Description Maps**.
- [x] Unsaved staged/typed rows update in the Table without requiring Save first.
- [x] Saved open rows update via existing update path.

**Shipped in:** `DailyRegister.apply_description_mapping_to_open_rows` (live grid +
draft); Day Issues `apply_live` + `save_mapping`; no full Table refresh after map
(preserves unsaved); `apply_mapping_to_draft_payload`;
`tests/test_day_issues_map_apply.py`.

---

### Phase 4 — Verify approve gate → Day Issues  ✅ DONE

**Goal:** Accountant cannot approve rows missing Item/Payee; UI redirects /
opens Day Issues to map first.

**Done when:**

- [x] Approve (single/bulk) blocked when mapping still needed.
- [x] Message offers / navigates to Day Issues for the relevant date(s).

**Shipped in:** Verify `_ensure_item_and_payee` → “Open Day Issues” dialog;
`open_day_issues` signal → accountant dashboard `_on_open_day_issues`;
preferred register day from `import_primary_date` / `date`;
`tests/test_verify_item_payee_gate.py` updated.

---

### Phase 5 — This day / All days scope  ✅ DONE

**Goal:** Scope control like automation. All days = incomplete register issues
across days only (no Master, no already-complete rows).

**Done when:**

- [x] This day / All days selector on Day Issues (cashier + accountant).
- [x] Counts and map actions respect scope + Phase 2 ownership rules.

**Shipped in:** `GET /v1/transactions/open-day-summaries`;
`collect_day_issues_all_days` + `merge_day_issue_snapshots`; Day Issues SHOW
combo; map apply across issue dates; `tests/test_day_issues_all_days.py`.

---

### Phase 6 — Inline Payee / Item + Save Filled Mappings  ✅ DONE

**Goal:** Edit Payee and Item inline on issue rows; **Save Filled Mappings**
commits completed drafts (same effect as Phase 3 map).

**Done when:**

- [x] Inline edit works on Unmapped / Bad map rows.
- [x] Save Filled writes Description Maps and applies to rows.
- [x] Incomplete drafts do not commit.

**Shipped in:** editable Payee/Item columns + completer delegates; draft store;
Save Filled Mappings (both fields required); applies via existing map path;
`tests/test_day_issues_save_filled.py`.

---

### Phase 7 — Search, VALUE filter, checkboxes, richer multi-select  ✅ DONE

**Goal:** Automation-parity selection UX (without Ignore):

- Search
- Filter VALUE…
- Checkboxes / Check Highlighted / Clear Checks
- Map from checked **and/or** highlighted selection

**Done when:**

- [x] All of the above work on cashier and accountant Day Issues.
- [x] Map Selected uses the richer selection model.

**Shipped in:** SEARCH box; Filter VALUE… dialog; ☐ column + Check Highlighted /
Clear Checks; `map_target_issues` union for Map Selected; shared widget used by
cashier + accountant; `tests/test_day_issues_selection.py`.

---

## Progress log

| Phase | Status | Notes |
|-------|--------|-------|
| 1 | Done | Unsaved + saved incomplete rows; live badge/refresh |
| 2 | Done | Cashier = own rows; accountant = all |
| 3 | Done | Map → Description Maps + apply unsaved/saved |
| 4 | Done | Verify block + Open Day Issues |
| 5 | Done | This day / All days scope |
| 6 | Done | Inline Payee/Item + Save Filled |
| 7 | Done | Search / VALUE filter / checkboxes |

---

## Performance — stop redundant Day Issues scans

Problem: tab switch / map / Table edits each force a full `collect_day_issues`
(+ overlay + table rebuild). Badge often runs a second full collect in parallel.

### Perf Phase A — Dirty flag + smart tab open  ✅ DONE

- Widget tracks `_loaded` / `_dirty`.
- Nav / `set_date`: full scan only if date changed, dirty, or never loaded.
- Manual **Refresh** always scans.
- Table/source changes mark dirty when Day Issues is not visible.

### Perf Phase B — Debounce + one scan when visible  ✅ DONE

- Coalesce `day_issues_source_changed` (~350ms).
- When Day Issues is visible: one widget `refresh`; badge from `issues_changed`.
- When not visible: debounced badge-only count.

### Perf Phase C — Map / Save Filled without cascade  ✅ DONE

- Live apply does not emit source-changed when Day Issues will refresh itself.
- Single post-map update (no double/triple collect).

### Perf Phase D — Lighter refresh + incremental drop  ✅ DONE

- Reuse cached categories/payees across scans.
- After map: drop resolved keys from local list immediately (badge updates).

### Perf Phase E — All-days per-day cache  ✅ DONE

- Cache per-day snapshots; re-scan only dirty / live day after edits.

**Shipped in:** `DayIssuesWidget` dirty/debounce/cache; cashier + accountant
dashboard wiring; `emit_source_changed=False` on map apply; `day_cache` on
`collect_day_issues_all_days`; `tests/test_day_issues_perf.py`.

### Perf follow-up — paste must update badge  ✅ DONE

Paste/clear/fill-down used `blockSignals` + `_bulk_mutating`, so
`day_issues_source_changed` never fired until you opened Day Issues.
Fix: coalesce — suppress per-cell, then **one** `_flush_day_issues_source_changed`
after bulk paste (and related bulk edits). Sidebar badge updates via the
existing debounced path while you stay on the Table.

**Shipped in:** `excel_grid._flush_day_issues_source_changed`;
`tests/test_day_issues_paste_badge.py`.

### Auto-fill from Description Maps (paste + setting)  ✅ DONE

- Setting `auto_fill_from_description_maps` (default **On**).
- Typing already auto-filled; **paste / fill-down** now batch-apply maps to
  blank Item/Payee only (does not overwrite typed values; import staging unchanged).
- Day Issues checkbox toggles the setting and updates the live Table cache.
- Partial maps fill what they have; incomplete rows still appear in Day Issues
  with known fields available to finish.

**Shipped in:** `excel_grid._auto_fill_rows_from_mappings`; Day Issues checkbox;
`tests/test_auto_fill_paste_from_maps.py`.
