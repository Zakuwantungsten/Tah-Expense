"""Trends deep-dive helpers — source catalog, analyzers, Phase 9 polish."""

from __future__ import annotations

import re
from calendar import month_abbr, monthrange
from dataclasses import dataclass
from datetime import date
from typing import Any

from .accountant_analytics_ops import SEPARATE_EXPENSE_FEEDS, empty_currency_totals

# Sidebar-aligned monetary sources. ``ready`` = Phase that ships live analysis.
TREND_SOURCE_PHASE: dict[str, int] = {
    "master": 1,
    # ITEMS are dynamic (phase 2) — listed via categories API with phase=2
    "diesel_cash": 4,
    "infinity": 3,
    "lake_zambia": 3,
    "lake_tunduma": 3,
    "gbp_diesel": 3,
    "toll_plaza": 5,
    "parking_congo": 5,
    "zambia_parking": 5,
    "rahntech": 5,
    "congo_exp": 6,
    "ahmed_kimvi": 6,
    "comesa": 7,
    "third_party": 7,
    "afritrack": 8,
    "sm_burhani": 8,
}

FUEL_TREND_SOURCES: tuple[tuple[str, str], ...] = (
    ("diesel_cash", "Diesel Cash"),
    ("infinity", "Infinity"),
    ("lake_zambia", "Lake Zambia"),
    ("lake_tunduma", "Lake Tunduma"),
    ("gbp_diesel", "GBP Diesel"),
)

# Phase 3 — imported diesel stations (shared ltrs × rate schema).
DIESEL_STATION_TRENDS: tuple[tuple[str, str, str, str], ...] = (
    # sidebar_key, label, feed_type, native_currency
    ("infinity", "Infinity", "diesel_infinity", "TZS"),
    ("lake_zambia", "Lake Zambia", "diesel_lake_zambia", "USD"),
    ("lake_tunduma", "Lake Tunduma", "diesel_lake_tunduma", "TZS"),
    ("gbp_diesel", "GBP Diesel", "diesel_gbp", "TZS"),
)

DIESEL_STATION_BY_KEY: dict[str, tuple[str, str, str]] = {
    key: (label, feed_type, currency)
    for key, label, feed_type, currency in DIESEL_STATION_TRENDS
}

PHASE3_DIESEL_KEYS = frozenset(DIESEL_STATION_BY_KEY.keys())

# Phase 4 — Diesel Cash (Master transactions; litres from description).
PHASE4_DIESEL_CASH_KEY = "diesel_cash"
PHASE4_DIESEL_CASH_KEYS = frozenset({PHASE4_DIESEL_CASH_KEY})
READY_FUEL_TREND_KEYS = PHASE3_DIESEL_KEYS | PHASE4_DIESEL_CASH_KEYS


@dataclass(frozen=True)
class SeparateTrendDims:
    """Signature chart fields for one Phase-5+ separate feed."""

    mix_field: str
    mix_label: str
    truck_field: str
    truck_label: str
    slice_field: str
    slice_label: str
    exclude_deposits: bool = False


# Phase 5 — gate / plate imports (each feed keeps its own dims).
PHASE5_SEPARATE_DIMS: dict[str, SeparateTrendDims] = {
    "toll_plaza": SeparateTrendDims(
        mix_field="toll_plaza",
        mix_label="Spend by Plaza",
        truck_field="vehicle_reg",
        truck_label="Spend by Truck",
        slice_field="cashier_name",
        slice_label="By Cashier",
    ),
    "parking_congo": SeparateTrendDims(
        mix_field="gate_in",
        mix_label="Spend by Gate",
        truck_field="vehicle_no",
        truck_label="Spend by Vehicle",
        slice_field="direction",
        slice_label="By Direction",
        exclude_deposits=True,
    ),
    "zambia_parking": SeparateTrendDims(
        mix_field="heading_to",
        mix_label="Spend by Heading",
        truck_field="plate_num",
        truck_label="Spend by Plate",
        slice_field="transaction_type",
        slice_label="By Type",
    ),
    "rahntech": SeparateTrendDims(
        mix_field="device_number",
        mix_label="Spend by Device",
        truck_field="truck_number",
        truck_label="Spend by Truck",
        slice_field="trip_number",
        slice_label="By Trip",
    ),
}

PHASE5_SEPARATE_KEYS = frozenset(PHASE5_SEPARATE_DIMS.keys())

# Phase 6 — signed USD ledgers (same schema: Congo Expenses · Ahmed Kimvi).
PHASE6_USD_LEDGER_KEYS = frozenset({"congo_exp", "ahmed_kimvi"})
PHASE6_USD_LEDGER_DIMS = SeparateTrendDims(
    mix_field="description",
    mix_label="Called Out by Description",
    truck_field="truck_no",
    truck_label="Called Out by Truck",
    slice_field="",  # synthetic Money In vs Called Out
    slice_label="Money In vs Called Out",
)

# Phase 7 — insurance pair (TZS premiums; cover-month string field).
PHASE7_INSURANCE_DIMS: dict[str, SeparateTrendDims] = {
    "comesa": SeparateTrendDims(
        mix_field="card_no",
        mix_label="Premium by Card",
        truck_field="truck_reg",
        truck_label="Premium by Truck",
        slice_field="month",
        slice_label="By Cover Month",
    ),
    "third_party": SeparateTrendDims(
        mix_field="status",
        mix_label="Premium by Status",
        truck_field="reg_no",
        truck_label="Premium by Truck",
        slice_field="month",
        slice_label="By Cover Month",
    ),
}
PHASE7_INSURANCE_KEYS = frozenset(PHASE7_INSURANCE_DIMS.keys())

# Phase 8 — outliers (unique schemas; still fit the separate chart shell).
PHASE8_OUTLIER_DIMS: dict[str, SeparateTrendDims] = {
    "afritrack": SeparateTrendDims(
        mix_field="period",
        mix_label="Invoice by Period",
        truck_field="truck",
        truck_label="Invoice by Truck",
        slice_field="variance",  # replaced by signed buckets in API
        slice_label="By Variance",
    ),
    "sm_burhani": SeparateTrendDims(
        mix_field="table",
        mix_label="Charge by Table",
        truck_field="station",
        truck_label="Charge by Station",
        slice_field="confirmed",
        slice_label="By Confirmation",
    ),
}
PHASE8_OUTLIER_KEYS = frozenset(PHASE8_OUTLIER_DIMS.keys())

READY_SEPARATE_TREND_KEYS = (
    PHASE5_SEPARATE_KEYS
    | PHASE6_USD_LEDGER_KEYS
    | PHASE7_INSURANCE_KEYS
    | PHASE8_OUTLIER_KEYS
)

SEPARATE_FEED_BY_KEY = {spec.key: spec for spec in SEPARATE_EXPENSE_FEEDS}


def currency_bucket_key(currency: str) -> str:
    code = (currency or "TZS").strip().upper()
    if code in ("ZMW", "ZMB", "ZK"):
        return "zmw"
    if code == "USD":
        return "usd"
    return "tzs"


def native_money(currency: str, amount: float) -> dict[str, float]:
    out = empty_currency_totals()
    out[currency_bucket_key(currency)] = float(amount or 0.0)
    return out


def static_trend_source_catalog() -> list[dict[str, Any]]:
    """Picker groups for Trends (ITEMS filled by API from categories)."""
    groups: list[dict[str, Any]] = [
        {
            "group": "Master",
            "sources": [
                {
                    "key": "master",
                    "label": "Master Expenses",
                    "phase": 1,
                    "ready": True,
                    "kind": "master",
                }
            ],
        },
        {
            "group": "Items",
            "sources": [],  # filled dynamically
        },
        {
            "group": "Separate Expenses",
            "sources": [
                {
                    "key": spec.key,
                    "label": spec.label,
                    "phase": TREND_SOURCE_PHASE.get(spec.key, 9),
                    "ready": spec.key in READY_SEPARATE_TREND_KEYS,
                    "kind": (
                        "ledger"
                        if spec.key in PHASE6_USD_LEDGER_KEYS
                        else (
                            "insurance"
                            if spec.key in PHASE7_INSURANCE_KEYS
                            else (
                                "outlier"
                                if spec.key in PHASE8_OUTLIER_KEYS
                                else "separate"
                            )
                        )
                    ),
                    "currency": spec.currency,
                }
                for spec in SEPARATE_EXPENSE_FEEDS
            ],
        },
        {
            "group": "Fuel",
            "sources": [
                {
                    "key": key,
                    "label": label,
                    "phase": TREND_SOURCE_PHASE.get(key, 9),
                    "ready": key in READY_FUEL_TREND_KEYS,
                    "kind": "fuel",
                    "currency": (
                        DIESEL_STATION_BY_KEY[key][2]
                        if key in DIESEL_STATION_BY_KEY
                        else ("TZS" if key == PHASE4_DIESEL_CASH_KEY else None)
                    ),
                }
                for key, label in FUEL_TREND_SOURCES
            ],
        },
    ]
    return groups


def money_triple(doc: dict[str, Any]) -> dict[str, float]:
    return {
        "tzs": float(doc.get("tzs") or 0.0),
        "usd": float(doc.get("usd") or 0.0),
        "zmw": float(doc.get("zmw") or 0.0),
    }


def month_deltas(
    current: list[dict[str, Any]],
    prior: list[dict[str, Any]],
    *,
    currency_key: str,
) -> list[dict[str, Any]]:
    """YoY change per month for one currency (increase / decrease callouts)."""
    prior_map = {
        int(r.get("month") or 0): float(r.get(currency_key) or 0.0)
        for r in prior
    }
    out: list[dict[str, Any]] = []
    for row in current:
        month = int(row.get("month") or 0)
        if not 1 <= month <= 12:
            continue
        cur = float(row.get(currency_key) or 0.0)
        prv = float(prior_map.get(month) or 0.0)
        delta = cur - prv
        pct = (delta / prv) if prv else (1.0 if cur > 0 else 0.0)
        out.append(
            {
                "month": month,
                "current": cur,
                "prior": prv,
                "delta": delta,
                "pct": pct,
                "direction": (
                    "up" if delta > 0 else ("down" if delta < 0 else "flat")
                ),
            }
        )
    return out


def mom_deltas(
    current: list[dict[str, Any]],
    *,
    currency_key: str,
) -> list[dict[str, Any]]:
    """Month-over-month change within the selected year."""
    by_month = {
        int(r.get("month") or 0): float(r.get(currency_key) or 0.0)
        for r in current
        if 1 <= int(r.get("month") or 0) <= 12
    }
    out: list[dict[str, Any]] = []
    for month in range(1, 13):
        cur = by_month.get(month, 0.0)
        prv = by_month.get(month - 1, 0.0) if month > 1 else 0.0
        if month == 1:
            out.append(
                {
                    "month": month,
                    "current": cur,
                    "prior": 0.0,
                    "delta": 0.0,
                    "pct": 0.0,
                    "direction": "flat",
                }
            )
            continue
        delta = cur - prv
        pct = (delta / prv) if prv else (1.0 if cur > 0 else 0.0)
        out.append(
            {
                "month": month,
                "current": cur,
                "prior": prv,
                "delta": delta,
                "pct": pct,
                "direction": (
                    "up" if delta > 0 else ("down" if delta < 0 else "flat")
                ),
            }
        )
    return out


def empty_receipt_breakdown() -> dict[str, int]:
    return {"received": 0, "pending": 0, "missing": 0, "total": 0}


def empty_master_trend_payload() -> dict[str, Any]:
    return {
        "totals": empty_currency_totals(),
        "prior_totals": empty_currency_totals(),
        "count": 0,
        "categories": [],
        "trucks": [],
        "receipts": empty_receipt_breakdown(),
        "month_trend": [],
        "prior_month_trend": [],
        "deltas": {"yoy": {"tzs": [], "usd": [], "zmw": []}, "mom": {"tzs": [], "usd": [], "zmw": []}},
    }


def item_key_from_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def parse_item_source_key(source: str) -> str | None:
    """Return slug after ``item:`` or None if not an item source key."""
    key = (source or "").strip().lower()
    if not key.startswith("item:"):
        return None
    slug = key[5:].strip("_")
    return slug or None


def category_name_clause(category_name: str) -> dict[str, Any]:
    """Case-insensitive match on category_name or item (Master ITEMS rows)."""
    name = (category_name or "").strip()
    if not name:
        return {}
    esc = re.escape(name)
    rx = {"$regex": f"^{esc}$", "$options": "i"}
    return {"$or": [{"category_name": rx}, {"item": rx}]}


def category_names_clause(names: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """OR match across several category/item names (Entities multi-select)."""
    cleaned = []
    seen: set[str] = set()
    for raw in names or ():
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    if not cleaned:
        return {}
    if len(cleaned) == 1:
        return category_name_clause(cleaned[0])
    alt = "|".join(re.escape(n) for n in cleaned)
    rx = {"$regex": f"^(?:{alt})$", "$options": "i"}
    return {"$or": [{"category_name": rx}, {"item": rx}]}


def payee_clause(payee: str) -> dict[str, Any]:
    """Case-insensitive exact match on transaction payee."""
    name = (payee or "").strip()
    if not name:
        return {}
    esc = re.escape(name)
    return {"payee": {"$regex": f"^{esc}$", "$options": "i"}}


def truck_number_clause(truck: str) -> dict[str, Any]:
    """Space-insensitive Master ``truck_number`` match (plate ± trailer)."""
    from .accountant_diesel_ops import truck_exact_clause

    name = (truck or "").strip()
    if not name or name in {"—", "-"}:
        return {}
    return {"truck_number": truck_exact_clause(name)}


def payees_clause(payees: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """OR match across several payee names (Entities multi-select)."""
    cleaned = []
    seen: set[str] = set()
    for raw in payees or ():
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    if not cleaned:
        return {}
    if len(cleaned) == 1:
        return payee_clause(cleaned[0])
    alt = "|".join(re.escape(n) for n in cleaned)
    return {"payee": {"$regex": f"^(?:{alt})$", "$options": "i"}}


def entity_set_label(
    family: str,
    labels: list[str] | tuple[str, ...],
) -> str:
    """Human label for one or many selected Entities."""
    names = [str(n).strip() for n in (labels or ()) if str(n).strip()]
    if not names:
        fam = (family or "").strip().lower()
        return "Entities" if fam in {"", "mixed", "entities"} else family.title()
    if len(names) == 1:
        return names[0]
    noun = {
        "items": "items",
        "payees": "payees",
        "suppliers": "suppliers",
        "mixed": "entities",
        "entities": "entities",
    }.get((family or "").strip().lower(), "entities")
    if len(names) == 2:
        return f"{names[0]} + {names[1]}"
    return f"{len(names)} {noun} · {names[0]} +"


# Entities (earth tab) — Master-side deep-dive families.
ENTITY_FAMILIES: tuple[tuple[str, str], ...] = (
    ("items", "Items"),
    ("payees", "Payees"),
    ("suppliers", "Suppliers"),
)
ENTITY_FAMILY_KEYS = frozenset(k for k, _ in ENTITY_FAMILIES)


# Phase 9 — display polish helpers (sparse / human labels).
_SM_BURHANI_TABLE_LABELS = {
    "bonds": "Bonds",
    "rpa_schedule": "RPA Schedule",
    "rpa": "RPA Schedule",
}


def humanize_dimension_name(source_key: str, field: str, raw: Any) -> str:
    """Friendly labels for outlier/insurance dimension buckets."""
    name = str(raw or "").strip() or "—"
    if source_key == "sm_burhani":
        if field == "table":
            return _SM_BURHANI_TABLE_LABELS.get(name.lower(), name)
        if field == "confirmed":
            low = name.lower()
            if low in ("true", "1", "yes"):
                return "Confirmed"
            if low in ("false", "0", "no"):
                return "Unconfirmed"
    return name


def polish_dimension_rows(
    source_key: str,
    field: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["name"] = humanize_dimension_name(
            source_key, field, row.get("name")
        )
        out.append(item)
    return out


def is_sparse_trends_payload(payload: dict[str, Any]) -> bool:
    """True when a ready source has no activity in the selected window."""
    if not bool(payload.get("ready")):
        return False
    kpis = payload.get("kpis") or {}
    count = int(kpis.get("count") or 0)
    if count > 0:
        return False
    totals = kpis.get("totals") or {}
    return not any(float(totals.get(k) or 0.0) for k in ("tzs", "usd", "zmw"))


def liters_from_description_docs(docs: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> float:
    """Sum litres parsed from transaction ``description`` fields."""
    from .diesel_liters import parse_liters_from_description

    total = 0.0
    for doc in docs:
        qty = parse_liters_from_description(str(doc.get("description") or ""))
        if qty:
            total += float(qty)
    return total


def liters_by_month_from_docs(
    docs: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    year: int,
) -> dict[int, float]:
    """Bucket parsed litres by calendar month for ``year``."""
    from datetime import date, datetime

    from .diesel_liters import parse_liters_from_description

    out: dict[int, float] = {m: 0.0 for m in range(1, 13)}
    for doc in docs:
        when = doc.get("date")
        if isinstance(when, datetime):
            day = when.date()
        elif isinstance(when, date):
            day = when
        else:
            continue
        if day.year != year:
            continue
        qty = parse_liters_from_description(str(doc.get("description") or ""))
        if not qty:
            continue
        out[day.month] = out.get(day.month, 0.0) + float(qty)
    return out


def merge_liters_into_month_series(
    series: list[dict[str, Any]],
    liters_by_month: dict[int, float],
) -> list[dict[str, Any]]:
    """Attach ``liters`` onto each month row (mutates copies)."""
    return [
        {
            **row,
            "liters": float(liters_by_month.get(int(row.get("month") or 0), 0.0)),
        }
        for row in series
    ]


# Entities / Trends compare palette + shared in-process TTL cache.
ENTITY_COMPARE_MAX = 4
TREND_COMPARE_MAX = ENTITY_COMPARE_MAX
ENTITY_COMPARE_COLORS: tuple[str, ...] = (
    "#0077C5",
    "#F59E0B",
    "#10B981",
    "#DC2626",
)
TREND_COMPARE_COLORS = ENTITY_COMPARE_COLORS

_ENTITY_CACHE: dict[str, tuple[float, Any]] = {}
_ENTITY_CACHE_TTL_SEC = 45.0
_ENTITY_CACHE_MAX = 64


def entity_cache_key(*parts: Any) -> str:
    """Stable cache key from simple scalar parts."""
    return "|".join("" if p is None else str(p) for p in parts)


def entity_cache_get(key: str) -> Any | None:
    """Return a shallow copy of a cached payload, or None if missing/expired."""
    import time

    row = _ENTITY_CACHE.get(key)
    if not row:
        return None
    mono, payload = row
    if (time.monotonic() - mono) >= _ENTITY_CACHE_TTL_SEC:
        _ENTITY_CACHE.pop(key, None)
        return None
    if isinstance(payload, dict):
        return dict(payload)
    return payload


def entity_cache_set(key: str, payload: Any) -> None:
    """Store payload under key; drop oldest entries when over max size."""
    import time

    if len(_ENTITY_CACHE) >= _ENTITY_CACHE_MAX and key not in _ENTITY_CACHE:
        # Drop roughly oldest half by insertion order (Py3.7+ dict order).
        drop = max(1, len(_ENTITY_CACHE) // 4)
        for old in list(_ENTITY_CACHE.keys())[:drop]:
            _ENTITY_CACHE.pop(old, None)
    stored = dict(payload) if isinstance(payload, dict) else payload
    _ENTITY_CACHE[key] = (time.monotonic(), stored)


def entity_cache_clear() -> None:
    _ENTITY_CACHE.clear()


def build_data_table_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble Analytics Data table payload (one row per source + totals).

    Expects rows shaped ``{key, label, group, tzs, usd, zmw, count}``.
    Does not invent Diesel Cash — callers must omit it (covered by Master).
    """
    cleaned: list[dict[str, Any]] = []
    for raw in rows or []:
        key = str(raw.get("key") or "").strip()
        if not key or key == "diesel_cash":
            continue
        cleaned.append(
            {
                "key": key,
                "label": str(raw.get("label") or key).strip() or key,
                "group": str(raw.get("group") or "").strip() or "Other",
                "tzs": float(raw.get("tzs") or 0.0),
                "usd": float(raw.get("usd") or 0.0),
                "zmw": float(raw.get("zmw") or 0.0),
                "count": int(raw.get("count") or 0),
            }
        )
    totals = {
        "tzs": sum(float(r["tzs"]) for r in cleaned),
        "usd": sum(float(r["usd"]) for r in cleaned),
        "zmw": sum(float(r["zmw"]) for r in cleaned),
        "count": sum(int(r["count"]) for r in cleaned),
    }
    return {
        "sources": cleaned,
        "totals": totals,
        "source_count": len(cleaned),
    }


def iter_year_months(start_d: date, end_d: date) -> list[tuple[int, int]]:
    """Calendar (year, month) pairs that intersect inclusive ``start_d``–``end_d``."""
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    out: list[tuple[int, int]] = []
    y, m = start_d.year, start_d.month
    while (y, m) <= (end_d.year, end_d.month):
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def month_intersects_range(year: int, month: int, start_d: date, end_d: date) -> bool:
    """True when calendar month overlaps the inclusive date window."""
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    return first <= end_d and last >= start_d


def month_trend_label(year: int, month: int, *, multi_year: bool) -> str:
    """Short axis label; include year when the From–To span crosses years."""
    abbr = month_abbr[month] if 1 <= month <= 12 else "—"
    if multi_year:
        return f"{abbr} '{year % 100:02d}"
    return abbr


def build_data_table_month_trend(
    buckets: list[dict[str, Any]],
    start_d: date,
    end_d: date,
) -> list[dict[str, Any]]:
    """Merge per-source ``{year, month, tzs, usd, zmw}`` into ordered month_trend.

    Emits one row per calendar month intersecting From–To (zeros allowed).
    """
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    months = iter_year_months(start_d, end_d)
    if not months:
        return []
    multi_year = start_d.year != end_d.year
    acc: dict[tuple[int, int], dict[str, float]] = {
        key: {"tzs": 0.0, "usd": 0.0, "zmw": 0.0} for key in months
    }
    for raw in buckets or []:
        try:
            year = int(raw.get("year"))
            month = int(raw.get("month"))
        except (TypeError, ValueError):
            continue
        key = (year, month)
        if key not in acc:
            continue
        acc[key]["tzs"] += float(raw.get("tzs") or 0.0)
        acc[key]["usd"] += float(raw.get("usd") or 0.0)
        acc[key]["zmw"] += float(raw.get("zmw") or 0.0)
    return [
        {
            "label": month_trend_label(y, m, multi_year=multi_year),
            "year": y,
            "month": m,
            "tzs": acc[(y, m)]["tzs"],
            "usd": acc[(y, m)]["usd"],
            "zmw": acc[(y, m)]["zmw"],
        }
        for y, m in months
    ]


# ── Analytics → Meetings (Master ownership / approver) ───────────────────────

MEETINGS_DIMENSIONS = ("approver", "ownership")
MEETINGS_DIMENSION_LABELS: dict[str, str] = {
    "approver": "App by",
    "ownership": "Ownership",
}
MEETINGS_CHART_TOP_N = 8
_MEETINGS_NO_RECEIPT = frozenset(
    {"no_receipt", "no", "n/a", "none", "waived"}
)


def normalize_meetings_dimension(dimension: str | None) -> str:
    key = (dimension or "approver").strip().lower()
    return key if key in MEETINGS_DIMENSIONS else "approver"


def meetings_person_label(raw: Any) -> str:
    name = str(raw or "").strip()
    return name if name else "Unassigned"


def receipt_needed_and_received(status: Any) -> tuple[bool, bool]:
    """Return ``(needed, received)`` for compliance math.

    ``no_receipt`` (and aliases) are excluded from the denominator.
    """
    s = str(status or "pending").strip().lower().replace(" ", "_")
    if s in _MEETINGS_NO_RECEIPT:
        return False, False
    if s == "received" or "received" in s or s in {"rcvd", "have_receipt"}:
        return True, True
    return True, False


def compliance_pct(received: int, needed: int) -> float | None:
    """Collected ÷ needed; ``None`` when nothing was required."""
    need = int(needed or 0)
    if need <= 0:
        return None
    return max(0.0, min(1.0, float(received or 0) / float(need)))


def build_meetings_people_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble Meetings table rows sorted by TZS desc with S/N + compliance."""
    cleaned: list[dict[str, Any]] = []
    for raw in rows or []:
        name = meetings_person_label(raw.get("name") or raw.get("key"))
        needed = int(raw.get("receipts_needed") or 0)
        received = int(raw.get("receipts_received") or 0)
        pct = compliance_pct(received, needed)
        cleaned.append(
            {
                "key": name.casefold(),
                "name": name,
                "count": int(raw.get("count") or 0),
                "tzs": float(raw.get("tzs") or 0.0),
                "usd": float(raw.get("usd") or 0.0),
                "zmw": float(raw.get("zmw") or 0.0),
                "receipts_needed": needed,
                "receipts_received": received,
                "compliance_pct": pct,
            }
        )
    cleaned.sort(
        key=lambda r: (-float(r["tzs"]), -float(r["usd"]), r["name"].casefold())
    )
    for i, row in enumerate(cleaned, start=1):
        row["sn"] = i
    totals = {
        "tzs": sum(float(r["tzs"]) for r in cleaned),
        "usd": sum(float(r["usd"]) for r in cleaned),
        "zmw": sum(float(r["zmw"]) for r in cleaned),
        "count": sum(int(r["count"]) for r in cleaned),
        "receipts_needed": sum(int(r["receipts_needed"]) for r in cleaned),
        "receipts_received": sum(int(r["receipts_received"]) for r in cleaned),
    }
    totals["compliance_pct"] = compliance_pct(
        int(totals["receipts_received"]), int(totals["receipts_needed"])
    )
    return {
        "people": cleaned,
        "totals": totals,
        "person_count": len(cleaned),
        "chart_people": [r["name"] for r in cleaned[:MEETINGS_CHART_TOP_N]],
    }


def build_meetings_month_trend(
    buckets: list[dict[str, Any]],
    *,
    chart_people: list[str],
    chart_year: int,
) -> list[dict[str, Any]]:
    """Jan–Dec rows with per-person TZS series for Spend Trend lines.

    Each bucket: ``{year, month, name, tzs}``. Only ``chart_people`` appear.
    """
    names = [meetings_person_label(n) for n in (chart_people or [])]
    # Preserve order, drop empties/dupes
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    months = iter_year_months(date(chart_year, 1, 1), date(chart_year, 12, 31))
    acc: dict[tuple[int, int], dict[str, float]] = {
        (y, m): {n: 0.0 for n in ordered} for y, m in months
    }
    name_by_key = {n.casefold(): n for n in ordered}
    for raw in buckets or []:
        try:
            year = int(raw.get("year"))
            month = int(raw.get("month"))
        except (TypeError, ValueError):
            continue
        slot = acc.get((year, month))
        if slot is None:
            continue
        label = meetings_person_label(raw.get("name") or raw.get("key"))
        canon = name_by_key.get(label.casefold())
        if not canon:
            continue
        slot[canon] += float(raw.get("tzs") or 0.0)
    return [
        {
            "label": month_trend_label(y, m, multi_year=False),
            "year": y,
            "month": m,
            "series": {n: float(acc[(y, m)].get(n) or 0.0) for n in ordered},
        }
        for y, m in months
    ]


def _truck_norm_key(truck: str) -> str:
    """Space-insensitive plate key for matching current vs prior fleet rows."""
    return re.sub(r"\s+", "", (truck or "").strip()).casefold()


def _fleet_mom_fields(current: float, prior: float) -> dict[str, Any]:
    """Period-over-period change for one truck (TZS primary)."""
    cur = float(current or 0.0)
    prv = float(prior or 0.0)
    delta = cur - prv
    if abs(delta) < 1e-9:
        return {"mom_pct": 0.0, "mom_delta": 0.0, "mom_direction": "flat"}
    pct = (delta / prv) if abs(prv) > 1e-9 else (1.0 if cur > 0 else -1.0)
    return {
        "mom_pct": pct,
        "mom_delta": delta,
        "mom_direction": "up" if delta > 0 else "down",
    }


# Fleet registry kinds used by Analytics → Trucks filters.
FLEET_VEHICLE_KINDS = ("truck", "trailer", "motor_vehicle")
FLEET_VEHICLE_KIND_LABELS: dict[str, str] = {
    "all": "All",
    "truck": "Trucks",
    "trailer": "Trailers",
    "motor_vehicle": "Motorcycles & Cars",
    "unknown": "Unregistered",
}


def resolve_fleet_vehicle_kind(
    truck: str,
    kind_lookup: dict[str, str] | None = None,
) -> str:
    """Map a plate to ``truck`` / ``trailer`` / ``motor_vehicle`` / ``unknown``."""
    key = _truck_norm_key(truck)
    if not key or not kind_lookup:
        return "unknown"
    kind = str(kind_lookup.get(key) or "").strip().lower()
    if kind in FLEET_VEHICLE_KINDS:
        return kind
    return "unknown"


def filter_truck_fleet_rows(
    rows: list[dict[str, Any]],
    *,
    q: str = "",
    fleet_kind: str = "all",
) -> list[dict[str, Any]]:
    """Client/server shared filter: plate search + registry kind."""
    needle = _truck_norm_key(q)
    kind = (fleet_kind or "all").strip().lower()
    if kind not in {"all", *FLEET_VEHICLE_KINDS, "unknown"}:
        kind = "all"
    out: list[dict[str, Any]] = []
    for raw in rows or []:
        row_kind = str(raw.get("vehicle_kind") or "unknown").strip().lower()
        if kind != "all" and row_kind != kind:
            continue
        plate = str(raw.get("truck") or raw.get("name") or "")
        if needle and needle not in _truck_norm_key(plate):
            continue
        out.append(raw)
    return out


def attach_fleet_sn_and_share(
    rows: list[dict[str, Any]],
    *,
    share_basis: str | None = None,
) -> dict[str, Any]:
    """Renumber SN 1..n and recompute share/totals for a (filtered) row set."""
    cleaned = list(rows or [])
    totals = {
        "tzs": sum(float(r.get("tzs") or 0.0) for r in cleaned),
        "usd": sum(float(r.get("usd") or 0.0) for r in cleaned),
        "zmw": sum(float(r.get("zmw") or 0.0) for r in cleaned),
        "count": sum(int(r.get("count") or 0) for r in cleaned),
    }
    if share_basis in {"tzs", "usd", "zmw"}:
        basis = share_basis
    elif float(totals["tzs"]) > 0:
        basis = "tzs"
    elif float(totals["usd"]) > 0:
        basis = "usd"
    elif float(totals["zmw"]) > 0:
        basis = "zmw"
    else:
        basis = "tzs"
    share_total = float(totals[basis]) or 0.0
    out_rows: list[dict[str, Any]] = []
    for i, row in enumerate(cleaned, start=1):
        amt = float(row.get(basis) or 0.0)
        share = (amt / share_total) if share_total > 0 else 0.0
        out_rows.append({**row, "sn": i, "share": share})
    return {
        "trucks": out_rows,
        "totals": totals,
        "truck_count": len(out_rows),
        "share_basis": basis,
    }


def build_truck_fleet_table(
    rows: list[dict[str, Any]],
    *,
    prior_rows: list[dict[str, Any]] | None = None,
    kind_lookup: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Sort fleet rows by TZS, attach share % + MoM + kind + SN, emit totals.

    Share uses TZS when the fleet has TZS spend; otherwise falls back to USD,
    then ZMW, so all-USD fleets still get meaningful share percentages.
    """
    cleaned: list[dict[str, Any]] = []
    blank_plates = {"", "—", "-", "–", "Unknown", "N/A", "n/a", "none", "null"}
    for raw in rows or []:
        truck = str(raw.get("truck") or raw.get("name") or "").strip()
        # Blank / placeholder plates are Master rows with no truck_number — not
        # deep-diveable (detail API 422s) and must not lead the fleet table.
        if not truck or truck in blank_plates or not _truck_norm_key(truck):
            continue
        tzs = float(raw.get("tzs") or 0.0)
        usd = float(raw.get("usd") or 0.0)
        zmw = float(raw.get("zmw") or 0.0)
        count = int(raw.get("count") or 0)
        if tzs <= 0 and usd <= 0 and zmw <= 0 and count <= 0:
            continue
        cleaned.append(
            {
                "truck": truck,
                "tzs": tzs,
                "usd": usd,
                "zmw": zmw,
                "count": count,
                "vehicle_kind": resolve_fleet_vehicle_kind(truck, kind_lookup),
            }
        )
    cleaned.sort(
        key=lambda r: (
            float(r.get("tzs") or 0.0),
            float(r.get("usd") or 0.0),
            float(r.get("zmw") or 0.0),
        ),
        reverse=True,
    )

    prior_by_key: dict[str, float] = {}
    for raw in prior_rows or []:
        truck = str(raw.get("truck") or raw.get("name") or "").strip() or "—"
        key = _truck_norm_key(truck)
        if not key or truck in {"—", "-"}:
            continue
        prior_by_key[key] = prior_by_key.get(key, 0.0) + float(raw.get("tzs") or 0.0)

    with_mom: list[dict[str, Any]] = []
    for row in cleaned:
        prior_tzs = prior_by_key.get(_truck_norm_key(str(row["truck"])), 0.0)
        mom = _fleet_mom_fields(float(row["tzs"]), prior_tzs)
        with_mom.append({**row, **mom})
    return attach_fleet_sn_and_share(with_mom)
