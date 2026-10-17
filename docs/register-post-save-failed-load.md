# Daily Register: post-save “Failed to load” (stale grid vs Saved count)

**Status:** Root-caused and fixed in UI (`_FocusRingHost` hardening).  
**Date observed:** 2026-10-17  
**Surface:** Cashier Daily Register → Save draft → grid reload

---

## Problem statement (what the user saw)

1. Cashier was saving many new entries on the Daily Register table.
2. An **Error** dialog appeared titled **“Failed to load:”** with a long nested Qt stack of:
   - `Error calling Python override of QObject::eventFilter():` (repeated many times)
   - Ending with:  
     `Error calling Python override of QWidget::eventFilter(): '_FocusRingHost' object has no attribute '_button'`
3. After dismissing the dialog, the **register table still showed ~43 rows** (the pre-save / stale view).
4. The **Saved** sidebar / Saved tab showed **~233** draft entries.
5. Opening that day from **Saved** loaded the full **233** rows into the register.

**Net effect:** Save looked broken in the grid, but drafts were already on the server. The UI failed to refresh after save.

---

## What this was *not*

| Misread | Reality |
|--------|---------|
| Save only persisted 43 rows | Server held the full draft set (~233); Saved read Mongo correctly |
| Saved tab “lied” about counts | Badge / Saved chip counts draft transactions from the DB |
| Data loss | No loss — opening from Saved proved the full set was stored |
| A pure network/DB failure | The exception was a **PySide/Qt event-filter crash** during post-save reload |

Arithmetic that fits the symptoms: **~43 already on screen + ~190 newly saved ≈ 233** total drafts for the day (or across the Saved scope), with the grid stuck on the old 43 after a failed reload.

---

## Intended save → refresh flow

In `tahmeed/ui/cashier/excel_grid.py`:

1. `_do_save` / `_do_save_body` persists new/edited rows (chunked `bulk_save_transactions`, local draft flush, etc.).
2. On success it emits `rows_saved` / `drafts_changed` (Saved badge + inbox refresh).
3. It may show **“Draft saved”**, then calls **`await self._load_date(self._current_date)`** so the grid matches Mongo.
4. `_load_date_body` loads transactions, calls `_populate(txs)`, restores local draft if needed.
5. On **any** exception in that path:

```text
QMessageBox.critical(self, "Error", f"Failed to load:\n{exc}")
```

So a UI crash during reload is presented as **“Failed to load”**, and the table is left on whatever was last successfully populated (here: ~43 rows).

Meanwhile Saved / draft badge refresh from the server and show the true count (~233).

---

## Root cause

### Crash site

`tahmeed/ui/dialogs/confirm_dialog.py` — class `_FocusRingHost`:

- Wraps the Cancel button and installs an **event filter** to redraw a focus ring on FocusIn / FocusOut.
- `eventFilter` and `paintEvent` accessed `self._button` unconditionally.
- The host was only added to a layout as a temporary:

```python
footer_row.addWidget(
    _FocusRingHost(cancel_btn, ring=c["ring"], parent=footer)
)
```

### Why `_button` can be missing

Classic **PySide / PyQt pitfall**:

- Qt keeps the C++ `QWidget` alive via parent ownership.
- The **Python wrapper** for an event-filter object can be garbage-collected or resurrected **without** re-running `__init__`.
- A resurrected / half-torn-down wrapper has **no** `_button` attribute.
- The next focus (or related) event calls `eventFilter` → `AttributeError`.
- Qt then reports nested  
  `Error calling Python override of QObject::eventFilter(): …`  
  for every subsequent filtered event (the spam in the dialog).

Confirm dialogs are used widely (including via patched `QMessageBox.question` → `ask_standard_question`). Focus traffic around modal dialogs / teardown can hit this path; the register’s broad `except` then surfaces it as **Failed to load** during `_load_date`.

### Why Saved showed 233 but the table showed 43

| Path | Behavior after the crash |
|------|---------------------------|
| Post-save `_load_date` | Aborted; grid not re-populated |
| `rows_saved` / draft badge | Still refreshed from server → **233** |
| Open day from Saved | Fresh `_load_date` → full **233** rows |

---

## Fix applied (this repo)

In `tahmeed/ui/dialogs/confirm_dialog.py`:

1. **Strong reference** on the dialog:  
   `self._cancel_ring_host = _FocusRingHost(...)`  
   so the event-filter wrapper is not GC’d while the dialog lives.
2. **Hardened** `eventFilter` / `paintEvent` to use `getattr(self, "_button", None)` and tolerate missing / deleted C++ objects (never raise into Qt’s event loop).

Regression coverage in `tests/test_confirm_dialog.py`:

- `test_confirm_dialog_keeps_focus_ring_host_ref`
- `test_focus_ring_host_event_filter_tolerates_missing_button`

---

## Impact / severity

| Aspect | Assessment |
|--------|------------|
| Data integrity | **No loss** when save completed before the failed reload |
| User trust | High — looks like save failed or truncated the day |
| Operational risk | Cashier may re-enter rows (duplicates) or re-save unnecessarily |
| Related work | Separate concern: **slow Save** for large batches (performance / local-first outbox) — tracked separately |

---

## Verification checklist

- [ ] Save a large batch of drafts on one day; dismiss any success dialog; grid row count matches Saved for that day.
- [ ] Open Yes/No confirms (Clear Table, leave with unsaved work, etc.); Cancel focus ring still works; no eventFilter spam.
- [ ] After Save, Saved badge updates and opening the day from Saved matches the register.
- [ ] Run: `python -m pytest tests/test_confirm_dialog.py -q`

---

## Related files

| File | Role |
|------|------|
| `tahmeed/ui/dialogs/confirm_dialog.py` | `_FocusRingHost` + `ConfirmDialog` |
| `tahmeed/ui/dialog_theme.py` | Patches `QMessageBox.question` → fluent confirm |
| `tahmeed/ui/cashier/excel_grid.py` | Save, `_load_date`, “Failed to load” dialog |
| `tahmeed/ui/cashier/dashboard.py` | Saved badge / Saved view refresh on `rows_saved` |
| `tahmeed/ui/cashier/saved_view.py` | Saved inbox counts from draft queries |
| `tests/test_confirm_dialog.py` | Regression tests |
