# Receipts Main / day view — phased build

Guidance for adding a **Saved-style** receipts overview to the desktop Receipts desk: consolidated **day rows** on Main, drill-into **day entries**, and a switch to **View all** (Advanced / range). Complements `RECEIPT_WORKFLOW_BUILD.md` (chase lifecycle) and `RECEIPT_VIEW_STORAGE_PRINT.md` (viewer / library).

**Goal:** Cashier and accountant open Receipts and immediately see, per register day, how many entries still need paper vs do not need a receipt; open a day to work entry-by-entry with one clear status column and an attachment open/view action; flip to View all for the same entry list across months or a chosen date range.

**Out of scope for this doc:** mobile Pending list redesign, new chase lifecycle statuses, storage browser changes. Existing Chase / Inbox / Link / Library / Evidence tabs stay; this adds Main + View-all on top.

---

## Product decisions (locked)

| Decision | Choice |
| --- | --- |
| Surface | Desktop Receipts desk (cashier + accountant) |
| Day key | Register / expense calendar day (same idea as Saved), not upload day |
| Main list | One consolidated row per day (Saved pattern) |
| Main counts | **Entries**, **Needed**, **Partial**, **Collected**, **Waived**, **No receipt**, **Multi-file**, **Shared** |
| Requested vs Needed | **Same idea.** “Requested” = chase opened / still needed — treat as **Needed** |
| Entry status | **One** status column with the correct current status |
| Partial | Normal entry row; Main also shows Partial count and still increments Needed until complete |
| View all | Tab or switch beside Main — flat entry list for all months / chosen date range (same columns as day drill) |
| Open file | Reuse shared `ReceiptFileViewer` / existing open endpoints |

### Status vocabulary (display)

Map product language onto existing fields. Prefer one human label in the UI.

| Display status | Meaning | Typical source |
| --- | --- | --- |
| **Needed** | Still need paper / open chase | Request `needed`, or tx that requires receipt and is not received/waived |
| **Collected** / **Received** | Paper linked / in hand | Request `received` or tx `receipt_status=received` |
| **Partial** | Some file linked, not complete | `partial` / `partial_receipt` + note; still a normal row |
| **Waived** | Explicitly skipped | Request `waived` / tx `missing` |
| **No receipt** | Row does not require paper | tx `no_receipt` (or item does not require receipt) |
| **Parked** (file sense) | Upload waiting to finish match | Shown via attachment/inbox context when opening, not as a Main day count in v1 |

Main day row shows:

- **Entries** — total for that day
- **Needed** — still needing paper (includes Partial)
- **Partial** — partial-linked count (also in Needed)
- **Collected** — received / complete
- **Waived** — waived / missing
- **No receipt** — marked no receipt
- **Multi-file** — one entry with many attached files
- **Shared** — entries sharing one file with another row

---

## User flow

```text
Receipts desk
│
├── [ Main | View all ]     ← tab or segmented switch
│
├── Main
│     Day summary table (newest first)
│       Date | Entries | Needed | No receipt | …
│            │
│            ▼ open / double-click day
│     Day entries table
│       … expense fields | Status (one col) | Attachment / Open
│            │
│            ▼ Open / paperclip when file exists
│     ReceiptFileViewer
│       full or partial-linked file (+ partial note if any)
│
└── View all
      Same entry columns as day drill
      Date range / month (and light filters)
      Open attachment → same viewer
```

**Main job:** “Which days still owe paper?”  
**Day drill job:** “For this day, which rows, what status, open the file.”  
**View all job:** “Same entry work across a range without clicking each day.”

---

## Current baseline (reuse)

| Piece | Where |
| --- | --- |
| Saved day rows | `tahmeed/ui/cashier/saved_view.py` + `GET /v1/transactions/drafts/day-summaries` |
| Receipts desk shell | `tahmeed/ui/widgets/receipt_chase_panel.py` |
| Viewer | `tahmeed/ui/widgets/receipt_file_viewer.py` + file open APIs |
| Request / tx status | `backend/app/receipt_ops.py` (`needed` / `received` / `waived`, normalize receipt status) |
| Attachments on tx | `attachments` on transaction + `receipt_files` |

**Gap:** no receipt-centric day-summary API; no Main day table or View-all entry list on the Receipts desk.

---

## Phases (small and shippable)

Do phases in order. Each phase should leave the desk usable and reviewable on its own.

### Phase 0 — Spec freeze & wireframe in code comments / checklist

**Status:** Done when this doc is accepted.

**Work**

- [x] Lock Main counts: Needed + No receipt (+ Entries)
- [x] Lock one status column on entries; Requested ≡ Needed
- [x] Lock Partial as normal row + viewer/attachment shows partial link
- [x] Lock View all as sibling switch to Main
- [x] Confirm who sees whose days: default **all cashiers** (same as chase/compliance); optional `cashier_id` filter for narrowing

**Exit:** Team agrees this MD is the build guide; start Phase 1.

---

### Phase 1 — Backend: receipt day summaries

**Goal:** API that powers Main day rows.

**Status:** Done.

**Work**

- [x] Add `GET /v1/receipts/day-summaries` (under receipts)
  - Query: optional `date_from`, `date_to`, `cashier_id`, `limit`
  - Group transactions by register calendar day (`import_primary_date` else `date`)
  - Return per day: `date`, `entries_count`, `needed_count`, `no_receipt_count`, `cashier_ids`
  - **Needed:** open `receipt_requests.status=needed` **or** explicit `receipt_status=receipt` (Requested ≡ Needed; partial stays Needed while request open)
  - **No receipt:** `normalize_receipt_status == no_receipt`
  - Default scope: all cashiers (same as chase/compliance); optional `cashier_id`
- [x] Ops helpers: `tx_register_calendar_day`, `classify_receipt_day_bucket`, `list_receipt_day_summaries` in `receipt_ops.py`
- [x] Desktop service wrapper: `list_receipt_day_summaries` in `receipt_chase_service.py`
- [x] Tests: `backend/tests/test_receipt_main_day_summaries_phase1.py`

**Exit:** Curl/desk client can load day rows with correct counts for a known fixture day.

**Not in Phase 1:** UI table, day drill, View all.

---

### Phase 2 — Backend: day entries + range entries

**Goal:** Payload for day drill and View all.

**Status:** Done.

**Work**

- [x] `GET /v1/receipts/day-entries?day=YYYY-MM-DD`  
  Rows for that register day: identity fields, **one** `display_status` / `display_status_key`, attachment refs (`attachments[]`, `primary_file_id`, partial note)
- [x] `GET /v1/receipts/entries?date_from=&date_to=&status=&limit=`  
  Same row shape; `status` filter on display key (`all|needed|received|partial|waived|no_receipt|pending`)
- [x] Status helpers: `receipt_display_status_key`, `receipt_display_status_label`, `serialize_receipt_entry_row`  
  Priority: No receipt → Waived → Partial → Received → Needed → Pending
- [x] Open path unchanged: client uses existing file/attachment open endpoints from refs
- [x] Desktop wrappers: `list_receipt_day_entries`, `list_receipt_entries` in `receipt_chase_service.py`
- [x] Tests: `backend/tests/test_receipt_main_entries_phase2.py`

**Exit:** API returns a day list and a range list with status + openable file refs.

**Not in Phase 2:** Main UI (can stub-test via service).

---

### Phase 3 — Desktop Main: day summary table

**Goal:** Saved-like Main list on Receipts.

**Status:** Done.

**Work**

- [x] Add **Main** + **View all** as first tabs (Main default); keep Requests / Chase / Inbox / Link / Library / Evidence
- [x] Main page: S/NO | Date | Entries | Needed | No receipt | Open
- [x] Load Phase 1 summaries via `list_receipt_day_summaries`; loading overlay; empty state
- [x] Search/filter on date label / counts (Saved-style)
- [x] Double-click / Open day → `day_opened` signal + stacked Phase 4 shell (Back + placeholder)
- [x] Scope: same as desk API default (all cashiers; optional `cashier_id` later)

**Exit:** User opens Receipts → sees day rows with Needed / No receipt counts.

**Not in Phase 3:** Real entry table UI (placeholder shell only — Phase 4).

---

### Phase 4 — Desktop day drill: entries + status + open

**Goal:** Click a Main day → entry list with one status column and attachment open.

**Status:** Done.

**Work**

- [x] Stacked Main view: Back + day title → entry table
- [x] Table: Item | Payee | Ownership | Amount | **Status** | Open (or —)
- [x] Load Phase 2 `list_receipt_day_entries`
- [x] Open → `open_receipt_viewer`; partial shows note in tooltip / viewer title hint
- [x] Refresh while drill is open reloads that day’s entries

**Exit:** Full Main → day → open receipt loop works for normal and partial links.

---

### Phase 5 — View all (Advanced)

**Goal:** Switch to flat entries across a date range.

**Status:** Done.

**Work**

- [x] View all panel: From / To date edits — **default = current calendar month** through today
- [x] Status chips: All / Needed / Received / Partial / Waived / No receipt
- [x] Entry table + Open (same UX as Phase 4, plus Date column)
- [x] Load `list_receipt_entries`; limit 500 with count label when capped
- [x] Included in desk Refresh; Apply reloads current range/status

**Exit:** User can audit all receipt-related rows in a range without day drill.

---

### Phase 6 — Polish & acceptance

**Goal:** Production-ready desk experience.

**Status:** Done (code + docs). Human desk sign-off remains on the acceptance checklist.

**Work**

- [x] Column width persistence keys: `receipt_main_days_v1`, `receipt_main_day_entries_v1`, `receipt_view_all_entries_v1`
- [x] KPI strip: **kept** global (does not block Main)
- [x] Empty / error messages: filter-vs-empty copy on Main + View all; status line on load failure
- [x] Cashier scope: same as rest of Receipts desk (**all cashiers** by default; optional `cashier_id` on APIs)
- [x] Product checklist marked implemented below (human ☐ in `RECEIPT_ACCEPTANCE_CHECKLIST.md`)
- [x] Link + Main/View all section in `RECEIPT_ACCEPTANCE_CHECKLIST.md`

**Exit:** Checklist green; no P0 bugs on Main / day / View all / open.

---

## Acceptance checklist (product)

Code-complete against this build. Human desk pass uses `RECEIPT_ACCEPTANCE_CHECKLIST.md`.

- [x] Receipts opens on **Main** with one row per register day
- [x] Day row shows **Entries**, **Needed**, **No receipt** (not a Requested column separate from Needed)
- [x] Opening a day lists that day’s entries
- [x] Each entry has **exactly one** status column with the correct current status
- [x] Attachment column opens the receipt in the shared viewer when a file exists
- [x] Partial rows look like normal rows; status can say Partial; open shows the partial-linked file (+ note)
- [x] **View all** switch shows the same entry work across a chosen date range / months
- [x] Existing Chase / Inbox / Link / Library / Evidence still reachable
- [x] Cashier and accountant scopes behave as on the rest of the Receipts desk

---

## Suggested implementation order (summary)

| Phase | Deliverable | Depends on |
| --- | --- | --- |
| 0 | This guidance accepted | — |
| 1 | Day-summaries API + client | 0 |
| 2 | Day + range entries API + status mapper | 1 (can parallelize mapper) |
| 3 | Main day table UI | 1 |
| 4 | Day drill + open | 2, 3 |
| 5 | View all | 2, 4 |
| 6 | Polish + acceptance | 5 |

Phases 1 and 2 can overlap once Phase 0 is accepted. UI Phase 3 can start against mocked summaries if API is late, but prefer real Phase 1 first.

---

## Open points (resolved)

1. **Needed rule:** Open `receipt_requests.status=needed` **or** explicit `receipt_status=receipt`.
2. **No receipt rule:** Explicit `receipt_status=no_receipt` only.
3. **Default View all range:** Current calendar month through today.
4. **Layout:** Main + View all as first tabs; chase tools remain sibling tabs.

---

## Related docs

- `docs/RECEIPT_WORKFLOW_BUILD.md` — request/file lifecycle
- `docs/RECEIPT_VIEW_STORAGE_PRINT.md` — viewer, library, storage
- `docs/RECEIPT_MOBILE_BUILD.md` — mobile capture (unchanged by this plan)
- `docs/RECEIPT_ACCEPTANCE_CHECKLIST.md` — includes Main / View all human sign-off
