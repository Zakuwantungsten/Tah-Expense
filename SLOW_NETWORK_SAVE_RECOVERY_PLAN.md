# Slow Network Save & Recovery — Root Cause & Architecture Plan

Investigation of Daily Register **Save** under slow/failed network, local
**“Recovered …”** draft restore, and expense **total inflation** when recovered
rows are stacked on already-saved rows.

**Status:** Plan only (no code changes yet). Aligns with existing docs:
`SAVE_DRAFT_ISSUES.md` (Save UX / drafts), `MERGED_VIEW_REFACTOR.md` (My vs Merged).

---

## 1. Incident reconstruction (user report)

| Observation | Likely meaning in current code |
|-------------|--------------------------------|
| ~1 minute to Save | Sequential per-row HTTP (dup-check + insert), not one bulk write |
| Slow network *or* app | Both: each call waits up to **15s** read timeout; N rows → minutes |
| “Recovered some rows” | Local draft restore: *“Recovered N new entries from before the app closed”* |
| Saved **41** rows | Partial or completed inserts reached Mongo as server drafts |
| Original day had **~221** | Already on server / in saved grid prefix |
| After recovery, totals looked like **221 + recovered (+ the 41 again)** | Restore **appends** local `new_rows` on top of `_populate` from DB **without deduping** against rows that already inserted |

**Financial risk:** Recovered copies inflate on-screen TZS/USD footers immediately.
If the cashier clicks Save again (or soft-dup is accepted / missed), Mongo can
gain **duplicate expense rows** — major ledger discrepancy.

---

## 2. How the app works today (relevant paths)

### Two “drafts” (same glossary as `SAVE_DRAFT_ISSUES.md`)

| Kind | Storage | Purpose |
|------|---------|---------|
| **Server draft** | Mongo `transactions` (`register_status="draft"`) | Saved-but-not-submitted |
| **Local draft** | `%LOCALAPPDATA%\meedexpe\register_drafts\{user}_{date}_{my\|merged}.json` | Crash recovery of grid cells |

### Save pipeline (`DailyRegister._do_save_body`)

1. Update dirty **saved** rows (by Mongo `_id`).
2. For each typed **new** row (My mode only):
   - `POST /v1/transactions/check-duplicates` (one RTT per row)
   - Optional Duplicate Review dialog
   - `POST /v1/transactions` via `save_transaction` (one RTT + optional audit)
3. On **any** error → “Save — partial errors”, **`_flush_local_draft()`**, **no reload**, `return False`.
4. On **full** success → clear local drafts → toast → `_load_date`.

**Critical gaps in the insert loop:**

- After a successful `save_transaction`, the row is **not** promoted into `_saved_*`.
- Local draft is **not** pruned of that row mid-save.
- Server create is a blind `insert_one` — **no client UUID / idempotency key**.

```6125:6149:tahmeed/ui/cashier/excel_grid.py
                try:
                    await save_transaction(tx)
                    saved += 1
                    self._pending_row_meta.pop(row, None)
                except Exception as exc:
                    errors.append(f"Row {row + 1}: {exc}")
        ...
        if errors:
            ...
            self._flush_local_draft()   # still captures ALL editable rows with data
            ...
            return False                # no _load_date; grid unchanged
```

### Local recovery (`_restore_local_draft`)

After every successful `_load_date` → `_populate(txs)` then restore:

- `dirty_saved` matched by `tx_id` (safe).
- `new_rows` **always appended** into the editable zone — **no compare** to loaded server rows.
- Message: *“Recovered … Click Save to store them on the server.”*

```5780:5797:tahmeed/ui/cashier/excel_grid.py
            if new_entries and not self._merged_mode:
                start = self._first_empty_editable_row()
                ...
                for offset, entry in enumerate(new_entries):
                    ...
                    self._write_row_values(row, values)
                    ...
                    new_applied += 1
```

Local `new_rows` payload has **no stable client row id** — only cell text + optional import `pending_meta`.

### Network behavior

| Piece | Behavior |
|-------|----------|
| `api_client` | `httpx.Timeout(15.0, connect=5.0)` |
| Connectivity banner | Informational only — **does not queue or block Save** |
| Retries | **None** on timeout; failed row stays editable / in local draft |
| Timeout after server commit | Client thinks fail; Mongo already has the row → classic at-least-once without idempotency |

Import feeds already use **`insert_many_idempotent` / `dedup_id`**. Register create does **not**.

---

## 3. Root causes

### A. Slow Save (~1 minute)

**Primary:** O(N) sequential round-trips.

For ~40 new rows on a slow link (~1–2 s/call):

- ~40× duplicate check + ~40× create ≈ **80–160+ seconds**
- Plus settings, `next_day_order`, optional audit per insert, then full day reload

Worst case: each call sits until the **15 s** timeout → multi-minute Save or partial errors.

This is an **architecture/efficiency** issue, not only “bad Wi‑Fi.”

### B. Expense inflation / duplicate recoveries

**Primary bug class:** successful inserts are not excluded from local recovery / retry until a *clean full* save clears drafts and reloads.

| Scenario | What happens |
|----------|----------------|
| **Partial errors** | 41 POSTs succeed; later rows fail → flush local draft still containing **all** typed rows (including the 41) → no reload → Save again or crash-restore **re-adds** them |
| **Crash / kill mid-save** | Local autosave still has full `new_rows`; restart loads 41 from DB **and** restores same cells as new → footer = original + recovered copies |
| **Timeout after commit** | Server has row; client errors → same as partial; soft-dup may warn but does not hard-block |
| **Soft-dup is advisory** | User can “save anyway”; check can fail open (`except: dupes = []`) |

**Why totals jump before a second Save:** `_update_footer` sums **every** grid row with money — saved prefix **plus** restored editable copies.

### C. Missing identity layer

| Layer | Identity today | Dedup? |
|-------|----------------|--------|
| Saved grid rows | Mongo `_id` | N/A |
| New / recovered rows | Row index / cell text only | **None vs server** |
| `POST /v1/transactions` | New ObjectId every time | **Not idempotent** |
| Soft duplicate check | Content similarity (N days) | Advisory only |

Without a **client-generated idempotency key**, retries after uncertainty are unsafe for money.

---

## 4. Industry research — recommended architecture

Synthesized from offline-first / API design practice (Stripe idempotency, durable
outbox + receipts, batch APIs with per-item keys, ledger integrity guidance):

### Principles that matter for *expenses*

1. **At-least-once delivery + idempotent apply**  
   Networks lose ACKs. Clients must retry. Servers must treat the same client key as
   “return original result,” never a second insert ([Stripe Idempotent requests](https://docs.stripe.com/api/idempotent_requests)).

2. **Durable outbox before relying on the network**  
   Persist intent locally first; drain when online; one in-flight batch; backoff with
   jitter ([offline outbox patterns](https://beefed.ai/en/offline-first-queueing-sync),
   [Concile receipted outbox](https://concile.dev/docs/client/offline-sync)).

3. **Batch + per-item keys**  
   A single batch `Idempotency-Key` is not enough after partial success. Each item needs
   a stable `client_row_id` / external id and upsert / unique constraint so full-batch
   retries do not duplicate successes
   ([batch HTTP API design](https://matheuspalma.com/blog/designing-batch-http-apis-partial-success-limits-and-safe-writes)).

4. **Reconcile before re-insert**  
   On timeout / unknown outcome: pull server truth for the day (or by client keys),
   then retry **only** still-missing rows — never blindly re-POST the whole grid.

5. **Ledger caution on optimism**  
   Show **pending** state; promote to “saved” only after server confirmation (or after
   idempotent ACK). Footers for money should not double-count pending copies of the
   same logical row.

### Architecture chosen for MeedExpe (pragmatic fit)

We already have: local JSON drafts, REST → Mongo, soft-dup UX, import-side
idempotent inserts. We do **not** need a full CRDT sync engine.

**Target model: “Register Outbox + Idempotent Bulk Create”**

```
┌─────────────────────────────────────────────────────────────┐
│  Grid (UI)                                                  │
│  • Each new row gets client_row_id (UUID) at first edit     │
│  • Status: editing | pending_sync | synced | error          │
└───────────────┬─────────────────────────────────────────────┘
                │ autosave / Save
                ▼
┌─────────────────────────────────────────────────────────────┐
│  Local outbox (evolve register_draft JSON or SQLite later)  │
│  • new_rows keyed by client_row_id                          │
│  • prune on ACK; never restore a key already on server      │
└───────────────┬─────────────────────────────────────────────┘
                │ drain (bulk preferred)
                ▼
┌─────────────────────────────────────────────────────────────┐
│  API: POST /v1/transactions/bulk-create                     │
│  • per-item client_row_id + optional batch Idempotency-Key  │
│  • unique index (cashier_id, client_row_id) or equivalent   │
│  • return per-item {created | already_exists | error}       │
└───────────────┬─────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────┐
│  Mongo transactions (server draft)                          │
│  • same create fields as today + client_row_id              │
└─────────────────────────────────────────────────────────────┘
```

**Accuracy rule:** One logical expense ⇒ one `client_row_id` ⇒ at most one Mongo doc
(for that cashier). Recovery merges by key, never by “append all cells.”

---

## 5. Phased plan

Mark checkboxes as work lands. Prefer shipping Phase 0–2 before bulk polish —
that stops **money inflation**; Phase 3+ makes Save **fast and smooth**.

---

### Phase 0 — Stop the bleed (client-only, high urgency) ✅ DONE

**Goal:** Partial save / crash / restore must not stack duplicates on screen or on retry.

| # | Work | Status | Detail |
|---|------|--------|--------|
| 0.1 | Clear on success | ✅ | `_mark_new_row_saved` clears editable cells after each successful insert |
| 0.2 | Prune local draft | ✅ | Mid-save flush after each success; capture only remaining editable rows |
| 0.3 | Reload on partial success | ✅ | Partial errors with `saved/updated > 0` → flush then `_load_date` |
| 0.4 | Restore dedupe (content heuristic) | ✅ | Multiset fingerprint skip in `_restore_local_draft`; rewrite draft after |
| 0.5 | Footer safety | ✅ | `_update_footer` skips editable rows that claim a saved fingerprint |
| 0.6 | Tests | ✅ | `tests/test_register_draft_restore.py`, fingerprint unit tests in draft service |

**Exit criteria:** Reproduce old scenario (221 saved, save 41 of N, kill/fail) →
recover notice only for **true leftovers**; day total does not become 221+41+leftovers.

**Risk:** Content fingerprint can false-positive on legitimate identical expenses
(mitigated with multiset claims). Phase 1 replaces with UUID.

---

### Phase 1 — Client row identity (foundation for correctness) ✅ DONE

**Goal:** Every new expense row has a stable id from first keystroke / import stage.

| # | Work | Status | Detail |
|---|------|--------|--------|
| 1.1 | `client_row_id` | ✅ | UUID on activate / import / capture / copy; `_pending_row_meta` + draft `new_rows[]` |
| 1.2 | Schema bump | ✅ | `SCHEMA_VERSION = 2`; `ensure_draft_client_row_ids` on load |
| 1.3 | Send on create | ✅ | `Transaction.client_row_id` → API `TransactionCreate` → Mongo create doc |
| 1.4 | Restore by id | ✅ | Skip when id already on loaded saved txs; fingerprint remains fallback |
| 1.5 | UX badges | ⏭ Deferred | Saving/Saved/Retry tint left for Phase 4 outbox UX (not required for identity) |

**Exit criteria:** Recovery merge is key-based, not append-blind.

---

### Phase 2 — Server idempotency (hard guarantee) ✅ DONE

**Goal:** Retries and timeouts cannot create a second expense for the same client row.

| # | Work | Status | Detail |
|---|------|--------|--------|
| 2.1 | Field + unique index | ✅ | `uniq_cashier_client_row` partial unique; migrate `0004_…` + runtime ensure on create |
| 2.2 | Idempotent create | ✅ | Find-before-insert; `DuplicateKeyError` → return existing (HTTP 200) |
| 2.3 | Optional header | ⏭ Skipped | Unique `(cashier_id, client_row_id)` covers creates without Stripe-style header store |
| 2.4 | Timeout reconcile | ✅ | `save_transaction` replays POST once on timeout when `client_row_id` set |
| 2.5 | Soft-dup vs idempotency | ✅ | Soft-dup unchanged (advisory); same id never second-inserts via create path |
| 2.6 | Tests | ✅ | API replay + client timeout replay tests |

**Exit criteria:** Blind Save retry after ACK loss is **safe**.

**Note:** Import path already has `insert_many_idempotent` — register create now mirrors that mindset. Run `python -m app.cli.migrate` (or rely on first create’s ensure) so the unique index exists in each environment.

---

### Phase 3 — Bulk save + slow-network performance ✅ DONE

**Goal:** Saving dozens of rows feels like one operation, not O(N) round-trips.

| # | Work | Status | Detail |
|---|------|--------|--------|
| 3.1 | Batch duplicate check | ✅ | `POST /v1/transactions/check-duplicates/bulk` (+ client helper) |
| 3.2 | Bulk create | ✅ | `POST /v1/transactions/bulk-create`; per-item `created` / `already_exists` / `error` |
| 3.3 | Chunking | ✅ | 50 rows/chunk; overlay `Saving draft… 1–50 of 120` |
| 3.4 | Client drain | ✅ | excel_grid Pass 2 uses bulk dup + bulk create; still `_mark_new_row_saved` |
| 3.5 | Audit | ✅ | Server `txn.bulk_create` once; desktop `txn.bulk_save` once per batch |
| 3.6 | Timeouts | ✅ | Bulk create 60s / bulk dup 45s; timeout replay when all items have `client_row_id` |

**Exit criteria:** ~40 rows on moderate latency finish in **seconds**, not ~1 minute.

---

### Phase 4 — Outbox worker & slow/offline UX (smoothness) ✅ DONE

**Goal:** Connectivity banner and Save share one mental model: work is never lost, never duplicated.

| # | Work | Status | Detail |
|---|------|--------|--------|
| 4.1 | Outbox states | ✅ | `PENDING` / `IN_FLIGHT` / `FAILED` in `pending_meta`; SYNCED = cleared after ACK |
| 4.2 | Background drain | ✅ | Single-flight `_drain_register_outbox`; exponential backoff + jitter on failure |
| 4.3 | Gate Save when offline | ✅ | Enqueue + “Saved locally — will sync” dialog; no failing long Save |
| 4.4 | Unknown outcome | ✅ | IN_FLIGHT → PENDING on load; draft kept until `_mark_new_row_saved` |
| 4.5 | Progress UI | ✅ | Overlay “Syncing N pending…”, sky/red row tint + tooltips; cancel deferred |
| 4.6 | Connectivity integration | ✅ | `connectivity_changed` → schedule drain; also after day load |

**Exit criteria:** Airplane mode → type expenses → back online → auto-sync; totals stay accurate.

**Note:** Ordinary unsaved typing (no `sync_status`) still requires Save while online. Only offline-enqueued / failed rows auto-drain.

---

### Phase 5 — Hardening, observability, cleanup tooling ✅ DONE

| # | Work | Status | Detail |
|---|------|--------|--------|
| 5.1 | Metrics / logs | ✅ | Client `timed_op` / `log_register_event` on save, outbox drain, draft reconcile; server `txn_idempotent_hit` + `txn_bulk_create` counters |
| 5.2 | Admin / support | ✅ | `python -m app.cli.find_duplicate_drafts` (day / cashier / lookback); JSON report of fingerprint groups |
| 5.3 | One-time repair | ✅ | Same CLI `--apply --yes` soft-trashes extras (drafts only; keep oldest; `dedupe_repair` stamp) |
| 5.4 | Docs | ✅ | Help → Register Save & Recovery… + `docs/cashier_register_save_recovery.md` |
| 5.5 | Chaos tests | ✅ | Double-Save guard, Merged drain skip, network-drop → FAILED, mid-ACK prune sibling |

**Exit criteria:** Ops can find/repair legacy inflated drafts; cashiers have in-app guidance; retries are observable in logs.

---


## 6. Suggested implementation order

```
Phase 0  ──►  Phase 1  ──►  Phase 2  ──►  Phase 3  ──►  Phase 4  ──►  Phase 5
(stop dup)    (UUID)       (server)     (fast)       (offline)    (ops)
```

- **Ship 0 alone** if we need a hotfix this week (still fingerprint-limited).
- **0+1+2** = correctness floor for money.
- **3** = fixes the “minute to save” complaint.
- **4** = smoothness under flaky networks.
- **5** = production confidence + cleanup of past incidents.

---

## 7. Key files (touch list)

| Area | Paths |
|------|--------|
| Save / restore UI | `tahmeed/ui/cashier/excel_grid.py` |
| Local draft | `tahmeed/services/register_draft_service.py` |
| API client / timeouts | `tahmeed/services/api_client.py`, `tahmeed/services/cashier_service.py` |
| Create API | `backend/app/transactions_api.py` |
| Indexes / migrations | `backend/app/cli/migrate.py` (pattern from import idempotency) |
| Soft dup | `tahmeed/services/duplicate_review.py`, `tahmeed/ui/dialogs/duplicate_review_dialog.py` |
| Connectivity | `tahmeed/services/connectivity_service.py`, `tahmeed/ui/widgets/connectivity_banner.py` |
| Metrics (Phase 5) | `tahmeed/services/register_metrics.py` |
| Dedupe CLI (Phase 5) | `backend/app/cli/find_duplicate_drafts.py` |
| Cashier help (Phase 5) | `docs/cashier_register_save_recovery.md`, Help menu |
| Tests | `tests/test_register_*`, `tests/test_register_chaos.py`, `backend/tests/` create replay / find-dup |

---

## 8. Research references

| Topic | Source |
|-------|--------|
| Idempotent POST / retry after timeout | [Stripe — Idempotent requests](https://docs.stripe.com/api/idempotent_requests) |
| Durable outbox + drain | [Offline-First Queueing & Sync](https://beefed.ai/en/offline-first-queueing-sync) |
| Receipted outbox (retry ≠ re-execute) | [Concile — Offline sync](https://concile.dev/docs/client/offline-sync) |
| Batch APIs + per-item keys | [Designing batch HTTP APIs](https://matheuspalma.com/blog/designing-batch-http-apis-partial-success-limits-and-safe-writes) |
| Bulk idempotency pitfalls | [Designing Bulk APIs (Syslabs)](https://www.syslabs.in/technical-articles/designing-bulk-and-batch-api-endpoints-that-do-not-fall-over-under-load) |
| Client consistency / change feed | [Client-Side Consistency & Sync](https://andrewaltimit.github.io/Documentation/docs/distributed-systems/client-side-consistency.html) |
| Ledger integrity & idempotency | Resilient ledger / posting guidance (idempotency keys, no double post on worker retry) |

---

## 9. Non-goals (this plan)

- Full CRDT multi-master sync across cashiers (Merged view stays server-authoritative).
- Replacing Mongo or moving register writes offline-only forever.
- Changing Verify / submit-day business rules (except that duplicates must not reach Verify inflated).

---

## 10. Success metrics

| Metric | Before (incident) | Target |
|--------|-------------------|--------|
| Save 40 new rows (moderate latency) | ~1+ minute | &lt; ~5–10 s with bulk |
| Partial save + restore | Totals inflate; possible Mongo dupes | Only true unsaved rows restored |
| Timeout after server commit + retry | Second insert possible | Same `client_row_id` → one doc |
| User message “Recovered…” | May include already-saved work | Only leftover pending keys |

---

*Next step when ready: implement Phase 0 (+ tests), then Phase 1–2 for hard financial safety.*
