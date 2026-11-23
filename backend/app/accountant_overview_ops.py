"""Overview dashboard helpers (Phase 4.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from .diesel_liters import parse_liters_from_description
from .fx_rates import convert_to_tzs

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

DIESEL_STATION_FEED_TYPES: tuple[str, ...] = tuple(DIESEL_FEED_SOURCE_GROUP.keys())


@dataclass(frozen=True)
class SourceCoreSpec:
    key: str
    label: str
    # Hint for bar end-label when a series is mostly one currency before FX.
    display_currency: str


# Master + Fuel stay as overview aggregates; every SEPARATE EXPENSES tab is its
# own Sources bar (no Congo / Toll / Insurance family folding).
SOURCE_CORE: tuple[SourceCoreSpec, ...] = (
    SourceCoreSpec("master", "Master", "TZS"),
    SourceCoreSpec("fuel", "Fuel", "TZS"),
)

# Back-compat aliases (older tests / imports).
SourceFamilySpec = SourceCoreSpec
SOURCE_FAMILIES = SOURCE_CORE
SOURCE_FAMILY_FEED_KEYS: dict[str, tuple[str, ...]] = {}


def empty_currency_bucket() -> dict[str, float]:
    return {"tzs": 0.0, "usd": 0.0, "zmw": 0.0}


def add_currency_buckets(
    *buckets: dict[str, float] | None,
) -> dict[str, float]:
    out = empty_currency_bucket()
    for bucket in buckets:
        if not bucket:
            continue
        out["tzs"] += float(bucket.get("tzs") or 0.0)
        out["usd"] += float(bucket.get("usd") or 0.0)
        out["zmw"] += float(bucket.get("zmw") or 0.0)
    return out


def subtract_currency_buckets(
    left: dict[str, float],
    right: dict[str, float],
) -> dict[str, float]:
    """left − right per currency, floored at 0 (Master without Diesel Cash)."""
    return {
        "tzs": max(0.0, float(left.get("tzs") or 0.0) - float(right.get("tzs") or 0.0)),
        "usd": max(0.0, float(left.get("usd") or 0.0) - float(right.get("usd") or 0.0)),
        "zmw": max(0.0, float(left.get("zmw") or 0.0) - float(right.get("zmw") or 0.0)),
    }


def bucket_to_tzs(bucket: dict[str, float], rates: dict[str, float]) -> float:
    return (
        convert_to_tzs(float(bucket.get("tzs") or 0.0), "TZS", rates)
        + convert_to_tzs(float(bucket.get("usd") or 0.0), "USD", rates)
        + convert_to_tzs(float(bucket.get("zmw") or 0.0), "ZMW", rates)
    )


def primary_native_currency(bucket: dict[str, float]) -> str:
    """Currency with the largest native absolute amount (for bar end labels)."""
    pairs = (
        ("TZS", abs(float(bucket.get("tzs") or 0.0))),
        ("USD", abs(float(bucket.get("usd") or 0.0))),
        ("ZMW", abs(float(bucket.get("zmw") or 0.0))),
    )
    best = max(pairs, key=lambda item: item[1])
    if best[1] <= 0:
        return "TZS"
    return best[0]


def _source_row(
    *,
    key: str,
    label: str,
    native: dict[str, float],
    rates: dict[str, float],
    display_currency: str = "TZS",
) -> dict[str, Any]:
    amount_tzs = bucket_to_tzs(native, rates)
    return {
        "key": key,
        "label": label,
        "amount_tzs": float(amount_tzs),
        "currency": "TZS",
        "native": {
            "tzs": float(native.get("tzs") or 0.0),
            "usd": float(native.get("usd") or 0.0),
            "zmw": float(native.get("zmw") or 0.0),
        },
        "native_currency": primary_native_currency(native) or display_currency,
    }


def build_source_rows(
    core_buckets: dict[str, dict[str, float]],
    rates: dict[str, float],
    *,
    separate_feeds: Sequence[dict[str, Any]] | None = None,
    rate_source: str = "live",
) -> list[dict[str, Any]]:
    """Assemble Overview Sources bars — Master, Fuel, then each separate tab.

    All amounts converted to TZS for bar length. Separate feeds are **not**
    grouped into families.
    """
    _ = rate_source
    rows: list[dict[str, Any]] = []
    for spec in SOURCE_CORE:
        native = add_currency_buckets(core_buckets.get(spec.key))
        rows.append(
            _source_row(
                key=spec.key,
                label=spec.label,
                native=native,
                rates=rates,
                display_currency=spec.display_currency,
            )
        )
    for feed in separate_feeds or ():
        key = str(feed.get("key") or "").strip()
        if not key:
            continue
        label = str(feed.get("label") or key).strip() or key
        native = empty_currency_bucket()
        accumulate_currency_amount(
            native,
            str(feed.get("currency") or ""),
            float(feed.get("amount") or 0.0),
        )
        rows.append(
            _source_row(
                key=key,
                label=label,
                native=native,
                rates=rates,
                display_currency=str(feed.get("currency") or "TZS").upper() or "TZS",
            )
        )
    rows.sort(key=lambda row: float(row.get("amount_tzs") or 0.0), reverse=True)
    return rows


def build_fuel_spend_rows(
    station_buckets: dict[str, dict[str, float]],
    rates: dict[str, float],
) -> list[dict[str, Any]]:
    """Per diesel station / Diesel Cash — native currencies converted to TZS bars."""
    rows: list[dict[str, Any]] = []
    for key, label in FUEL_LITERS_SERIES:
        native = add_currency_buckets(station_buckets.get(key))
        amount_tzs = bucket_to_tzs(native, rates)
        rows.append(
            {
                "key": key,
                "label": label,
                "amount_tzs": float(amount_tzs),
                "currency": "TZS",
                "native": {
                    "tzs": float(native.get("tzs") or 0.0),
                    "usd": float(native.get("usd") or 0.0),
                    "zmw": float(native.get("zmw") or 0.0),
                },
                "native_currency": primary_native_currency(native),
            }
        )
    rows.sort(key=lambda row: float(row.get("amount_tzs") or 0.0), reverse=True)
    return rows


def _pie_slice(
    name: str,
    bucket: dict[str, float] | None,
) -> dict[str, Any]:
    native = add_currency_buckets(bucket)
    return {
        "name": name,
        "tzs": float(native.get("tzs") or 0.0),
        "usd": float(native.get("usd") or 0.0),
        "zmw": float(native.get("zmw") or 0.0),
    }


def _pie_rank(slice_row: dict[str, Any]) -> float:
    return max(
        float(slice_row.get("tzs") or 0.0),
        float(slice_row.get("usd") or 0.0),
        float(slice_row.get("zmw") or 0.0),
    )


def build_source_pie_slices(
    *,
    master: dict[str, float] | None,
    separate_feeds: Sequence[dict[str, Any]] | None = None,
    station_buckets: dict[str, dict[str, float]] | None = None,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    """Overview donut slices: Master + each separate tab + each fuel station.

    Diesel Cash is **not** a slice — it remains inside Master totals.
    Station keys come from ``FUEL_LITERS_SERIES`` excluding ``diesel_cash``.
    """
    slices: list[dict[str, Any]] = [_pie_slice("Master Expenses", master)]

    for feed in separate_feeds or ():
        label = str(feed.get("label") or feed.get("key") or "").strip() or "Separate"
        bucket = empty_currency_bucket()
        accumulate_currency_amount(
            bucket,
            str(feed.get("currency") or ""),
            float(feed.get("amount") or 0.0),
        )
        slices.append(_pie_slice(label, bucket))

    stations = station_buckets or {}
    for key, label in FUEL_LITERS_SERIES:
        if key == "diesel_cash":
            continue
        slices.append(_pie_slice(label, stations.get(key)))

    positive = [row for row in slices if _pie_rank(row) > 0]
    if not positive:
        return []
    ranked = sorted(positive, key=_pie_rank, reverse=True)
    n = max(1, int(top_n or 8))
    top = ranked[:n]
    rest = ranked[n:]
    if not rest:
        return top
    other = empty_currency_bucket()
    for row in rest:
        other = add_currency_buckets(other, row)
    return [*top, _pie_slice("Other", other)]


def accumulate_currency_amount(
    bucket: dict[str, float],
    currency: str | None,
    amount: float,
) -> None:
    """Add ``amount`` into tzs/usd/zmw on ``bucket`` (mutates)."""
    cur = str(currency or "").strip().upper()
    amt = abs(float(amount or 0.0))
    if cur in {"", "TZS", "TSH", "TZ"}:
        bucket["tzs"] += amt
    elif cur == "USD":
        bucket["usd"] += amt
    elif cur in {"ZMW", "ZMB", "ZK"}:
        bucket["zmw"] += amt
    else:
        bucket["tzs"] += amt


def feed_amounts_to_bucket(
    feeds: Sequence[dict[str, Any]],
    keys: Iterable[str],
) -> dict[str, float]:
    """Fold separate-feed rows `{key, currency, amount}` into a currency bucket."""
    wanted = set(keys)
    out = empty_currency_bucket()
    for feed in feeds:
        if str(feed.get("key") or "") not in wanted:
            continue
        cur = str(feed.get("currency") or "").upper()
        amt = abs(float(feed.get("amount") or 0.0))
        if cur in {"TZS", "TSH", "TZ"}:
            out["tzs"] += amt
        elif cur == "USD":
            out["usd"] += amt
        elif cur in {"ZMW", "ZMB", "ZK"}:
            out["zmw"] += amt
    return out


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
