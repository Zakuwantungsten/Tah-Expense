# Paperwork & Records — build guidance

Company paper storage that is **not** tied to chase requests or expense-table rows, plus a clearer accountant nav for everything under filing.

**Product names (locked)**

| UI label | Meaning |
|----------|---------|
| **Paperwork** | Sidebar parent (umbrella) |
| **Receipts** | Existing chase desk (Main / Requests / Inbox / Link / Library / Evidence pack) |
| **Records** | New permanent company-paper store (no expense row required) |

Do **not** use “Archive / Archived” in the product UI. Prefer **Records** everywhere user-facing. Internal code may use `records` / `paperwork` keys; disk prefix may be `records/`.

---

## Problem

Today Receipts assume expense linkage:

- Chase/Requests need a transaction
- Inbox “Park files…” is temporary until Link / Approve
- Library / approved folders organize by linked item / payee / ownership
- Separate Expenses Excel “Uploads” are data feeds, not paper evidence

Accountants also file supplier pays, separate-expense paper, and misc company receipts that **never** appear in the register. Those need durable storage under reusable destinations.

---

## Product shape

### Navigation (accountant)

```
Paperwork                    ← expandable parent
  ├── Receipts               ← existing ReceiptChasePanel (can_export=True)
  └── Records                ← new Records panel
Receipt storage              ← unchanged disk explorer (sibling)
Receipt disk                 ← unchanged path admin (MANAGE)
```

Cashier keeps a flat **Receipts** entry (chase only). No Records nav. No Records destinations in upload dialogue.

### Unified upload (Receipts Main)

**Upload** on Main → multi-file picker → destination dialogue:

| Destination | Meaning |
|-------------|---------|
| Receipts flow | Park / chase path (existing behavior) |
| Records → Item | System CoA item |
| Records → Supplier | Supplier catalog |
| Records → Separate expense | Toll Plaza, Afritrack, … |
| Records → Custom folder | User label; **persisted and reused** |
| Records → existing custom | Pick from saved customs |

Upload from Receipts Main and from Records share the same backend.

### Records panel

- Browse by Item / Supplier / Separate expense / Custom
- Create / rename / delete custom folders
- Multi-upload into a folder
- View / search; accountant pack/ZIP in a later phase if needed

### Privileges

| Capability | admin | accountant | cashier |
|------------|:-----:|:----------:|:-------:|
| Paperwork → Receipts | ✓ | ✓ | ✓ (flat Receipts) |
| Paperwork → Records | ✓ | ✓ | — |
| Destination dialogue → Records options | ✓ | ✓ | — |
| Receipt storage / disk | ✓ | ✓ | — |

API: Manager (`admin` \| `accountant`) for Records CRUD + Records-bound upload. DeskActor may keep existing park upload.

### Out of scope for this program

- Changing chase ↔ transaction 1:1
- Mobile chase / early-upload redesign
- Merging Separate Expenses Excel feeds into Records
- Renaming cashier “Receipts”

---

## Technical anchors

| Area | Path |
|------|------|
| Accountant sidebar | `tahmeed/ui/accountant/sidebar.py` |
| Accountant dashboard | `tahmeed/ui/accountant/dashboard.py` |
| Receipts desk UI | `tahmeed/ui/widgets/receipt_chase_panel.py` |
| Receipt browser | `tahmeed/ui/widgets/receipt_browser_panel.py` |
| Desk API client | `tahmeed/services/receipt_chase_service.py` |
| Receipts HTTP | `backend/app/receipts_api.py` |
| Domain ops | `backend/app/receipt_ops.py` |
| Upload security | `backend/app/receipt_upload_security.py` |
| Roles | `backend/app/dependencies.py` (`Manager`, `DeskActor`) |
| Storage layout | `files/`, `approved/`, `parked/`, `quarantine/` → add `records/` |

Reuse Receipts chrome: `#F3F4F6` page, white cards, `#0077C5` accent, ghost/primary buttons, `QFileDialog.getOpenFileNames`, `open_receipt_viewer`, Fluent `confirm_dialog`.

**Disk sketch (Records)**

```
{attachments_root}/records/
  items/<item-slug>/YYYY/MM/
  suppliers/<supplier-slug>/YYYY/MM/
  separate/<feed-key>/YYYY/MM/
  custom/<custom-slug>/YYYY/MM/
```

Canonical bytes may still live under `files/<uuid>_name` with a materialized browse copy under `records/…` (mirror approve/materialize pattern).

**Data sketch**

- `receipt_record_folders` (or equivalent): `{ _id, kind, ref_id?, label, slug, created_by, created_at, … }` for customs (+ optional cache of Item/Supplier/Separate shortcuts)
- `receipt_files` extensions: `record_folder_id`, `record_kind`, `record_label`, … when filed into Records (no `transaction_ids` required)

Exact schema is Phase 2.

---

## Phases

### Phase 1 — Navigation shell ✅ (done)

**Goal:** Accountant can open Paperwork → Receipts / Records. No backend Records API yet.

- [x] Guidance doc (`docs/PAPERWORK_RECORDS_BUILD.md`)
- [x] Sidebar: `receipts` → expandable **Paperwork** with fixed children **Receipts** + **Records**
- [x] Dashboard routes `paperwork` children to existing chase panel + Records placeholder page
- [x] Keep `receipt_browse` / `receipt_disk` unchanged
- [x] Cashier sidebar unchanged (flat Receipts)
- [x] Legacy `parent_key == "receipts"` routing still harmless / redirected
- [x] Placeholder widget: `tahmeed/ui/widgets/receipt_records_panel.py`

**Done when:** Accountant expands Paperwork, opens Receipts (full desk) and Records (placeholder). Cashier still sees Receipts only.

### Phase 2 — Records backend ✅ (done)

**Goal:** Durable folders + upload that never need a transaction.

- [x] Mongo collection + indexes for record folders (customs reusable)
- [x] Manager APIs: list/create/rename/delete customs; list destinations (items, suppliers, separate feeds, customs)
- [x] Manager bulk upload with destination metadata; store under `records/…`
- [x] List/browse files by destination; open via existing `/files/{id}/open`; allowlist + magic-byte checks
- [x] Audit log actions (`records.upload`, `records.folder.create|rename|delete`)
- [x] Inbox / Library exclude `record` filings; storage tree includes `records/`
- [x] Desktop client: `tahmeed/services/receipt_records_service.py`

**Key paths:** `backend/app/receipt_records_ops.py`, routes under `/v1/receipts/records/*` in `receipts_api.py`.

**Done when:** API can create a custom folder, upload PDFs into it, list and stream them back with no transaction id.

### Phase 3 — Records UI panel ✅ (done)

**Goal:** Sleek accountant Records page under Paperwork.

- [x] Replace placeholder with full panel (category/folder rail + file table)
- [x] Create/rename/delete custom folders
- [x] Multi-upload into selected destination
- [x] View via existing receipt viewer
- [x] Search / filter by date + destination
- [x] Match Receipts desk visual language

**Widget:** `tahmeed/ui/widgets/receipt_records_panel.py`

**Done when:** Accountant can manage customs and file/browse company paper without using Inbox/Link.

### Phase 4 — Unified Upload dialogue (Receipts Main) ✅ (done)

**Goal:** One Upload on Main that routes to Receipts flow **or** Records destinations.

- [x] Main-tab Upload (multi-file)
- [x] Destination dialogue: Receipts flow vs Records → Item / Supplier / Separate / Custom / existing custom
- [x] Customs created in dialogue appear next time (shared with Records panel)
- [x] Cashier: Upload parks only (no Records destinations); accountant gets full dialogue (`can_export`)
- [x] Wire to Phase 2 APIs; leave Inbox “Park files…” working

**Dialog:** `tahmeed/ui/dialogs/receipt_upload_destination_dialog.py`  
**Hook:** Main toolbar `Upload…` in `receipt_chase_panel.py` → `_main_upload`

**Done when:** From Receipts Main, accountant uploads a batch into a reusable custom Records folder in one flow.

### Phase 5 — Polish & handoff ✅ (done)

- [x] ZIP/pack by Records destination (`POST /v1/receipts/records/pack` + Records **Download ZIP**)
- [x] Receipt storage tree shows `records/` (Phase 2) + Explorer hint updated
- [x] Acceptance checklist + light regression test
- [x] Copy pass: product UI uses **Records** / **Paperwork** (no Archive labels)
- [x] Overview quick action **Paperwork** → Receipts desk

**Checklist:** [`PAPERWORK_RECORDS_ACCEPTANCE.md`](PAPERWORK_RECORDS_ACCEPTANCE.md)  
**Test:** `backend/tests/test_receipt_records_phase5.py`

---

## Phase dependency

```
Phase 1 (nav) ──► Phase 2 (API) ──► Phase 3 (Records UI)
                      │
                      └──────────► Phase 4 (Main Upload dialogue)
                                        │
                                        └─► Phase 5 (polish)
```

Phase 3 and 4 can overlap after Phase 2; prefer Records panel (3) before Main dialogue (4) so destinations have a home to manage.

---

## Acceptance (program)

1. Accountant sidebar shows **Paperwork** with **Receipts** and **Records**.
2. Receipts desk behavior unchanged for chase / inbox / link.
3. Records accepts multi-file upload with no expense row.
4. Custom folders persist and reuse in dialogue + Records panel.
5. Destinations include Item, Supplier, Separate expense, Custom.
6. Cashier cannot open Records or file into Records.
7. Existing park → link → approve path still works.

---

## Changelog

| Date | Note |
|------|------|
| 2026-11-16 | Initial plan; names locked to Paperwork / Receipts / Records; Phase 1 started |
| 2026-11-16 | Phase 1 nav shell done; Phase 2 Records backend (folders, upload, list, indexes) done |
| 2026-11-16 | Phase 3 Records UI panel (rail, CRUD customs, multi-upload, viewer) done |
| 2026-11-16 | Phase 4 Main Upload destination dialogue (park vs Records) done |
| 2026-11-16 | Phase 5 polish: Records pack, overview shortcut, acceptance checklist |
