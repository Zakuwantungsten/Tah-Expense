"""Overview dashboard helpers (Phase 4.5)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from .diesel_liters import parse_liters_from_description

FUEL_LITERS_SERIES: tuple[tuple[str, str], ...] = (
    ("diesel_cash", "Diesel Cash"),
    ("infinity", "Infinity"),
    ("lake_zambia", "Lake Zambia"),
    ("lake_tunduma", "Lake Tunduma"),
    ("gbp_diesel", "GBP Diesel"),
)

DIESEL_FEED_SOURCE_GROUP = {
    "diesel_infinity": "infinity",
    "diesel_lake_zambia": "lake_zambia",
    "diesel_lake_tunduma": "lake_tunduma",
    "diesel_gbp": "gbp_diesel",
}


def empty_fuel_liters_daily() -> dict[str, dict[date, float]]:
    return {key: {} for key, _ in FUEL_LITERS_SERIES}


def add_fuel_liters_day(
    store: dict[str, dict[date, float]],
    source: str,
    when: Any,
    liters: float | None,
) -> None:
    if source not in store or liters is None:
        return
    try:
        qty = float(liters)
    except (TypeError, ValueError):
        return
    if qty <= 0:
        return
    if isinstance(when, datetime):
        day = when.date()
    elif isinstance(when, date):
        day = when
    else:
        return
    bucket = store[source]
    bucket[day] = bucket.get(day, 0.0) + qty


def accumulate_diesel_cash_fuel_liters(
    docs: Iterable[dict],
    store: dict[str, dict[date, float]],
) -> None:
    for doc in docs:
        liters = parse_liters_from_description(str(doc.get("description") or ""))
        add_fuel_liters_day(store, "diesel_cash", doc.get("date"), liters)


def serialize_fuel_liters_series(
    store: dict[str, dict[date, float]],
) -> dict[str, dict[str, float]]:
    """JSON-safe daily series (ISO date keys)."""
    out: dict[str, dict[str, float]] = {}
    for key, days in store.items():
        out[key] = {
            day.isoformat(): float(qty)
            for day, qty in sorted(days.items())
        }
    return out


def sum_amount_if_currency(*codes: str, absolute: bool = False) -> dict[str, Any]:
    amount_expr: Any = {"$abs": "$amount"} if absolute else "$amount"
    return {
        "$sum": {
            "$cond": [
                {
                    "$in": [
                        {"$toUpper": {"$ifNull": ["$currency", ""]}},
                        list(codes),
                    ]
                },
                amount_expr,
                0,
            ]
        }
    }


def safe_double(field: str) -> dict[str, Any]:
    return {
        "$convert": {
            "input": f"${field}",
            "to": "double",
            "onError": 0.0,
            "onNull": 0.0,
        }
    }


def year_window(year: int) -> tuple[datetime, datetime]:
    return datetime(year, 1, 1), datetime(year + 1, 1, 1)
