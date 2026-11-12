# Receipt workflow — Phase G acceptance checklist

Automated coverage: `backend/tests/test_receipt_phase_g.py`  
(run: `python -m pytest tests/test_receipt_phase_g.py -q` from `backend/`).

## Automated (CI / local)

| Scenario | Test |
| --- | --- |
| Save row → request → upload → inbox → approve → folder → pack | `test_e2e_save_request_upload_approve_folder_pack` |
| Early upload → later link → approve | `test_early_upload_later_link_approve` |
| One file → three rows | `test_one_file_three_rows` |
| Partial then complete | `test_partial_then_complete` |
| Verify blocks while `needed` | `test_verify_still_blocks_needed` (+ E2E / partial) |

## Human sign-off

Use one **accountant** desk login and one **ownership** Expo Go user on the same LAN/hotspot.

### Accountant

- [ ] Open **Receipts** — Requests / Compliance KPIs load
- [ ] Inbox shows parked / matched; **Approve** materializes `approved/YYYY/MM/<item>/`
- [ ] Inbox **View** / double-click opens image or PDF; **Print** from viewer
- [ ] Link: N files ↔ M rows; **Partial** keeps chase open with note
- [ ] **Library**: filter by date/item/payee; View works
- [ ] **Library**: Download ZIP / Save to folder (consolidate) for a payee range
- [ ] **Receipts** (same as cashier chase desk): Requests / Inbox / Link / Library / View+Print
- [ ] **Receipt storage**: Explorer browse `files/` / `approved/`; Pack & download for TRA/KRA
- [ ] Evidence pack (on Receipts): multi-item + **Approved only** ZIP; history shows Kind
- [ ] Quarantine chip lists security-rejected uploads
- [ ] Verify / approve still blocked while a required request is `needed`

### Cashier

- [ ] Receipts Inbox / Library: **View** and **Print** work
- [ ] No Evidence pack / consolidate ZIP tabs (accountant-only)

### Ownership (Expo)

- [ ] Login; home shows Open / Overdue / Reminded + last upload
- [ ] Open a needed request → camera/photos/files → upload summary (sent/failed)
- [ ] **Park early** lands in desk Inbox as unmatched
- [ ] **History** shows the upload; tap opens the request when linked
- [ ] Waive with reason clears the request
- [ ] Push notification (if token registered) opens the request

### Sign-off

| Role | Name | Date | OK |
| --- | --- | --- | --- |
| Accountant | | | ☐ |
| Ownership user | | | ☐ |

Notes:
