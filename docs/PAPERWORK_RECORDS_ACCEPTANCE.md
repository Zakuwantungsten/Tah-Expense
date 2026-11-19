# Paperwork & Records — acceptance checklist

Guidance: [`PAPERWORK_RECORDS_BUILD.md`](PAPERWORK_RECORDS_BUILD.md)

Automated: `backend/tests/test_receipt_records_phase5.py`  
(run: `python -m pytest tests/test_receipt_records_phase5.py -q` from `backend/`).

Also keep chase regressions green: `tests/test_receipt_phase_g.py`.

---

## Automated

| Scenario | Coverage |
| --- | --- |
| Custom folder → upload → list; excluded from Inbox; `records/` in storage tree; ZIP pack | `test_records_upload_excluded_from_inbox_and_packable` |

---

## Human sign-off (accountant)

### Navigation

- [ ] Sidebar shows **Paperwork** (expandable) with **Receipts** and **Records**
- [ ] Overview quick action **Paperwork** opens Receipts desk
- [ ] Cashier still sees flat **Receipts** only (no Records)

### Receipts (chase) — regression

- [ ] Main / View all / Requests / Chase / Inbox / Link / Library still work
- [ ] Inbox **Park files…** still parks unmatched files
- [ ] Link / Approve / Library behavior unchanged
- [ ] Evidence pack still available (accountant)

### Main Upload (Phase 4)

- [ ] Main **Upload…** → destination dialogue
- [ ] **Park in Receipts inbox** parks and jumps to Inbox
- [ ] **File to Records** → Item / Supplier / Separate / Custom works
- [ ] **New…** custom folder in dialogue reappears next time and in Records panel

### Records panel (Phase 3)

- [ ] Custom / Items / Suppliers / Separate rail lists destinations
- [ ] New / Rename / Delete custom folders
- [ ] Multi-upload into selected folder; View / double-click opens viewer
- [ ] Search + date filters refresh the table
- [ ] **Download ZIP** saves a pack for current filters

### Storage

- [ ] **Receipt storage** Explorer lists `records/` beside files / approved / parked / quarantine
- [ ] No user-facing “Archive / Archived” labels (product says **Records**)

---

## Privileges

| Action | admin | accountant | cashier |
| --- | :---: | :---: | :---: |
| Paperwork → Receipts | ✓ | ✓ | ✓ (flat) |
| Paperwork → Records | ✓ | ✓ | — |
| Main Upload → Records destinations | ✓ | ✓ | — |
| Main / Inbox park | ✓ | ✓ | ✓ |
