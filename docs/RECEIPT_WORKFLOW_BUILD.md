# Receipt workflow — full product build

This document turns the live receipt flow (desktop chase/inbox + Expo upload + disk + evidence pack) into a complete, phased product. It assumes Phases 0–4 in `RECEIPT_MOBILE_BUILD.md` already shipped (disk/S3, requests, mobile upload, chase/link, evidence ZIP).

**Goal:** ownership people get asked for paper; cashiers/accountants chase and match; files never get lost; verified evidence ends up sorted by item; exports and the phone app stay professional and safe.

---

## Current baseline (already working)

| Piece | Status |
| --- | --- |
| Receipt disk (`ATTACHMENTS_DIRECTORY` / override, e.g. `D:\tahmeed-receipts`) | Done |
| Open `receipt_requests` on save when item `requires_receipt` | Done (ownership-based) |
| Expo upload → `receipt_files` + link when request-owned | Done |
| Desktop **Receipts**: Chase / Inbox / Link / Evidence pack | Done (basic) |
| Verify blocks approve while request still `needed` | Done |
| Evidence ZIP by date range (+ optional filters) | Done (basic) |

**Gaps this plan closes:** compliance dashboards, parked vs permanent storage, item-folder materialization, stronger matching (1↔N / partial / early upload), upload security hardening, mobile polish + atomic multi-upload UX.

---

## Product principles

1. **The register row is the event.** Saving does not wait for paper. A request is the follow-up.
2. **Ownership is who is chased** (`ownership` / people directory), not necessarily the cashier who typed the row.
3. **Bytes live once.** Links (file ↔ one or many rows) are the truth. Folders are derived for humans and exports.
4. **Never lose an upload.** Unmatched or unapproved files stay in a **parked** area until linked/approved.
5. **Atomic user actions.** Upload batch, link confirm, approve-to-permanent, and pack build either finish with a clear summary or roll back / leave no half-state the user cannot understand.
6. **Security first on ingress.** Reject dangerous types/sizes before anything is parked or linked.

---

## Domain model (target)

### Request lifecycle

```text
needed → received (file linked / confirmed)
       → waived (explicit, reason required; respect waiver_allowed)
```

Reminders increment while `needed`; age and reminder_count drive compliance badges.

### File lifecycle

```text
uploaded (parked / inbox)
  → matched (linked to ≥1 row, pending accountant confirm if required)
  → approved (permanent evidence; eligible for item-folder materialization + packs)
  → rejected (optional; stays parked or quarantine with reason)
```

Early upload (no row yet): `inbox_status=unmatched` / parked until linked.

### Link flexibility (bidirectional)

| Case | Behaviour |
| --- | --- |
| One file → one row | Default auto or confirm |
| One file → many rows | Cashier/accountant multi-select rows; one physical file, N index lines |
| Many files → one row | Partial / multi-page receipt; row stays `received` when policy says “enough” |
| Partial cover | Mark link as `partial` + note; compliance can still show “gap” until complete |
| Upload before row | Park in inbox; later link when rows exist |

### Storage layout (target)

```text
D:\tahmeed-receipts\
  files\                 # canonical bytes by file id (immutable identity)
  .staging\              # in-flight writes
  parked\                # optional mirror/shortcut view of unapproved / unmatched
  approved\              # optional human tree: year\month\item\...
  quarantine\            # failed security scan / rejected types
```

Canonical DB + `files/<id>_…` remain source of truth. `approved\` / pack ZIP paths are **materialized views** for browsing and TRA/KRA handoff—not a second ledger.

---

## Desktop information architecture

**Receipts** sidebar stays one place, with clearer tabs:

1. **Requests** — all open/closed requests; filters: Today / Open / Received / Waived; ownership search.
2. **Compliance** — KPIs: requested today, collected, still missing, reminded ≥N and still needed, overdue (age > threshold), approved vs parked counts.
3. **Inbox** — every upload (expo/desktop); unmatched-first; preview; actions: link, approve, reject/quarantine.
4. **Parked** — waiting for approval or match (safety net so nothing disappears into “matched” limbo).
5. **Link** — multi-select files ↔ multi-select rows; partial flag; show request coverage.
6. **Evidence pack** — date range + **multi-item** (+ payee/ownership); ZIP + audit; optional “approved only”.

---

## Mobile (Expo) polish targets

- Brand palette (aligned with desktop blue `#0077C5` / navy, light surfaces—not generic purple AI chrome).
- Clean login (API URL only in debug/advanced).
- Home: open count, overdue count, last upload summary.
- Request detail: pick camera/photos/files → **atomic batch upload** with summary (“3 sent, 0 failed”).
- **Upload history** for this user (what was sent, when, which request/row).
- Early upload entry point: “Upload without a request” → parks in inbox for later link.
- Clear empty states and permission CTAs (open Settings).

---

## Security (ingress)

| Control | Rule |
| --- | --- |
| Allowlist | Existing image/PDF/HEIC policy; reject executables/scripts/HTML/SVG-as-active |
| Size | Existing ~25 MB cap |
| Content sniff | Magic-byte check vs claimed MIME; mismatch → quarantine |
| Filename | Sanitize; never execute; store under opaque id |
| Authz | Mobile only own ownership requests; desk roles for approve/link/pack |
| Parked isolation | Quarantine not served as “Open” until cleared |

---

## Phases

### Phase A — Requests desk + Today + compliance KPIs

**Outcome:** Accountant/cashier can see *what was asked* and *who is late*, not only a flat chase list.

- [x] **Requests** tab (or upgrade Chase): list requests with status, ownership, amount, item, age, reminder_count, tx id.
- [x] Presets: **Today**, **Open (needed)**, **Received**, **Waived**, **Overdue** (configurable hours).
- [x] **Compliance** strip or tab: counts for today requested, still needed, received, waived, reminded ≥ N still needed, parked/unmatched uploads.
- [x] Keep existing mark-reminded + copy chase line.
- [x] API: aggregate endpoints or efficient queries for KPI cards (no N+1 from UI).
- [x] Sleek Fluent-style Receipts desk UI (compact KPIs, chips, MDI icons).

**Done when:** Opening Receipts answers “who owes paper today?” and “how bad is the backlog?” in one glance.

---

### Phase B — Parked inbox, approve-to-permanent, no lost files

**Outcome:** Every upload is visible; nothing is “gone” until approved or deliberately rejected.

- [x] Clarify statuses: `unmatched` / `matched` / `approved` / `rejected` (parked = unmatched+matched).
- [x] Inbox actions: status chips, link, **Approve** (marks permanent under `files/`), **Reject** (reason → `quarantine/` copy).
- [x] Policy: auto-link / request-owned upload can set `matched`; **Approve** still required for permanent / official.
- [x] Disk: `ensure_receipt_workspace` creates parked/quarantine/approved under receipt root; Receipt disk UI reused for path + free space.
- [x] Audit events: `receipt.approve`, `receipt.reject` (upload remains the park ingress).

**Done when:** An upload always appears in Inbox/Parked until approved or rejected; disk still holds bytes if Mongo link changes.

---

### Phase C — Bidirectional link + partial + early upload

**Outcome:** Real shop floor matching—not only 1:1.

- [x] Link UI: N files ↔ M rows; coverage column + Inbox selection hint.
- [x] Partial link flag + note (“covers fuel only”) — keeps request `needed`, sets `receipt_status=partial`.
- [x] Early upload (mobile Park early + desktop Park files…): `receipt_files` with no request/tx → parked unmatched.
- [x] Cashier path: Inbox → Link selected…; Today / Open·partial filters on Link tab.
- [x] One receipt covering many ownership rows: allowed; chase groups clear when full link marks received.
- [x] Tests for 1:1, 1:N, N:1, early-then-link, partial.

**Done when:** Staff can match the messy cases without re-uploading or losing the file.

---

### Phase D — Item folders + richer evidence packs

**Outcome:** After approve (and item+payee present), humans can browse by item; packs select what TRA/KRA ask for.

- [x] Materialize approved files into `approved/year/month/<item>/…` (hardlink/copy; canonical stays `files/`).
- [x] Trigger: on approve + after link of already-approved files; `POST /files/rematerialize` when item lands.
- [x] Evidence pack UI: comma-separated items + date range + ownership/payee; “Approved files only” toggle.
- [x] Pack README/index extended with items list + approved_only; API accepts `items[]`.
- [x] Retention unchanged: prune never touches receipt prefixes (Phase 4 coverage).

**Done when:** Accountant can ZIP “all diesel + LATRA for March” and folders on `D:` match item names for spot checks.

---

### Phase E — Upload security hardening

**Outcome:** Malicious or junk files never become “approved evidence”.

- [x] Server-side allowlist + magic-byte verification on mobile and desktop upload paths.
- [x] Quarantine bucket + desk Inbox Quarantine chip / `GET /quarantine`.
- [x] Rate limits on mobile upload per user (`receipt_mobile_uploads_per_hour`).
- [x] Strip JPEG APP1/EXIF where practical; never trust client MIME alone.
- [x] Security tests (exe renamed to .jpg, double extension, oversized, rate limit).

**Done when:** Bad files fail closed with a clear error; good JPEG/PDF still succeed.

---

### Phase F — Mobile polish + atomic UX

**Outcome:** Phone app feels like a product, not a spike.

- [x] Visual system: palette, typography, login, home, request detail (`theme.ts`).
- [x] Home KPIs + upload history screen (on-device).
- [x] Atomic multi-file upload with end summary (sent / failed / request status).
- [x] Early-upload screen → parked inbox (summary + history).
- [x] Permission flows that deep-link to Settings.
- [x] Stable LAN/hotspot config docs in `mobile/README.md`.

**Done when:** Owner can clear a day’s requests with confidence and see history.

---

### Phase G — Hardening & acceptance

- [x] End-to-end script: save row → request → Expo upload → inbox → approve → folder → pack (`test_receipt_phase_g`).
- [x] Early upload → later link → approve.
- [x] One file → three rows; partial then complete.
- [x] Verify still blocks `needed`.
- [x] Checklist sign-off template: `docs/RECEIPT_ACCEPTANCE_CHECKLIST.md` (human rows for accountant + ownership).

---

## Suggested order

```text
A (visibility) → B (don’t lose files) → C (flexible match)
    → D (folders + packs) → E (security) → F (mobile polish) → G (acceptance)
```

A and B are desk-first and unlock daily ops. C unblocks messy reality. D/E/F can overlap slightly once B exists. G last.

---

## Out of scope (still)

- WhatsApp Web scraping
- OCR as the primary matcher
- App Store / Play release (Expo Go / later dev build is enough until Phase F stability)
- Blocking register save on missing receipt

---

## Follow-on — View / storage / print / export

Desk open-before-approve, storage browser, print, consolidate, and polish are tracked in **`docs/RECEIPT_VIEW_STORAGE_PRINT.md`** (Phases 1–6 — all shipped). See that doc + `RECEIPT_ACCEPTANCE_CHECKLIST.md` for desk acceptance rows.

---

## Phase A detail (implement next)

See the short brief below for the first coding slice.
