"""Pure aggregations for cashier Overview charts (items donut + payee trend)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable


_CURRENCY_BUCKETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("tzs", frozenset({"TZS", "TSH", "TZ", ""})),
    ("usd", frozenset({"USD"})),
    ("zmw", frozenset({"ZMW", "ZMB", "ZK"})),
)

_UNASSIGNED_PAYEE = "Unassigned"
# Pool large enough for client period re-rank (Month/3m/Year).
_DEFAULT_PAYEE_POOL = 100


def item_label(doc: dict[str, Any]) -> str:
    raw = (doc.get("item") or doc.get("category_name") or "").strip()
    return raw or "Uncategorised"


def payee_label(doc: dict[str, Any]) -> str:
    raw = (doc.get("payee") or "").strip()
    return raw or _UNASSIGNED_PAYEE


def _day_key(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "T" in text:
            text = text.split("T", 1)[0]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


def _currency_key(doc: dict[str, Any]) -> str | None:
    cur = str(doc.get("currency") or "").strip().upper()
    for key, codes in _CURRENCY_BUCKETS:
        if cur in codes:
            return key
    return None


def _add_money(bucket: dict[str, float], doc: dict[str, Any]) -> None:
    cur_key = _currency_key(doc)
    amt = abs(float(doc.get("amount") or 0.0))
    if amt <= 0 or cur_key is None:
        return
    bucket[cur_key] = float(bucket.get(cur_key) or 0.0) + amt


def _empty_money() -> dict[str, float]:
    return {"tzs": 0.0, "usd": 0.0, "zmw": 0.0}


def _empty_daily() -> dict[str, float]:
    return {"tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0.0}


def _rank_key(row: dict[str, float]) -> float:
    return max(
        float(row.get("tzs") or 0.0),
        float(row.get("usd") or 0.0),
        float(row.get("zmw") or 0.0),
    )


def _top_breakdown(
    totals: dict[str, dict[str, float]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    ranked = sorted(
        totals.items(),
        key=lambda pair: _rank_key(pair[1]),
        reverse=True,
    )
    ranked = [(name, money) for name, money in ranked if _rank_key(money) > 0]
    if not ranked:
        return []
    top = ranked[: max(1, int(top_n))]
    rest = ranked[max(1, int(top_n)) :]
    slices: list[dict[str, Any]] = []
    for name, money in top:
        slices.append(
            {
                "name": name,
                "tzs": float(money.get("tzs") or 0.0),
                "usd": float(money.get("usd") or 0.0),
                "zmw": float(money.get("zmw") or 0.0),
            }
        )
    if rest:
        other = _empty_money()
        for _, money in rest:
            for key in other:
                other[key] += float(money.get(key) or 0.0)
        slices.append(
            {
                "name": "Other",
                "tzs": other["tzs"],
                "usd": other["usd"],
                "zmw": other["zmw"],
            }
        )
    return slices


def compute_overview_charts(
    docs: Iterable[dict[str, Any]],
    *,
    year: int,
    top_items: int = 4,
    top_payees: int = 5,
    payee_pool: int = _DEFAULT_PAYEE_POOL,
) -> dict[str, Any]:
    """Build item breakdown, daily totals, and named-payee daily series for one year.

    Unassigned payees are excluded from the top-payee series. A larger named
    payee pool is returned so the client can re-rank for Month / 3m / Year.
    """
    item_totals: dict[str, dict[str, float]] = {}
    payee_totals: dict[str, dict[str, float]] = {}
    payee_daily: dict[str, dict[str, dict[str, float]]] = {}
    daily_totals: dict[str, dict[str, float]] = {}

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    display_n = max(1, int(top_payees))
    pool_n = max(display_n, int(payee_pool))

    for doc in docs:
        day = _day_key(doc.get("date"))
        if day is None or day < year_start or day > year_end:
            continue
        if doc.get("trashed") is True or doc.get("discarded") is True:
            continue

        amt = abs(float(doc.get("amount") or 0.0))
        if amt <= 0 or _currency_key(doc) is None:
            continue

        item = item_label(doc)
        payee = payee_label(doc)
        day_iso = day.isoformat()

        item_bucket = item_totals.setdefault(item, _empty_money())
        _add_money(item_bucket, doc)

        day_bucket = daily_totals.setdefault(day_iso, _empty_daily())
        _add_money(day_bucket, doc)
        day_bucket["count"] = float(day_bucket.get("count") or 0.0) + 1.0

        if payee == _UNASSIGNED_PAYEE:
            continue

        payee_bucket = payee_totals.setdefault(payee, _empty_money())
        _add_money(payee_bucket, doc)

        payee_day_map = payee_daily.setdefault(payee, {})
        payee_day_bucket = payee_day_map.setdefault(day_iso, _empty_money())
        _add_money(payee_day_bucket, doc)

    items = _top_breakdown(item_totals, top_n=top_items)

    payee_ranked = sorted(
        payee_totals.items(),
        key=lambda pair: _rank_key(pair[1]),
        reverse=True,
    )
    payee_ranked = [
        (name, money)
        for name, money in payee_ranked
        if name != _UNASSIGNED_PAYEE and _rank_key(money) > 0
    ][:pool_n]

    series: list[dict[str, Any]] = []
    for name, _money in payee_ranked:
        series.append(
            {
                "key": name,
                "label": name,
                "daily": payee_daily.get(name) or {},
            }
        )

    return {
        "year": int(year),
        "items": items,
        "daily": daily_totals,
        "payees": {"series": series, "top_n": display_n},
    }
