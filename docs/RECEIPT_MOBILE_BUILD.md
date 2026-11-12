# Receipt mobile capture — build brief

Start here in the IDE. Scope is manual linking (no OCR sorter), Expo Go upload app, server hard-disk storage, S3 backup, and ownership follow-up pushes.

Related research (older, phone-page-first): [receipt-capture-implementation.md](./receipt-capture-implementation.md).  
Post–Phase 0–4 productization (compliance, parked/approve, 1↔N link, item folders, mobile polish): [RECEIPT_WORKFLOW_BUILD.md](./RECEIPT_WORKFLOW_BUILD.md).  
This file is the **storage/mobile foundation**; the workflow doc is the **next build direction**.

---

## Product decisions (locked)

| Decision | Choice |
| --- | --- |
| Matching | **Manual** on desktop. No live OCR / company-folder sorter |
| Primary storage | External **hard disk** mounted on the API server (`ATTACHMENTS_DIRECTORY`) |
| Backup | **S3** second copy after local write |
| Upload client | **Expo Go** app in repo folder `mobile/` |
| Who is chased | Transaction **`ownership`** (not `approver` / APP BY) |
| Follow-up | Expo **push** when a required receipt stays unuploaded |
| Formats | Existing attachment allowlist + HEIC / common phone types |
| Cashier save | Never blocked because a receipt is missing |

---

## Current system (what we extend)

```text
Desktop (PySide6)  --HTTPS JWT-->  FastAPI  -->  MongoDB
                                      |
                                      +-->  disk attachments (today: per transaction id)
```

Useful fields on `transactions` already:

- `ownership`, `approver`, `payee`, `item` / `category_name`
- `receipt_status`: `pending` | `received` | `missing`
- `attachments[]` metadata on the row
- Category flag: `requires_receipt`

Gaps today: upload does not set `received`; no mobile API; `people` has no phone/push token; production may lack a writable persistent attachment volume on the HDD.

Key code:

- `backend/app/attachment_ops.py`, `attachments_api.py`
- `backend/app/transactions_api.py`, `schemas.py`
- `tahmeed/services/attachment_service.py`
- `tahmeed/ui/dialogs/attachment_dialog.py`
- `tahmeed/ui/accountant/master_expenses.py`
- `backend/app/cli/backup.py` (mongodump + optional S3 — extend for receipt bytes)
- `backend/app/config.py` (`attachments_directory`, backup S3 settings)

---

## Target flow

```text
Cashier saves row (requires_receipt)
        -> receipt_request status=needed
        -> Expo push to ownership person (if token)
        -> Person opens Expo Go, uploads photo/PDF/...
        -> API writes file to HDD, then S3 backup
        -> Desktop: chase list + manual link if needed
        -> receipt_status=received
        -> Later: date-range ZIP pack (year/month/item + index.csv)
```

Canonical file path on disk: `<ATTACHMENTS_DIRECTORY>/files/<file_id>_<safe_name>`  
Do **not** use live `year/month/item` folders as the source of truth. Those paths exist only inside export ZIPs.

---

## New data (Mongo)

### `receipt_files`

One document per uploaded file.

- `_id` / file id  
- `original_filename`, `content_type`, `size`, `sha256`  
- `stored_path`, `s3_key` (empty until backup succeeds)  
- `uploaded_at`, `uploaded_by`  
- `source`: `expo` | `desktop` | …  
- `inbox_status`: `unmatched` | `matched`  
- `request_id` (optional)

### `receipt_requests`

One per row that needs evidence.

- `transaction_id`  
- `asked_of_name` (ownership text at ask time)  
- `asked_of_person_id` (optional)  
- `status`: `needed` | `received` | `waived`  
- `opened_at`, `last_reminded_at`, `reminder_count`  
- `waiver_reason`, `waived_by`, `waived_at`  
- `file_ids[]`

### `people` extensions

- `phone`  
- `receives_receipt_requests`  
- `expo_push_tokens[]`  
- optional link to a login user (`user_id`) for mobile auth

Transaction `attachments` should move toward links (`file_id`, `linked_at`, `linked_by`). Keep old embedded reads working during migration.

---

## Build phases

### Phase 0 — Hard disk + S3

- [x] Set `ATTACHMENTS_DIRECTORY` to the server HDD mount (example: `/mnt/tahmeed-receipts`)
- [x] Docker / systemd: writable mount / `ReadWritePaths` for that path
- [x] Confirm upload survives API restart
- [x] S3 upload of receipt bytes after local rename; store `s3_key` + `sha256`; retry if backup failed

Object key is `receipts/<file id>` (`RECEIPT_S3_PREFIX`), not the mongodump prefix, so backup prune does not delete receipt bytes. A failed upload leaves `s3_key` empty and leaves the local file in place. The scheduler maintenance slot retries a bounded batch; `tahmeed-backup retry` drains the backlog. Mount the disk before first start — see `deployment/docker/README.md` and `deployment/v1/README.md`.

Ops UI (admin + accountant): **Receipt disk** shows the API host path, free space, connected drives/mounts, S3 status, and pending cloud copies. `GET/PUT /v1/receipts/storage` and `POST /v1/receipts/storage/probe` set a database override (`attachments_directory`) over the env default. Changing the path does not migrate existing files. Local walkthrough: `LOCAL_DEV.md`.

### Phase 1 — Backend model + mobile API

- [x] Collections + indexes for `receipt_files`, `receipt_requests`
- [x] On save: open request when `requires_receipt` and no linked file
- [x] Attach/link → `receipt_status=received`; last unlink → `pending` (honor explicit `missing` / waiver)
- [x] Secure mobile endpoints under `/v1/mobile/...`:
  - login / refresh (JWT, same session family as desktop where possible)
  - list **my** open requests (ownership match)
  - multipart upload (multi-file, broad formats)
  - register Expo push token
- [x] Reminder job: still `needed` after threshold → Expo Push (default: remind every 24h)

Mobile files land under `files/<id>_<name>` and are linked onto the row. Desktop attach still uses the per-transaction folder and also writes a `receipt_files` row. People may store `phone`, `receives_receipt_requests`, `user_id`, and `expo_push_tokens[]`. Reminder interval: `RECEIPT_REMINDER_HOURS` (default 24). Run `tahmeed-migrate` once so the new indexes exist.

### Phase 2 — Expo app (`mobile/`)

Create subdirectory:

```text
tahmeed-expense/mobile/
```

- [x] Expo Go TypeScript app (SDK current stable)
- [x] Config API base URL (`https://api.meedexpe.com` + local override)
- [x] Login screen
- [x] Pending receipt list
- [x] Camera + document picker; multi-file upload
- [x] Register push token after login; tap notification → that request

App lives in `mobile/` (Expo SDK 58). Local override: `EXPO_PUBLIC_API_BASE_URL` (see `mobile/.env.example` and `mobile/README.md`).

```bat
cd mobile
npm start
```

### Phase 3 — Desktop manual chase / link

- [x] Chase list grouped by ownership (count, oldest age, last reminder)
- [x] Inbox of unmatched / recent uploads
- [x] Multi-select **manual** link files ↔ rows (one↔many allowed)
- [x] Verify refuses submit when request is still `needed` and item requires receipt
- [x] Copy / show reminder status (no WhatsApp scraping)

### Phase 4 — Evidence pack

- [x] Accountant action: date range (+ optional item / payee / ownership)
- [x] ZIP: `year/month/item/...` + `index.csv` + short README
- [x] Export audit record (who, when, range, pack sha256)
- [x] Retention: receipt files are not rotated with mongodump “keep N”

---

## API sketch (mobile)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/mobile/auth/login` | username/password → access + refresh |
| POST | `/v1/mobile/auth/refresh` | rotate refresh |
| POST | `/v1/mobile/devices` | body: `{ "expo_push_token": "..." }` |
| GET | `/v1/mobile/receipt-requests` | open requests for this ownership person |
| GET | `/v1/mobile/receipt-requests/{id}` | one request summary |
| POST | `/v1/mobile/receipt-requests/{id}/files` | multipart upload |
| POST | `/v1/mobile/receipt-requests/{id}/waive` | reason required; respect `waiver_allowed` later |

Desktop keeps existing `/v1/transactions/.../attachments` during transition; new link/inbox routes can live under `/v1/receipts/...`.

---

## Config knobs

| Env / setting | Meaning | Default idea |
| --- | --- | --- |
| `ATTACHMENTS_DIRECTORY` | HDD mount root | `/mnt/tahmeed-receipts` |
| `BACKUP_S3_BUCKET` / prefix | Receipt + DB backup bucket | already used for mongodump |
| `RECEIPT_REMINDER_HOURS` | Hours between follow-up pushes | `24` |
| Mobile API URL | Expo app config | `https://api.meedexpe.com` |

---

## Non-goals

- OCR auto-sort into company folders as the product
- Live folder tree as the database of record
- WhatsApp Web scraping / group archive as storage
- Second ledger where rows are retyped
- Blocking register save until the paper arrives
- App Store / Play release required for v1 (Expo Go is enough)

---

## Suggested first coding session

1. Confirm HDD mount path and set `ATTACHMENTS_DIRECTORY` locally + in deployment docs.
2. Scaffold `mobile/` with Expo TypeScript blank template.
3. Add `/v1/mobile/auth/login` + health check from the app.
4. Add `receipt_requests` creation on transaction save when `requires_receipt`.
5. Wire one happy path: list one pending request → upload one JPEG → file on disk → `receipt_status=received`.

Work only under `C:\Users\Tungsten\Desktop\tahmeed-expense` for this feature.
