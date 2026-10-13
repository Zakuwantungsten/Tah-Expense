# Temp notes — Cashier Overview payee / daily charts

Scratch pad while fixing Top Payees + adding a daily-month chart.
Delete after the work ships (or fold into release notes).

---

## Product decisions (confirmed)

- Daily chart: **money + entry counts** (both).
- Top payees: **exclude Unassigned**.

## What we think the user wants

1. **Fix** Cashier Overview **Top payees** (it feels broken / empty).
2. **Add** a new graph (same family as Top payees) that shows **daily / by-date spend for the current month**.
3. Put that **new daily chart first** in the layout.
4. Make sure period/currency toggles and data actually render.

Scope: `tahmeed/ui/cashier/overview.py` + `GET /v1/transactions/overview-charts` pipeline  
(`cashier_overview_ops` → `transactions_api` → `cashier_service.get_overview_charts` / `bucket_payee_spend`).

Not Day Issues payee mapping (separate track), unless empty payees explain chart content.

## Shipped (implementation)

- `daily` map on overview-charts (tzs/usd/zmw + count).
- Named payee pool (excl. Unassigned); client re-ranks by selected period.
- Blank currency treated as TZS.
- UI: **This month by day** (money + entries) first; Top payees default Month.

---

## Current layout / data path

```
CashierOverview.refresh → get_overview_charts(year) + get_overview_stats()
API: GET /v1/transactions/overview-charts?year=
  → compute_overview_charts(docs)
     items[]          (donut)
     payees.series[]  { key, label, daily: { "YYYY-MM-DD": {tzs,usd,zmw} } }
UI: bucket_payee_spend(payees, period∈{month,3m,year}, year, currency)
     → labels + series.values for _LineChartWidget
```

Existing charts row: **Top payees (line, stretch 6)** | **By item (donut, stretch 4)**  
Period toggle defaults to **Year**. Month already means “spend by day this month” for payees only.

---

## Root issue 1 — Top payees ranked for year, then Month/3m slices that list

**Symptom:** KPI row can show this-month entries/TZS, but Top payees on **Month** (or even Year) looks empty / flat / “No spend for this period.”

**Code:**

- Rank once for whole year, take top N:
  - `backend/app/cashier_overview_ops.py` ~140–147 (`payee_ranked` on `payee_totals`, then `[:top_payees]`)
- Client only buckets that fixed list:
  - `tahmeed/services/cashier_service.py` `bucket_payee_spend` + `_overview_period_spec`
  - UI: `CashierOverview._refresh_payee_chart` / `_on_period_changed`

**Why it breaks:**

- Top-5 are **year winners**.
- **Month** view only plots those 5’s days in the current month.
- This month’s real payees may not be in that year top-5 → empty chart while month KPIs are non-zero.
- Even Year view can look weak if currency/filter issues (see RI-3) or almost all spend is unassigned/blank currency.

**Fix direction:**

- Re-rank top payees **inside the selected period** (needs period param on API, or send richer payload), **or**
- Always return enough daily/payee data for client re-rank; simplest UX fix: API accepts `period` / ranks for month when UI asks Month.
- At minimum: when period=month, rank by current-month totals before building series.

---

## Root issue 2 — No true “daily transactions this month” series

**Symptom:** User wants a graph of **day-by-day activity/spend for the month**. Top payees Month view is the wrong model (multi-series by payee, and only top N).

**Code gap:**

- `compute_overview_charts` builds `payee_daily` and `items` only — **no** `daily` totals / counts for all txs.
- Summing top-payee daily client-side **under-counts** (other payees omitted).

**Fix direction:**

- Extend `compute_overview_charts` (and API payload) with e.g.:
  ```json
  "daily": { "2026-10-01": {"tzs":…,"usd":…,"zmw":…,"count":…}, … }
  ```
  for the full year (or at least current month days).
- New UI chart “Daily spend” / “This month by day” using same `_LineChartWidget`, single series, **first** in layout.
- Reuse `_overview_period_spec(..., "month")` bucketing or a dedicated month-only helper.

---

## Root issue 3 — Silent failures + stats vs charts mismatch

**Symptom:** Charts stay blank with no error; KPIs may still populate.

**Code:**

- `CashierOverview._load` — `except Exception: pass` (and per-future `return_exceptions=True` without surfacing chart errors).
- `overview-stats` (`transactions_api` ~246+):
  - does **not** filter `discarded`
  - `tzs_total` = `$sum: "$amount"` with **no currency filter**
- `overview-charts`:
  - filters `discarded: {$ne: True}`
  - drops rows with unknown/empty currency (`cashier_overview_ops` ~120–123)

**Fix direction:**

- Don’t swallow chart load errors (log / empty-state message).
- Align discarded/currency rules between stats and charts where product intends parity.
- Treat blank currency as TZS (or same default as register) if that’s how legacy rows were saved.

---

## Root issue 4 — UX defaults fight the “daily month” story

**Code:**

- `_PeriodToggle` + `CashierOverview._period` default **`year`**.
- Initial subtitle hard-coded `"Spend by month"` before first paint (`overview.py` ~667) — mismatched copy.
- Desired new chart wants **month/daily first**; Top payees can keep Month/3m/Year.

**Fix direction:**

- New daily chart: month-first, no year default confusion.
- Top payees: default period to **month** (optional) once RI-1 is fixed; fix subtitle from bucket result only.

---

## Root issue 5 — Empty payee field → “Unassigned” dominates (content, not crash)

**Code:** `payee_label` → blank payee becomes `"Unassigned"` (`cashier_overview_ops.py` ~21–23).

**Note:** Day Issues work increases real payee fill over time. Chart may “not show payees” when register rows still lack Payee. Separate from ranking bug; consider excluding Unassigned from top-N or showing it last — product call.

---

## Proposed layout after fix

```
KPI row (unchanged)
┌─ Daily spend (this month, by day) ─────────────┐  ← NEW, first
├─ Top payees (line) ──────────────┬─ By item ───┤
└──────────────────────────────────┴─────────────┘
```

Or stacked: Daily full width first, then Top payees | By item as today.

---

## Files to touch (implementation checklist)

| Area | Files |
|------|--------|
| Aggregate | `backend/app/cashier_overview_ops.py` |
| API | `backend/app/transactions_api.py` (`/overview-charts`) |
| Client service | `tahmeed/services/cashier_service.py` |
| UI | `tahmeed/ui/cashier/overview.py` |
| Tests | `backend/tests/test_cashier_overview_ops.py`, `backend/tests/test_transactions_api.py`, `tests/test_cashier_overview_charts.py` |

---

## Open product questions (ask only if blocked)

1. Daily chart metric: **money** (per currency toggle) vs **entry count** vs both?
2. Top payees: exclude **Unassigned** from top N?
3. Daily chart: current calendar month only, or share Month/3m/Year toggles?
