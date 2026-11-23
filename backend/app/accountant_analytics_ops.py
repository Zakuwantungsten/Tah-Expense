"""Accountant Analytics helpers — period windows, feed totals, avg/day."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from .accountant_overview_ops import safe_double, sum_amount_if_currency
from .feed_ops import not_trashed_clause
from .reconciliation_ops import ENTITY as SM_BURHANI_ENTITY

PeriodKind = Literal["year", "month"]


@dataclass(frozen=True)
class SeparateFeedSpec:
    """One SEPARATE EXPENSES sidebar tab and how to sum its spend."""

    key: str
    label: str
    currency: str
    collection: str  # imported_feeds | separate_expenses | reconciliation_entries
    match: dict[str, Any]
    date_field: str
    amount_expr: dict[str, Any]
    absolute: bool = True


def _abs_field(field: str) -> dict[str, Any]:
    return {"$abs": safe_double(field)}


def _zambia_spend_expr() -> dict[str, Any]:
    """Debit is spend; credit is top-up — count debit only (absolute)."""
    return {"$abs": safe_double("debit")}


# All SEPARATE EXPENSES tabs (excluding Uploads Trash).
SEPARATE_EXPENSE_FEEDS: tuple[SeparateFeedSpec, ...] = (
    SeparateFeedSpec(
        key="toll_plaza",
        label="Toll Plaza",
        currency="ZMW",
        collection="imported_feeds",
        match={"feed_type": "toll_plaza", **not_trashed_clause()},
        date_field="transaction_date",
        amount_expr=_abs_field("tender_amount"),
    ),
    SeparateFeedSpec(
        key="parking_congo",
        label="Parking Congo",
        currency="USD",
        collection="imported_feeds",
        match={"feed_type": "parking_congo", **not_trashed_clause()},
        date_field="transaction_date",
        amount_expr=_abs_field("amount"),
    ),
    SeparateFeedSpec(
        key="congo_exp",
        label="Congo Expenses",
        currency="USD",
        collection="separate_expenses",
        match={"expense_type": "congo_expenses"},
        date_field="expense_date",
        amount_expr=_abs_field("amount_usd"),
    ),
    SeparateFeedSpec(
        key="ahmed_kimvi",
        label="Ahmed Kimvi",
        currency="USD",
        collection="separate_expenses",
        match={"expense_type": "ahmed_kimvi"},
        date_field="expense_date",
        amount_expr=_abs_field("amount_usd"),
    ),
    SeparateFeedSpec(
        key="zambia_parking",
        label="Zambia Parking",
        currency="ZMW",
        collection="imported_feeds",
        match={"feed_type": "zambia_parking", **not_trashed_clause()},
        date_field="transaction_date",
        amount_expr=_zambia_spend_expr(),
    ),
    SeparateFeedSpec(
        key="afritrack",
        label="Afritrack",
        currency="USD",
        collection="imported_feeds",
        match={"feed_type": "afritrack", **not_trashed_clause()},
        date_field="import_date",
        amount_expr=_abs_field("total_invoice"),
    ),
    SeparateFeedSpec(
        key="third_party",
        label="Third Party Covers",
        currency="TZS",
        collection="imported_feeds",
        match={"feed_type": "third_party", **not_trashed_clause()},
        date_field="import_date",
        amount_expr=_abs_field("total_premium"),
    ),
    SeparateFeedSpec(
        key="comesa",
        label="COMESA Covers",
        currency="TZS",
        collection="imported_feeds",
        match={"feed_type": "comesa", **not_trashed_clause()},
        date_field="import_date",
        amount_expr=_abs_field("premium"),
    ),
    SeparateFeedSpec(
        key="sm_burhani",
        label="SM Burhani",
        currency="USD",
        collection="reconciliation_entries",
        match={"entity": SM_BURHANI_ENTITY},
        date_field="t1_date",
        amount_expr=_abs_field("charge"),
    ),
    SeparateFeedSpec(
        key="rahntech",
        label="RahnTech",
        currency="USD",
        collection="imported_feeds",
        match={"feed_type": "rahntech", **not_trashed_clause()},
        date_field="transaction_date",
        amount_expr=_abs_field("amount"),
    ),
)


def normalize_period(
    year: int,
    period: str | None = None,
    month: int | None = None,
) -> tuple[PeriodKind, int | None]:
    kind = (period or "year").strip().lower()
    if kind == "month":
        m = int(month or 0)
        if not 1 <= m <= 12:
            raise ValueError("month must be 1–12 when period=month")
        return "month", m
    return "year", None


def period_window(
    year: int,
    kind: PeriodKind,
    month: int | None = None,
) -> tuple[datetime, datetime]:
    if kind == "month":
        m = int(month or 1)
        start = datetime(year, m, 1)
        last = calendar.monthrange(year, m)[1]
        end = datetime(year, m, last, 23, 59, 59)
        return start, end
    return datetime(year, 1, 1), datetime(year, 12, 31, 23, 59, 59)


def period_day_count(
    year: int,
    kind: PeriodKind,
    month: int | None = None,
    *,
    today: date | None = None,
) -> int:
    """Days to divide by for avg/day (elapsed days for the current open period)."""
    today = today or date.today()
    start, end = period_window(year, kind, month)
    start_d = start.date()
    end_d = end.date()
    if today < start_d:
        return 1
    last = min(today, end_d)
    return max(1, (last - start_d).days + 1)


def period_label(year: int, kind: PeriodKind, month: int | None = None) -> str:
    if kind == "month" and month:
        return f"{calendar.month_abbr[month]} {year}"
    return str(year)


def empty_currency_totals() -> dict[str, float]:
    return {"tzs": 0.0, "usd": 0.0, "zmw": 0.0}


def master_currency_group() -> dict[str, Any]:
    return {
        "_id": None,
        "tzs": sum_amount_if_currency("TZS", "TSH", "TZ", absolute=True),
        "usd": sum_amount_if_currency("USD", absolute=True),
        "zmw": sum_amount_if_currency("ZMW", "ZMB", "ZK", absolute=True),
        "count": {"$sum": 1},
    }


def averages_from_totals(
    totals: dict[str, float],
    days: int,
) -> dict[str, float]:
    d = max(1, int(days))
    return {
        "tzs": float(totals.get("tzs") or 0.0) / d,
        "usd": float(totals.get("usd") or 0.0) / d,
        "zmw": float(totals.get("zmw") or 0.0) / d,
    }


def feed_sum_pipeline(
    spec: SeparateFeedSpec,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    match = dict(spec.match)
    match[spec.date_field] = {"$gte": start, "$lte": end}
    return [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": spec.amount_expr},
                "count": {"$sum": 1},
            }
        },
    ]


def money_rank_key(doc: dict[str, Any]) -> float:
    return max(
        float(doc.get("tzs") or 0.0),
        float(doc.get("usd") or 0.0),
        float(doc.get("zmw") or 0.0),
    )


def collapse_top_n(
    rows: list[dict[str, Any]],
    *,
    top_n: int,
    name_key: str = "name",
) -> list[dict[str, Any]]:
    """Keep top_n by max currency, fold the rest into Other."""
    if not rows:
        return []
    ranked = sorted(rows, key=money_rank_key, reverse=True)
    if money_rank_key(ranked[0]) <= 0:
        return []
    top = ranked[:top_n]
    rest = ranked[top_n:]
    out = list(top)
    if rest:
        out.append(
            {
                name_key: "Other",
                "tzs": sum(float(d.get("tzs") or 0) for d in rest),
                "usd": sum(float(d.get("usd") or 0) for d in rest),
                "zmw": sum(float(d.get("zmw") or 0) for d in rest),
                "count": sum(int(d.get("count") or 0) for d in rest),
            }
        )
    return out


def pareto_for_currency(
    rows: list[dict[str, Any]],
    currency_key: str,
) -> dict[str, Any]:
    """Split ranked spend into Top 80% vs Bottom 20% with transaction counts."""
    ranked = sorted(
        (
            {
                "amount": float(r.get(currency_key) or 0.0),
                "count": int(r.get("count") or 0),
            }
            for r in rows
            if float(r.get(currency_key) or 0.0) > 0
        ),
        key=lambda r: r["amount"],
        reverse=True,
    )
    total = sum(r["amount"] for r in ranked)
    if total <= 0:
        empty = {"amount": 0.0, "count": 0, "share": 0.0}
        return {"top80": dict(empty), "bottom20": dict(empty), "total": 0.0}
    threshold = total * 0.8
    cum = 0.0
    top_amt = 0.0
    top_count = 0
    bot_amt = 0.0
    bot_count = 0
    for row in ranked:
        if cum < threshold:
            top_amt += row["amount"]
            top_count += row["count"]
        else:
            bot_amt += row["amount"]
            bot_count += row["count"]
        cum += row["amount"]
    return {
        "top80": {
            "amount": top_amt,
            "count": top_count,
            "share": top_amt / total if total else 0.0,
        },
        "bottom20": {
            "amount": bot_amt,
            "count": bot_count,
            "share": bot_amt / total if total else 0.0,
        },
        "total": total,
    }
