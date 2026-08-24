"""Diesel station currency helpers (Lake Zambia per-upload USD/ZMW)."""

from __future__ import annotations

from typing import Optional

LAKE_ZAMBIA_DIESEL_CURRENCIES = frozenset({"USD", "ZMW"})

_DIESEL_FEED_CURRENCY = {
    "diesel_infinity": "TZS",
    "diesel_lake_zambia": "USD",
    "diesel_lake_tunduma": "TZS",
    "diesel_gbp": "TZS",
}


def diesel_record_currency(doc: dict, feed_type: str) -> Optional[str]:
    """Display/report currency for one diesel imported_feeds row."""
    stored = str(doc.get("currency") or "").strip().upper()
    if feed_type == "diesel_lake_zambia":
        return stored if stored in LAKE_ZAMBIA_DIESEL_CURRENCIES else "USD"
    if feed_type in ("diesel_gbp", "diesel_lake_tunduma"):
        return None
    return _DIESEL_FEED_CURRENCY.get(feed_type)


def diesel_amount_by_currency_groups(groups: list) -> dict[str, float]:
    """Sum diesel amounts keyed by USD/ZMW from a Mongo $group facet."""
    totals: dict[str, float] = {}
    for row in groups:
        cur = diesel_record_currency({"currency": row.get("_id")}, "diesel_lake_zambia")
        if not cur:
            continue
        totals[cur] = totals.get(cur, 0.0) + float(row.get("amount") or 0.0)
    return totals
