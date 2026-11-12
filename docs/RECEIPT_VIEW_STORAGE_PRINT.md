# Receipt viewer, storage browser, print & export

Phased build for desk-side receipt inspection, HDD/storage browsing via DB indexes, print, and consolidate download. Complements `RECEIPT_WORKFLOW_BUILD.md` (A–G lifecycle) and `RECEIPT_MOBILE_BUILD.md` (capture).

**Goal:** Accountant and cashier can open any uploaded receipt (image, PDF, or other) before approve/link; accountant browses storage like an S3-indexed bucket over the receipt disk; both roles can view/print; filtered consolidate download packs files into one folder/ZIP.

**Principles**

1. **DB is the index.** `receipt_files` holds `stored_path` / S3-like keys; bytes live once under the receipt root (`files/`, plus parked/quarantine/approved views).
2. **API mediates every open.** Desk never reads raw host paths; open streams go through authenticated routes + path-jail under the receipt root.
3. **View before decide.** Inbox/Link actions open the shared viewer so approve/reject/link is informed by the actual file.
4. **One shared viewer.** Accountant and cashier share the same Qt widget and endpoints (`DeskActor`).

```text
Desk (Inbox / Link / Library / Storage)
        │  GET /v1/receipts/files/{id}/meta
        │  GET /v1/receipts/files/{id}/open
        ▼
   receipts_api ──► receipt_files index ──► files/<id>_name on HDD
        │
        ▼
   ReceiptFileViewer (image | PDF | OS open)
```

---

## Phase 1 — Open / preview before approve

**Status:** Done.

**Scope**

| Surface | Work |
| --- | --- |
| API | `GET /v1/receipts/files/{file_id}/meta`, `GET /v1/receipts/files/{file_id}/open` |
| Ops | Resolve doc → safe path under receipt root; stream with real Content-Type; inline disposition |
| Desk service | `fetch_receipt_file_meta`, `fetch_receipt_file_bytes` |
| UI | Shared `ReceiptFileViewer` dialog; **View** + double-click on Inbox (Link uses same selection) |
| Roles | Accountant + cashier (`DeskActor`) |

**Acceptance**

- [ ] Select parked/matched/approved file → View shows image or PDF in-app when possible.
- [ ] Non-image/PDF opens via OS default (or explicit Open externally).
- [ ] Missing file / path escape returns 404/422; desk shows clear error.
- [ ] Approve/Reject/Link unchanged; View does not mutate status.

**Out of Phase 1:** print, storage tree sidebar, library filters, consolidate ZIP, mobile viewer.

---

## Phase 2 — Submitted receipts library

**Status:** Done (desk Library tab + `GET /v1/receipts/library`).

**Scope**

- [x] Desk **Library** tab: query `receipt_files` (+ linked tx fields) by upload date range, item, payee, ownership, inbox status.
- [x] Table with View (Phase 1 viewer) + double-click.
- [ ] Jump to linked transaction (later polish).
- [ ] Hooks for Print (Phase 4) and Export selection (Phase 5).

**Endpoints**

- `GET /v1/receipts/library?date_from=&date_to=&item=&payee=&ownership=&status=&limit=`

**Acceptance:** Accountant finds a day’s receipts without hunting Inbox chips; View works on results.

---

## Phase 3 — Accountant sidebar: Receipts → Chase + Storage

**Status:** Done.

**Scope**

- [x] Sidebar: **Receipts** = chase desk (same as cashier + Evidence pack); **Receipt storage** = explorer + TRA/KRA pack/download (Manage → Receipt disk stays for path config).
- [x] Storage UI lists indexed prefixes (`files/`, `approved/…`, `quarantine/`, `parked/`) via API — not a raw HDD dump.
- [x] Explorer tab: folder tree + multi-select file list (Windows/bucket-style), View / Download selected / Rematerialize.
- [x] Pack & download tab: year/date/item/payee filters, day scan, ZIP / folder / pack selected days.
- [x] View / double-click opens Phase 1 viewer when `file_id` is known; breadcrumb + filename/sha search.
- [x] Disk root configure stays on Manage → Receipt disk / admin.
- [x] Sidebar Chase/Storage route to desk panels (not category master tables).

**Endpoints**

- `GET /v1/receipts/storage/tree?prefix=` (Manager)
- `GET /v1/receipts/storage/objects?prefix=&q=&limit=` (Manager)

**Acceptance:** Accountant browses approved item folders and opens a file without leaving desk; objects map to a DB row when indexed (disk-only quarantine orphans show as such).

---

## Phase 4 — Print

**Status:** Done.

**Scope**

- [x] Viewer toolbar: **Print** for image (`QPrinter` + full-res pixmap) and PDF (`QPdfDocument.render` per page).
- [x] Other types / non-previewable PDF: confirm → Open externally (OS print).
- [x] Available anywhere the shared viewer opens (Inbox, Link, Library, Storage).

**Acceptance:** Cashier and accountant can print the currently viewed receipt from the dialog.

---

## Phase 5 — Filtered download & consolidate

**Status:** Done.

**Scope**

- [x] Library (accountant): **Download ZIP** + **Save to folder…** using date/item/payee/ownership + approved-only.
- [x] Files-only pack (SUMMARY.txt + unique files under `year/month/<item>/`); reuses evidence-pack transaction filters.
- [x] Response headers + desk summary (rows / with file / files / missing).

**Endpoints**

- `POST /v1/receipts/files/consolidate` (Manager) → ZIP + `X-Pack-*` headers; audit via `receipt_exports`.

**Acceptance:** Accountant downloads all approved receipts for one payee in a date range into one ZIP/folder.

---

## Phase 6 — Polish & parity

**Status:** Done.

**Scope**

- [x] Cashier: View + Print via shared panel; ZIP evidence/consolidate remain **accountant-only** (`can_export`).
- [x] Export history shows **Kind** (`evidence` | `consolidate`); exports persist `kind`.
- [x] Storage browser: **Rematerialize** for indexed file → refresh `approved/` browse copies.
- [x] Mongo indexes on `receipt_files` (`transaction_ids`, `stored_path`, `materialized_paths`, `quarantine_path`, `original_filename`, …) + `receipt_exports` (`created_at`, `kind`) via migrate.
- [x] Docs + acceptance checklist rows.

**Ops note:** run migrate / index creation so new indexes exist on existing DBs.

---

## Suggested order

```text
1 (view) → 2 (library) → 3 (storage browser) → 4 (print) → 5 (consolidate) → 6 (polish)
```

Phase 1 unblocks daily approve with eyes on the file. 2–3 make inquiry scale. 4–5 are ops power tools.

---

## Out of scope (still)

- Client-side raw mount of the external HDD
- True AWS S3 SDK (local disk + keys remain the storage model)
- Mobile in-app PDF/image viewer (desk Phase 1 only)
- OCR / auto-approve from viewer

---

## Implementation map

| Piece | Location |
| --- | --- |
| Meta / open / library / consolidate / storage browse | `backend/app/receipts_api.py` |
| Path resolve, library, storage tree/objects | `backend/app/receipt_ops.py` |
| Evidence + consolidate packs | `backend/app/receipt_evidence.py` |
| Indexes | `backend/app/cli/migrate.py` |
| Client | `tahmeed/services/receipt_chase_service.py`, `receipt_storage_service.py` |
| Viewer + print | `tahmeed/ui/widgets/receipt_file_viewer.py` |
| Chase / Library / consolidate UI | `tahmeed/ui/widgets/receipt_chase_panel.py` |
| Storage browser + rematerialize | `tahmeed/ui/widgets/receipt_browser_panel.py` |
