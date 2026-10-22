# Immediate actions: phases

This is the implementation plan for making import, park, verify, and ordinary navigation feel immediate, and for keeping the app connected while several people use it at once.

It is based on the local API log from 22 Oct 2026 (the process started at 18:17 local time), the current query shapes in the backend, and the same design rules used by data-heavy systems and by QuickBooks-style ledgers: keep the screen on a small stored answer, do heavy work as one batch, and never let a health check wait behind that batch.

## What the log actually showed

The API process did not crash, did not reload, and returned no HTTP 500s in that session.

| What | Count in that log |
| --- | ---: |
| All HTTP requests | 519 |
| `GET /v1/accountant/notification-counts` | 192 |
| `GET /v1/transactions/drafts/count` | 192 |
| `GET /health/ready` (this pings Mongo) | 51 |
| `GET /v1/categories` pages | 30 |

The desktop polls the two badge endpoints about every 2 seconds from [`tahmeed/ui/accountant/dashboard.py`](../tahmeed/ui/accountant/dashboard.py) (`_notification_timer`, 2_000 ms). Each verify poll runs three `count_documents` calls in [`accountant_notification_counts`](../backend/app/api.py). The drafts poll is another `count_documents` in [`count_draft_transactions`](../backend/app/transactions_api.py). The same timer also calls `count_day_issues`, which collects the day's open rows.

The connected light is separate. [`ConnectivityMonitor`](../tahmeed/services/connectivity_service.py) calls `GET /health/ready` every 8 seconds and gives up after 5 seconds. [`ready`](../backend/app/main.py) runs `database.ping()`, which is a Mongo command on the same process that is serving the counts.

When a count or an import is still running, the ping waits in that one process. After 5 seconds the banner says the API cannot be reached and tells the user to check the internet. The process never left this PC.

## Decision: one API, two lanes. Not several products.

Do not split Tahmeed into a separate import service, park service, and verify service. One user already sees "offline" because of query shape and head-of-line blocking, not because one process is too small for the CPU. Extra services would add network hops between pieces that all still talk to the same MongoDB, which is the thing that is busy.

Do split the *work* inside this one API:

| Lane | What it serves | Rule |
| --- | --- | --- |
| Interactive | Login, register reads, save, park, verify click, badges, `/health/live` | Must return in well under a second. Never scans the whole `transactions` collection. |
| Batch | Excel import commit, large feed imports, rebuilding a counter if it drifts | One job, batches of writes, progress the screen can paint. Must not be on the path of `/health/live`. |

Add more API workers only after Phase 2. Today every open accountant window multiplies the counts. Two workers in front of the same unindexed counts would make Mongo busier, not calmer. After the badges are a single document read, two to four workers let several cashiers save and import at once without sharing one queue with the health ping.

This matches how large ledgers behave, including QuickBooks Online's public API guidance:

- A screen does not re-download the whole company file to update a badge. Change data capture and webhooks say "this entity changed"; the client fetches that entity.
- A large import is a job with a status, not a request the window holds open until the last row is painted.
- Writes are batched and idempotent (QuickBooks uses a request id so a retry does not double-post; we already have `client_row_id` and `import_row_key`).
- Their remote batch cap is 50 items because each item is a call to Intuit's servers, with a limit of about 10 concurrent requests and 500 requests per minute per company. We own the database, so our write batch should stay large (`insert_many` / `bulk_write`, hundreds to a couple of thousand documents). Copying their cap of 50 would make us slower.
- Throttling protects the database. Our version of that is: the desktop must not poll counts while a batch is running, and the API must refuse to do a collection-wide count on the interactive lane.

From data-intensive design, the rules we are applying, without copying a book:

- Round trips dominate once the database is on the same machine. Five hundred local queries are slower than one local batch.
- Counts that users look at are derived data. They are updated when a row changes. They are not recomputed by scanning the log.
- A liveness check answers "is the process up?". A readiness check answers "can it serve traffic?". The desktop connected light is liveness. It must not ping Mongo.
- Heavy work is a batch with backpressure. The interactive lane stays short so one import cannot mark every other user offline.

## What is already in good shape

Do not rewrite these. Later phases build on them.

- Daily and master import already insert in batches of 500 in [`commit_daily_import_rows`](../backend/app/import_ops.py) and [`commit_master_import`](../backend/app/import_ops.py) (`IMPORT_BATCH_SIZE`). Duplicate rows are rejected by partial unique indexes `uniq_daily_import_row` and `uniq_master_import_serial` in [`backend/app/cli/migrate.py`](../backend/app/cli/migrate.py).
- Register create already has `POST /v1/transactions/bulk-create`: one prefetch by `client_row_id`, then `insert_many` ([`bulk_create_transactions`](../backend/app/transactions_api.py)).
- Submit for verify is already one `update_many` ([`submit_day_for_verify`](../backend/app/transactions_api.py)).
- Idempotent register rows use `uniq_cashier_client_row`.

The slow follow-up is everything that runs *around* those writes: recounts, catalog pages, the health ping, and a dialog that builds one heavy card per parked truck.

## Indexes that exist, and why they do not save the badges

[`migrate.py`](../backend/app/cli/migrate.py) creates `pending_verification` on `(verified, rejected)`. The verify badge does not use a query that matches that index cleanly. [`inbox_base_query`](../backend/app/accountant_ops.py) is:

- `verified: False`
- `rejected`, `trashed`, and `deletion_requested` are `$ne: True`
- `register_status` is `submitted` or missing

`$ne` and `$or` make `count_documents` walk every matching document. The drafts query [`DRAFT_OR_PARKED_MATCH`](../backend/app/transaction_ops.py) is the same shape on `register_status: draft|parked` plus several `$ne` fields. There is no index whose prefix is `register_status`.

Catalog lists in [`list_collection`](../backend/app/api.py) call `count_documents` and then `.skip(offset).limit(500)`. The log shows offsets 0, 500, 1000, 1500, and 2000. Skip walks past the earlier documents on every page, and the desktop repeats the walk when the register opens again.

Mongo client in [`backend/app/database.py`](../backend/app/database.py) is one `AsyncMongoClient` per process, default pool, `serverSelectionTimeoutMS=5000`. Uvicorn in [`scripts/dev_api.ps1`](../scripts/dev_api.ps1) is one worker with `--reload`. A file save kills that process. In-flight imports die and the app shows offline until the new process answers. That is a real "backend closed" path, separate from the timeout path. This session did not hit it.

## Phase order

Do the phases in this order. Each one is safe to ship alone. Later phases assume the earlier ones so we do not speed up a write and then immediately recount the whole collection.

| Phase | User-visible result | Do it first because |
| --- | --- | --- |
| 1 | Connected stays connected during work | The false offline is what blocks testing everything else |
| 2 | Badges stop scanning on a timer | This is most of the 519 requests and the Mongo queue |
| 3 | Remaining list queries use an index | Inbox, by-date, and drafts lists still run after badges are cheap |
| 4 | Opening the register does not page the catalog | Repeated on every register open |
| 5 | Import paints as the batch lands | The write path is already batched; the wait is the blocked screen and the polls |
| 6 | Park trucks does not build a card per row | Dialog cost, then one save, then one counter update |
| 7 | Verify click does not recount the ledger | The update is already one call |
| 8 | Several users at once | Workers only after phases 1–2 |

---

## Phase 1 — Connected means the process is up

**Goal.** A busy import or a slow count must not flip the app to offline. The banner may say the server is working. It must not say to check the internet when the API process is on this PC.

**Backend**

- Add `GET /health/live` if the desktop is not already calling the one in [`backend/app/main.py`](../backend/app/main.py). It returns `{"status":"ok"}` and does not touch Mongo. It already exists as `live`. Point the desktop at that.
- Leave `GET /health/ready` for operators and for startup. It keeps the Mongo ping. The desktop connected light does not call it on a timer.

**Desktop** — [`tahmeed/services/connectivity_service.py`](../tahmeed/services/connectivity_service.py)

- `_probe_api` requests `health/live`, not `health/ready`.
- Timeout stays short (2 seconds is enough on loopback). A timeout still means the process did not answer.
- `banner_message` for a timeout while a save or import is in flight: "The app is still working. Connection was not lost."
- `banner_message` for connection refused: "The Tahmeed API is not running." Do not mention the internet for `API_BASE_URL` on `127.0.0.1`.
- Keep the 8 second interval. Live is cheap. Do not poll it faster.

**During long work**

- Import, park-all save, and verify already have places that pause polls (`pause_background_polls`, `pause_notification_polling`). Extend that pause to the connectivity timer for the duration of the batch, then resume. A paused light stays on the last "connected" state. It does not flip to offline because a probe was cancelled.

**Dev reload**

- In [`scripts/dev_api.ps1`](../scripts/dev_api.ps1), keep `--reload` for coding, and add a switch `-NoReload` that starts uvicorn without it. Testing import and park uses `-NoReload` so saving an unrelated file does not kill the request.
- Document that in the script comment. A reload is logged as `Shutting down` plus a new `Application startup complete`. That is the "backend closed" case. Phase 1 does not remove reload by default.

**Done when**

- With the API up and idle, the status bar stays Connected.
- Starting a long import does not show the internet message.
- Stopping the API process shows "API is not running" within one probe interval.
- `GET /health/ready` still returns 503 when Mongo is down. The desktop does not use that route for the badge.

**Tests**

- Probe helper treats `health/live` 200 as online and does not require a database.
- Banner text for loopback timeout does not contain "internet".
- Existing connectivity tests updated for the new path.

---

## Phase 2 — Badges are stored numbers

**Goal.** Stop `count_documents` on `transactions` every 2 seconds. The sidebar reads one small document. Writes adjust it.

**New document.** Collection `system_counters`, one document `_id: "badges"`:

```text
verify            int    unverified submitted rows in the verify inbox
drafts            int    register_status draft, not verified, not trashed
parked            int    register_status parked, same exclusions
deletion_requests int
restore_requests  int
updated_at        datetime
```

The verify badge on screen is `verify + deletion_requests + restore_requests`, which is the formula in `accountant_notification_counts` today. The drafts badge is `drafts + parked`, which is what `DRAFT_OR_PARKED_MATCH` counts today.

**Seed.** On API startup, and from `python -m app.cli.migrate`, run the current count queries once and upsert the document. That is the only full scan, and it happens when the process starts, not every 2 seconds.

**Adjust on write.** Add one helper, `adjust_badges(db, deltas: dict[str, int])`, using `$inc`. Call it from the write paths that change membership, with the delta of how many rows entered or left each bucket. Do not recompute.

Call sites, each computed from the row's old status and new status so a retry is idempotent:

- [`bulk_create_transactions`](../backend/app/transactions_api.py) and the single-create route: `+drafts` for each inserted draft, `+parked` for each inserted parked row.
- Bulk patch / unpark / clear-parked: parked to draft, or parked to trash.
- [`submit_day_for_verify`](../backend/app/transactions_api.py): `-drafts`, `+verify` by `modified_count`.
- Verify, reject, trash, restore, deletion-request routes in the accountant action modules: the matching decrement.
- Import commit when it inserts directly as verified master rows: no badge change. Import that lands as drafts or parked: the same deltas as create.

If a path cannot cheaply know the delta (a query that matches an unknown number of rows), it may `$inc` by `modified_count` only when the filter guarantees every matched row was in one bucket. `submit_day_for_verify` is that case. A filter that might match mixed statuses must load the affected ids in the same update (or use a pipeline update) and count the transitions. Do not fall back to a full collection count.

**Read.** `GET /v1/accountant/notification-counts` and `GET /v1/transactions/drafts/count` become `find_one` on `system_counters`. Response shape stays the same so the desktop poll can stay for one release. The poll is no longer a scan.

**Drift repair.** A nightly or startup comparison of the stored number against one real count, logged if they differ, then overwrite. Not on the request path.

**Desktop poll.** After the read is a `find_one`, slow the accountant timer from 2 seconds to 15 seconds. Several open windows then cost one tiny read each, not four collection scans. Phase 8 can replace the poll with a version number (`badges.updated_at`) so the client skips work when nothing changed.

**Day-issues badge.** [`count_day_issues`](../tahmeed/services/day_issues_service.py) still builds a snapshot. Take it off the 2-second poll. Refresh it when the open register changes, when a save finishes, and when the user opens Day Issues. Debounce at 2 seconds, not 350 ms, and never on the notification timer.

**Done when**

- Idle for one minute produces no `count_documents` on `transactions` in the API log.
- Creating a draft increases the drafts badge by one without a scan.
- Submit moves that row from drafts to verify by the modified count.
- Two accountant windows open do not add visible delay to a save.

**Tests**

- Seed from a fixture of draft, parked, submitted, and trashed rows. Stored numbers match today's count functions once.
- Each write path test asserts the delta, including a second identical bulk-create (idempotent `client_row_id`) that does not double-increment.
- Notification route returns the stored sum and does not call `count_documents` (spy the collection).

---

## Phase 3 — Indexes for the lists we still run

**Goal.** Screens that still query `transactions` (verify inbox, register by-date, draft day list) use an index. Badges no longer depend on this phase, but inbox and by-date will feel slow for every extra user until these exist.

Add in [`backend/app/cli/migrate.py`](../backend/app/cli/migrate.py), and do not call `create_index` on the request path (import already guards this with `_indexes_ensured`; keep it that way).

| Name | Keys | Partial filter |
| --- | --- | --- |
| `inbox_submitted` | `date` descending, `day_order` ascending, `_id` | `verified: false`, `register_status: "submitted"` |
| `register_by_day` | `cashier_id`, `date`, `day_order` | none |
| `drafts_by_status` | `register_status`, `date` | `register_status` in `draft`, `parked` |

Partial filters must use equality, not `$ne`. Rows with missing `register_status` (legacy) are either backfilled to `submitted` or `draft` in the same migrate step, or the inbox query's `$or` on `$exists: false` is removed after the backfill. Until that backfill, the new index will not cover legacy rows. The migrate step prints how many documents lacked `register_status`.

Drop the urge to add an index for every `$ne` field. Those indexes would be large and the badge path no longer needs them after Phase 2.

**Query changes**

- Verify inbox list uses `verified: false` and `register_status: "submitted"` with no `$ne` on the fields that are now implied by status. Trash and deletion keep their own flags, but the inbox query should be equality on the status that defines the queue.
- `GET /v1/transactions/by-date` should hit `register_by_day`. Confirm with `explain` in a test or a one-off migrate check that the winning plan is `IXSCAN`, not `COLLSCAN`.

**Done when**

- `explain` for the inbox first page and for one cashier's by-date is an index scan.
- Opening verify for a day with a few hundred submitted rows returns the page without a multi-second wait.

**Tests**

- Migrate creates the three indexes (inspect `index_information`).
- A fixture with a missing `register_status` is backfilled and then appears in the inbox or the drafts list, not in both.

---

## Phase 4 — One catalog snapshot

**Goal.** Opening the register does not walk items, payees, or fleet with `skip`.

**Backend**

- Add `GET /v1/catalog/snapshot`. One response with active categories, payees, trucks, trailers, motor vehicles, allowed truck labels, and the cashier settings the register reads today as separate calls (`restrict_items`, `restrict_payees`, `restrict_trucks`, `defer_item_to_verify`, `auto_fill_from_description_maps`, `duplicate_check_days`, `export_restrict_surfaces`, `allowed_truck_labels`).
- Build it without `skip`. A few thousand items fit in one `find` sorted by name. Cache the JSON on the API process. Store `catalog_version` (an integer bumped on any catalog write).
- Response header `ETag: "<catalog_version>"`. If the client sends `If-None-Match` and the version matches, return 304 and no body.
- Invalidate the cache in the category, payee, truck, trailer, motor vehicle, and settings write routes. Bump the version there. Do not scan `transactions`.

**Desktop**

- Register startup calls the snapshot once ([the many calls in the 22 Oct log](../tahmeed/ui/cashier/excel_grid.py) are the loads of categories, fleet, settings, and descriptions). Repeat the call only when a write returns a new version, or when the user opens Manage Items / fleet and comes back.
- Delete the client loop that requests `limit=500` at offsets 0, 500, 1000, 1500, 2000.

**Descriptions.** `GET /v1/transactions/descriptions?limit=5000` is a separate cache. Keep it, but do not refetch it on every register focus. Same version idea if description writes are rare; otherwise refetch only when the register opens a new day.

**Done when**

- One register open produces one catalog request (or a 304), not five category pages plus a settings call per key.
- Adding an item is visible in the register after the next snapshot, without restarting the API.

**Tests**

- Snapshot returns active categories only, stable order, and an ETag.
- A category write changes the ETag and the next snapshot contains the new name.
- `If-None-Match` with the current tag returns 304.

---

## Phase 5 — Import finishes as a batch, and the screen stays usable

**Goal.** The user sees progress, can still tell the app is connected, and the grid fills from the commit result. The truck scan stays on the desktop because it needs the correction dialog. It must not freeze the window, and it must not run while badge scans are also running.

**Truck scan**

- [`scan_import_trucks`](../tahmeed/services/import_truck_check.py) is CPU work over every row. Run it off the Qt event loop (a worker thread with progress callbacks), in chunks of about 2,000 rows, yielding so the window can paint "Checked 2,000 of N".
- The correction dialog must not build a full widget card per issue when the issue count is large. Phase 6 replaces that widget. Until Phase 6 ships, cap the open dialog at a page of issues (for example 100) with a count of how many remain, so a bad file cannot allocate thousands of forms at once.

**Commit**

- Keep `IMPORT_BATCH_SIZE` at 500. That is already one `insert_many` per batch and matches idempotent retries via `import_row_key`. Do not drop it to QuickBooks' remote cap of 50.
- [`ensure_import_indexes`](../backend/app/import_ops.py) stays behind `_indexes_ensured`. Do not `create_index` on every batch.
- The commit route returns `{inserted, duplicates_skipped, upload_id, min_date, max_date}` as it does now. The desktop paints rows from that result and from the rows it already holds. It does not `GET` the day three times after a successful commit.
- While the commit HTTP call is in flight, polls and the connectivity probe stay paused (Phase 1). The status line shows "Importing batch 3 of 8", driven by client-side batch count if the server still does the batches inside one request. If a single request regularly runs longer than 10 seconds, split the client into several commit calls of one batch each so a failure retries only the unfinished batch. Each call is still one `insert_many`. The screen updates after each call.

**Do not add a queue service in this phase.** A background job table is justified only if a commit still exceeds about 10 seconds after the polls are gone. If we add it later, it is a `import_jobs` document in Mongo and a task inside the same API process, not a new network service. The desktop polls that one document, or receives the result of each batch.

**Done when**

- A multi-thousand-row import keeps the window painting progress.
- The status bar does not say offline.
- Retrying the same file does not duplicate rows (`uniq_daily_import_row`).
- The API log during the import shows the commit requests and no badge `count_documents`.

**Tests**

- Commit of 1,000 rows inserts 1,000, second commit of the same keys inserts 0.
- Client batch splitter (if added) retries only the failed batch.

---

## Phase 6 — Park trucks is one list and one save

**Goal.** Clearing a wrong truck, marking No truck, or fixing a plate updates the grid immediately. The dialog is not a separate form per row.

**Dialog** — [`tahmeed/ui/dialogs/truck_correction_dialog.py`](../tahmeed/ui/dialogs/truck_correction_dialog.py)

- Replace the per-issue card (its own line edit, badges, and buttons) with one table: row label, pasted value, replacement, status, and actions Apply / Allow / No truck.
- The table is the same data the cards show. Actions stay per row and for the selection. "No truck" and Apply-on-blank stay the parked-tab behavior added for empty trucks.
- Virtualize if the toolkit table already recycles rows. Do not parent a widget per row.

**Save**

- Local parked rows stay local until Save, as they do now.
- Save uses the existing bulk create / bulk patch. One request for the parked set, not one request per row.
- On success, Phase 2's `adjust_badges` runs once with the count of rows that entered or left `parked`. The register removes those rows from the Parked surface in memory. It does not reload the day and does not recount.

**Done when**

- Opening Fix trucks for a few hundred parked rows shows a table within a second.
- No truck on a blank row still moves that row to My entries with an empty truck (existing tests in `tests/test_register_view_draft.py` and `tests/test_truck_correction_dialog.py` stay green).
- Save of the parked set is one bulk call, and the drafts/parked badge matches without a collection scan.

**Tests**

- Dialog builds one table for N issues, not N card frames.
- Bulk save delta: 10 parked rows promoted to draft changes the counter by `-10` parked and `+10` drafts.

---

## Phase 7 — Verify does not recount

**Goal.** Submit and the verify action return as soon as the update is acknowledged. The inbox shows the new rows from that result.

**Submit**

- [`submit_day_for_verify`](../backend/app/transactions_api.py) stays one `update_many`.
- Add the Phase 2 delta: `drafts -= modified_count`, `verify += modified_count`.
- Desktop removes the day's drafts from the local badge using the response `updated`. It does not call `drafts/count` or reload categories.

**Inbox**

- First page uses the Phase 3 `inbox_submitted` index, paged by date and `day_order`, not a count of the whole queue.
- Verifying or rejecting a page is one `bulk_write` (or one `update_many` by id list), then `verify -= n`. The page drops those ids locally.

**Done when**

- Submit on a day of a few hundred drafts returns the modified count and the verify badge has moved by that count.
- The verify screen's first page is an index scan.
- No `count_documents` on `transactions` runs because the user opened Verify.

**Tests**

- Submit delta matches `modified_count`, including a second submit of the same day that modifies 0 and does not change the counter.
- Inbox page query explain is an index scan on the fixture.

---

## Phase 8 — Several people at once

**Goal.** Cashiers and an accountant can import, save, and verify together without any of them seeing offline, and without Mongo running four full scans per person per 2 seconds.

Only start this after Phases 1 and 2 are in production use locally.

**Process model**

- One API program. For a shared office server, run uvicorn with 2 workers, no `--reload`. Each worker has its own Mongo client ([`Database`](../backend/app/database.py) already constructs the client in the process). Set `maxPoolSize` explicitly to 20 per worker so four workers cannot open hundreds of connections by default.
- Interactive routes stay on those workers.
- If Phase 5's commit is still over about 10 seconds, run the batch function in a single in-process queue (one import at a time per database) so two giant imports cannot saturate Mongo. The HTTP call returns a job id. This queue lives in the API process. It is not a second deployable service.
- Do not add a second API for "reads" and a third for "writes". They would share Mongo and the failure mode would be the same, with more timeouts between them.

**What each extra user costs after Phase 2**

- One `find_one` for badges every 15 seconds, or nothing when `updated_at` is unchanged.
- Their own save or import batches.
- Catalog 304s when the version matches.

**What we refuse**

- Per-user `count_documents` on a timer.
- Desktop probes of `/health/ready`.
- `create_index` during a user request.
- A worker count high enough that import batches and badge repairs run in parallel without a limit. Mongo on this PC is the capacity. Workers exist so a health check and a save are not stuck behind one another, not to multiply scans.

**Done when**

- Two desktop sessions can save different days at the same time while a third holds the verify screen, and none of them flips to offline.
- Mongo connection count stays near `workers × pool` under that test, not hundreds.
- The API log for a quiet minute with three clients is a handful of badge reads and live probes, not hundreds of counts.

**Tests**

- A concurrency test: one bulk-create and one submit overlapping, counters end at the sum of the deltas, no lost increment (`$inc` is atomic per document).
- Health live answers while a test holds a long batch (the live route must not take the batch lock).

---

## What we will not do

- A microservice per action (import, park, verify). Same database, more timeouts.
- Copying QuickBooks' batch size of 50. That limit is for their hosted API. Our `insert_many` of 500 is the right local batch.
- Caching badge counts in the desktop only. The next window would still scan, and two windows would still pile onto Mongo.
- Raising the 5 second probe timeout and leaving it on `/health/ready`. That hides a stuck Mongo ping. It does not make actions immediate.
- Turning off `--reload` with no switch. Coding still wants reload. Testing a big import uses `-NoReload`.

## How we know a phase worked

For each phase, before calling it done, repeat the check that produced the 22 Oct log: run the API, use the screen the phase is about, and read the uvicorn log.

| Phase | Log should show | Log should not show |
| --- | --- | --- |
| 1 | `GET /health/live` about every 8 seconds | A burst of `health/ready` during an import, or a reload unless a file was saved |
| 2 | `notification-counts` and `drafts/count` as fast `find_one`s, at most every 15 seconds | `count_documents` on `transactions` while idle |
| 3 | Inbox and by-date plans are `IXSCAN` | `COLLSCAN` on those two routes |
| 4 | One `GET /v1/catalog/snapshot` or a 304 per register open | Five `categories?offset=` calls |
| 5 | One commit per batch, progress on screen | Badge counts interleaved with the commit |
| 6 | One bulk save for the parked set | One HTTP call per parked row |
| 7 | One `submit-day`, badge delta equals `updated` | A full recount after submit |
| 8 | Live probes still return while another user imports | Any session's connected light dropping because another user is busy |
