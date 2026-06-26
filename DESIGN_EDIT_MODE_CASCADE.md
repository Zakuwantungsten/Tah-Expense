# Edit Mode, Save/Export Controls & Cascade Update System
## Design Document — Problems and Fixes Required

---

## 1. Context: How the App Currently Works

### Transaction Lifecycle (Current)

```
Cashier types row → auto-saves on row exit → DB insert → accountant inbox → approve → master expenses
```

A `Transaction` document in MongoDB is the single source of truth. Every downstream view
(cashier category tabs, accountant verify inbox, master expenses ledger, overview KPIs) reads
from the `transactions` collection with different filters:

| View | Query filter | Purpose |
|---|---|---|
| `DailyRegister` (excel_grid.py) | `date + cashier_id` | Cashier's own daily entries |
| `CashierCategoryView` | `cashier_id + category_name` | Per-item sidebar tab |
| `CashierOverview` | today / month aggregate | Cashier dashboard stats |
| `VerifyInboxWidget` | `verified=False` | Accountant approval queue |
| `MasterExpenses` | `verified=True + year/month` | Verified ledger |
| `AccountantOverview` | aggregate (verified ratio, YTD totals) | Accountant KPIs |

### Auto-Save (Recently Added — To Be Removed)

The following was recently built and must be **completely removed**:

| File | What to remove |
|---|---|
| `excel_grid.py` | `_autosave_row()`, `_on_current_cell_changed()`, `_on_retry_timer()`, `_freeze_row()`, `_retry_queue`, `_retry_timer` state vars, `currentCellChanged` connection in `_build_ui` |
| `signals.py` | Entire file (`app_signals.transaction_saved`) — or repurpose once new save is built |
| `verify_inbox.py` | `app_signals.transaction_saved.connect(self._debounce.start)` line |

The `_build_transaction_from_row()` method can be **kept** — it is reused by the new explicit Save.

---

## 2. Problem: No Explicit Save / Export / Edit Controls

### Current state
- `save_rows()` method exists on `DailyRegister` but is **never called** from the UI.
- There is no Save button, no Export button, no Edit toggle.
- The cashier has no way to commit new rows from the table (only via `EntryForm`).

### What is needed

Three buttons to be added to the header area (`_QBDocHeader` in `dashboard.py` or a new
action bar between the header and the table):

#### Button 1 — SAVE
- Commits **all pending changes** in a single action.
- Covers two scenarios:
  1. **New rows** (editable rows with data below `_saved_count`) → `INSERT` into DB.
  2. **Edited saved rows** (rows modified while in Edit mode) → `UPDATE` existing DB documents.
- Shows a non-blocking result summary (e.g. "5 saved, 2 updated").
- After save, reloads the current date view.

#### Button 2 — EXPORT
- Exports the current register view (current date's rows) to Excel/CSV.
- Uses the existing `QFileDialog` import already present in `excel_grid.py`.
- Should respect active search/column filters (export only visible rows).

#### Button 3 — EDIT (toggle)
- Toggles the entire table between **read-only mode** (default) and **full edit mode**.
- In edit mode, ALL saved rows across ALL dates become editable (not just the current date).
- A visual banner or row background change distinguishes edit mode clearly.
- A second click on Edit (or a Cancel button) exits edit mode and **discards unsaved changes**
  (restores original cell values by reloading from DB).

---

## 3. Problem: Saved Rows Are Permanently Read-Only

### Current state
`_fill_saved_row()` sets all cell flags to `Qt.ItemIsEnabled | Qt.ItemIsSelectable`
(no `Qt.ItemIsEditable`). There is no mechanism to re-enter edit mode on a saved row.

### What is needed

#### 3a. Edit Mode Toggle in DailyRegister

New state variable: `self._edit_mode: bool = False`

When Edit is activated:
- Call `_enter_edit_mode()`: iterate all saved rows, change cell flags to include
  `Qt.ItemIsEditable`, change row background to a warm yellow `#FFFBEB` to signal editability.
- Track which rows have been modified: `self._dirty_rows: set[int]` (populated via
  `_on_item_changed` when row < `_saved_count`).
- The `COL_NOTES` ref-float checkbox and `COL_RECEIPT` badge delegates already handle
  click/toggle — they only need `Qt.ItemIsEditable` to activate.

When Edit is deactivated without saving:
- Call `_exit_edit_mode(discard=True)`: reload current date from DB, restore read-only flags.
- Clear `_dirty_rows`.

When Save is pressed while in Edit mode:
- Call `_exit_edit_mode(discard=False)`: commit all dirty rows first (see section 5),
  then reload and restore read-only flags.

#### 3b. Date Navigation in Edit Mode

When the user navigates to a different date while in Edit mode:
- Prompt: "You have unsaved changes. Save before leaving?" (Yes / Discard / Cancel).
- On Yes: run save, then navigate.
- On Discard: clear dirty state, navigate.
- On Cancel: stay on current date.

---

## 4. Problem: No `update_transaction` Service Function

### Current state
`cashier_service.py` only has `save_transaction()` which does `insert_one`. There is no
`update_one` path.

### What is needed

#### New function in `cashier_service.py`

```python
async def update_transaction(tx_id: ObjectId, updates: dict) -> bool:
    db = get_db()
    result = await db.transactions.update_one(
        {"_id": tx_id},
        {"$set": updates}
    )
    return result.modified_count == 1
```

The `updates` dict is built from the edited row's cell values (same field extraction logic
as `_build_transaction_from_row`, minus `cashier_id` and `created_at` which never change).

Fields that can be updated by a cashier edit:
- `date`, `description`, `item`, `category_name`, `truck_number`, `amount`, `currency`,
  `memo`, `receipt_status`, `notes_flag`, `ownership`, `approver`

Fields that must NOT be overwritten on update:
- `cashier_id`, `created_at`, `_id`, `verified`, `verified_by`, `verified_at`,
  `rejection_reason` (these are managed by the accountant or are immutable audit fields)

Additional fields to add to the update payload (new — see section 6):
- `last_edited_at`: `datetime.utcnow()`
- `last_edited_by`: the cashier's `_id`

---

## 5. Problem: Editing an Already-Verified Row Has No Re-Verification Path

### Current state
When a cashier edits a row that the accountant has already approved (`verified=True`), the
change silently overwrites the verified data in MongoDB. The accountant sees nothing.
The master expenses ledger shows the updated data without any flag. There is no audit trail.

### What is needed

#### 5a. New Transaction Model Fields

Add to `tahmeed/models/transaction.py`:

```python
edited_after_verification: bool = False   # True if cashier edited a previously verified row
last_edited_at: Optional[datetime] = None # Timestamp of last cashier edit
last_edited_by: Optional[ObjectId] = None # Cashier who made the edit
```

Update `to_doc()` and `from_doc()` accordingly.

#### 5b. Update Save Logic for Dirty Verified Rows

In `DailyRegister` save path, when committing a dirty row that has `verified=True`:
1. Build the `updates` dict from cell values.
2. Add `edited_after_verification=True`, `last_edited_at=now`, `last_edited_by=cashier._id`.
3. Add `verified=False` — the approval is revoked because the data changed.
4. Call `update_transaction(tx_id, updates)`.
5. The row now reappears in the accountant's verify inbox.

For dirty rows that had `verified=False` (never approved or previously rejected):
- Build same `updates` dict but do NOT set `edited_after_verification=True` or reset `verified`.
- Call `update_transaction(tx_id, updates)`.

#### 5c. New Accountant Service Functions

Add to `tahmeed/services/accountant_service.py`:

```python
async def get_edited_transactions(search, truck, cashier_id, date_from, date_to, limit, skip):
    # Query: verified=False AND edited_after_verification=True
    # Same filter structure as get_unverified_filtered

async def count_edited_transactions(...):
    # Count version of the above

async def re_approve_transaction(tx_id, accountant_id):
    # Same as approve_transaction but also clears edited_after_verification
    # Sets: verified=True, verified_by, verified_at, edited_after_verification=False

async def bulk_re_approve_transactions(tx_ids, accountant_id):
    # Bulk version of re_approve
```

---

## 6. Problem: Accountant Verify Tab Has No "Edited" Sub-Tab

### Current state
`VerifyInboxWidget` (`verify_inbox.py`) is a single flat list of all unverified transactions.
There is no way to distinguish between:
- Fresh entries (never been approved before)
- Edited entries (were approved, then cashier changed them)

### What is needed

#### 6a. Sub-tab Structure in VerifyInboxWidget

Replace the current single table with a two-tab layout at the top of the widget:

| Tab | Label | Query |
|---|---|---|
| Tab 0 | **New** (or "Inbox") | `verified=False AND edited_after_verification=False` (or field missing) |
| Tab 1 | **Edited** | `verified=False AND edited_after_verification=True` |

The tab labels show a count badge: e.g., **New (14)** and **Edited (3)**.

Both tabs share the same filter controls (search, truck, cashier, date range).
Only the underlying query changes.

#### 6b. Edited Tab — Visual Differences

The "Edited" tab rows should show additional context:
- `last_edited_at` — "Edited 2 hours ago" or exact datetime
- `last_edited_by` — Cashier name who made the edit
- A subtle orange left border or row highlight to visually distinguish from new entries

#### 6c. Re-Approve Action

On the "Edited" tab, the approve action calls `re_approve_transaction()` instead of
`approve_transaction()`, which clears `edited_after_verification=False` in addition to
setting `verified=True`.

On re-approval, the updated transaction data cascades automatically to:
- **Master Expenses** — next query sees the updated fields (amount, category, date, etc.)
- **Cashier Category View** — next load for that category shows updated data
- **Cashier Overview stats** — next refresh recalculates aggregates
- **Accountant Overview KPIs** — next refresh reflects updated totals

No explicit cascade code is needed because all downstream views query the DB fresh on load.
The cascade is implicit — re-approval sets `verified=True`, so the row reappears in master
expenses with its updated field values.

---

## 7. Problem: `_on_item_changed` Does Not Track Dirty Rows for Edit Mode

### Current state
`_on_item_changed` (line 1013 in `excel_grid.py`) has an early return for
`row < self._saved_count`. In edit mode, saved rows being edited would be silently ignored.

### What is needed

In edit mode (`self._edit_mode is True`), remove the early return for saved rows and instead:
1. Mark the row as dirty: `self._dirty_rows.add(row)`.
2. Apply a visual indicator (e.g., a small orange dot in the S/NO cell, or change row bg to
   `#FFFBEB`) so the cashier knows which rows have unsaved changes.
3. Still skip the auto-uppercase logic for `COL_RECEIPT` and `COL_NOTES` (they are
   delegate-managed).

---

## 8. Complete File-by-File Change Summary

### `tahmeed/models/transaction.py`
- Add fields: `edited_after_verification`, `last_edited_at`, `last_edited_by`
- Update `to_doc()` to include these fields
- Update `from_doc()` to read these fields (with defaults for existing documents)

### `tahmeed/services/cashier_service.py`
- Add `update_transaction(tx_id, updates: dict) -> bool`
- Remove nothing (keep `save_transaction` for new rows)

### `tahmeed/services/accountant_service.py`
- Add `get_edited_transactions(...)` — query edited-after-verification rows
- Add `count_edited_transactions(...)` — count version
- Add `re_approve_transaction(tx_id, accountant_id)` — approve + clear edited flag
- Add `bulk_re_approve_transactions(tx_ids, accountant_id)` — bulk version

### `tahmeed/signals.py`
- Keep the file; repurpose or add signals as needed once new save is wired

### `tahmeed/ui/cashier/excel_grid.py`
- **Remove:** `_autosave_row`, `_on_current_cell_changed`, `_on_retry_timer`, `_freeze_row`
- **Remove:** `_retry_queue`, `_retry_timer` from `__init__`
- **Remove:** `currentCellChanged` connection from `_build_ui`
- **Keep:** `_build_transaction_from_row` (reused by explicit Save)
- **Keep:** `save_rows()` / `_do_save()` (repurposed as the explicit Save action)
- **Add:** `self._edit_mode: bool = False`
- **Add:** `self._dirty_rows: set[int] = set()`
- **Add:** `self._dirty_originals: dict[int, Transaction] = {}` (snapshot of values before edit, for discard)
- **Add:** `toggle_edit_mode()` — public method called by the Edit button
- **Add:** `_enter_edit_mode()` — unlock all saved rows, change background
- **Add:** `_exit_edit_mode(discard: bool)` — lock rows back, optionally reload
- **Add:** `export_csv()` / `export_xlsx()` — called by Export button
- **Modify:** `_on_item_changed` — in edit mode, add `row` to `_dirty_rows` for saved rows
- **Modify:** `_do_save` — handle both new rows (insert) and dirty saved rows (update); split verified/unverified update logic
- **Modify:** `navigate_to_date` — prompt about unsaved changes if dirty

### `tahmeed/ui/cashier/dashboard.py`
- **Remove:** `search_changed` signal and search bar from `_QBDocHeader` (or keep — separate concern)
- **Add:** Action bar (thin strip between `_QBDocHeader` and `DailyRegister`) with:
  - **Edit** button (toggle) — calls `register.toggle_edit_mode()`
  - **Save** button — calls `register.save_rows()`
  - **Export** button — calls `register.export_xlsx()`
- Edit button changes appearance (active/inactive state) when mode toggles

### `tahmeed/ui/accountant/verify_inbox.py`
- **Remove:** `app_signals.transaction_saved.connect(self._debounce.start)` line
- **Add:** Sub-tab bar at the top (New | Edited) with count badges
- **Add:** Tab switch handler that changes which query function is called in `_reload()`
- **Add:** For "Edited" tab rows: show `last_edited_at` and `last_edited_by` columns
- **Modify:** Approve action — use `re_approve_transaction` for rows where `edited_after_verification=True`

---

## 9. Data Flow After Redesign

```
CASHIER ENTERS / EDITS DATA
─────────────────────────────────────────────────────
User clicks [Edit]
  → All saved rows become editable (yellow bg)
  → _edit_mode = True

User changes cells (new rows or existing)
  → _dirty_rows.add(row) for edited saved rows
  → Yellow dirty indicator per row

User clicks [Save]
  → For each new row (row >= _saved_count):
      INSERT → transactions (verified=False)
  → For each dirty saved row:
      If was verified=True:
        UPDATE → {all edited fields, verified=False,
                  edited_after_verification=True,
                  last_edited_at=now, last_edited_by=cashier._id}
      If was verified=False:
        UPDATE → {all edited fields, last_edited_at=now, last_edited_by=cashier._id}
  → Reload date → back to read-only mode

User clicks [Export]
  → Write current table rows to .xlsx


ACCOUNTANT REVIEWS
─────────────────────────────────────────────────────
Verify tab "New" sub-tab:
  → get_unverified_filtered(edited_after_verification=False OR missing)
  → Shows fresh cashier entries never previously approved
  → Approve → verified=True  |  Reject → rejection_reason set

Verify tab "Edited" sub-tab:
  → get_edited_transactions(edited_after_verification=True)
  → Shows rows cashier edited AFTER previous approval
  → Shows "Edited by [cashier] on [date]" column
  → Re-approve → verified=True, edited_after_verification=False


CASCADE ON RE-APPROVAL (implicit — no extra code needed)
─────────────────────────────────────────────────────
verified=True is set
  ↓
Master Expenses next query: returns updated row with new values
  ↓
Accountant Overview next refresh: recalculates totals with new amount
  ↓
Cashier Category View next load: shows updated category/description
  ↓
Cashier Overview next refresh: recalculates today/month stats
```

---

## 10. Fields That Cascade Implicitly on `verified=True`

Because all downstream views query the DB fresh, any field update on a transaction
cascades automatically when `verified=True` is restored. The affected fields and
their destination views:

| Field edited | Affected downstream view |
|---|---|
| `amount` | Master Expenses totals, Accountant KPIs (YTD TZS/USD), Overview stats |
| `category_name` / `item` | Master Expenses category filter, CashierCategoryView (moves to different sidebar tab) |
| `date` | Master Expenses month tab placement, date-filtered queries |
| `truck_number` | Master Expenses truck filter, Verify inbox truck filter |
| `description` | Search results everywhere |
| `receipt_status` | Master Expenses receipt column + filter, Overview receipt breakdown |
| `memo` | Displayed in Master Expenses, Verify inbox |
| `notes_flag` | Refund to Float column in all views |
| `ownership` | Master Expenses ownership column |
| `approver` | Master Expenses APR column |

---

*This document describes the problems and required fixes. No code has been changed.*
