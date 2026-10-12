import asyncio
from datetime import datetime, date
from typing import List, Optional
from bson import ObjectId

from tahmeed.models.transaction import Transaction
from tahmeed.services.api_client import ApiError, api_client
from tahmeed.services.api_models import desktop_document, jsonable_document


def _tx_from_api(document: dict) -> Transaction:
    return Transaction.from_doc(desktop_document(document))


def _tx_create_body(tx: Transaction) -> dict:
    doc = tx.to_doc()
    doc.pop("_id", None)
    return jsonable_document(doc)


async def get_transactions_by_date(
    target_date: date,
    cashier_id=None,
    *,
    merged: bool = False,
) -> List[Transaction]:
    """Load a calendar day's register rows.

    Includes daily-import rows filed under that day via ``import_primary_date``
    (Excel dates may differ; the whole upload still belongs on the register day)
    and manual/legacy rows whose Excel ``date`` is that day.

    ``merged=True`` returns every cashier's rows for that day (Shared/Merged mode).
    Otherwise ``cashier_id`` scopes to one user when provided.
    Sorted by ``day_order`` then ``created_at``.
    """
    params: dict = {
        "date": target_date.isoformat(),
        "merged": "true" if merged else "false",
    }
    if not merged and cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    payload = await api_client.request("GET", "v1/transactions/by-date", params=params)
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def submit_day_for_verify(target_date: date) -> int:
    """Mark all draft (and legacy) unverified rows for *target_date* as submitted.

    Includes daily-import rows filed under this register day via
    ``import_primary_date``. Returns the number of documents updated.
    """
    payload = await api_client.request(
        "POST",
        "v1/transactions/submit-day",
        json={"date": target_date.isoformat()},
    )
    return int(payload.get("updated") or 0)


async def recount_day_order(target_date: date, ordered_ids: List[ObjectId]) -> None:
    """Persist ``day_order`` = index for each id in *ordered_ids*."""
    if not ordered_ids:
        return
    await api_client.request(
        "POST",
        "v1/transactions/day-order",
        json={
            "date": target_date.isoformat(),
            "ordered_ids": [str(oid) for oid in ordered_ids],
        },
    )


async def next_day_order(target_date: date) -> int:
    """Next ``day_order`` for *target_date* (append after every cashier's rows)."""
    payload = await api_client.request(
        "GET",
        "v1/transactions/next-day-order",
        params={"date": target_date.isoformat()},
    )
    try:
        return int(payload.get("next") or 0)
    except (TypeError, ValueError):
        return 0


async def save_transaction(tx: Transaction) -> Transaction:
    payload = await api_client.request(
        "POST", "v1/transactions", json=_tx_create_body(tx)
    )
    saved = _tx_from_api(payload)
    tx._id = saved._id
    try:
        from tahmeed.services.audit_service import record_event

        await record_event(
            "txn.save",
            actor_id=tx.cashier_id,
            entity_type="transaction",
            entity_ids=[tx._id],
            details={
                "amount": tx.amount,
                "description": (tx.description or "")[:120],
                "register_status": tx.register_status,
            },
        )
    except Exception:
        pass
    return saved


async def update_transaction(tx_id: ObjectId, updates: dict) -> bool:
    """Apply a $set update to an existing transaction. Used by the explicit
    Save action when committing edits to already-saved rows. The caller is
    responsible for building `updates` from the edited cell values (excluding
    immutable / accountant-managed fields)."""
    try:
        await api_client.request(
            "PATCH",
            f"v1/transactions/{tx_id}",
            json={"values": jsonable_document(dict(updates))},
        )
    except ApiError as exc:
        if exc.status_code == 404:
            return False
        raise
    try:
        from tahmeed.services.audit_service import record_event

        await record_event(
            "txn.update",
            actor_id=updates.get("last_edited_by"),
            entity_type="transaction",
            entity_ids=[tx_id],
            details={"fields": sorted(str(k) for k in updates.keys())},
        )
    except Exception:
        pass
    return True


async def delete_transaction(tx_id: ObjectId) -> None:
    """Hard-delete a transaction by id (no status guards). Prefer
    ``request_or_delete_transaction`` from the cashier register UI."""
    await api_client.request("DELETE", f"v1/transactions/{tx_id}")


async def request_or_delete_transaction(
    tx_id: ObjectId,
    cashier_id: Optional[ObjectId] = None,
) -> str:
    """Delete or request deletion of a register row.

    Returns one of:
      - ``"deleted"`` — hard-deleted (unverified, or pending-edit clone)
      - ``"deletion_requested"`` — approved row flagged for accountant confirm
      - ``"not_found"`` — no matching document
    """
    body: dict = {}
    if cashier_id is not None:
        body["cashier_id"] = str(cashier_id)
    payload = await api_client.request(
        "POST",
        f"v1/transactions/{tx_id}/request-delete",
        json=body,
    )
    result = str(payload.get("result") or "not_found")
    if result in {"deleted", "deletion_requested", "not_found"}:
        return result
    return "not_found"


async def get_cashier_trash(
    search: str = "",
    limit: int = 100,
    skip: int = 0,
) -> List[Transaction]:
    params: dict = {"limit": limit, "skip": skip}
    if search.strip():
        params["search"] = search.strip()
    payload = await api_client.request(
        "GET", "v1/transactions/trash", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_cashier_trash() -> int:
    payload = await api_client.request("GET", "v1/transactions/trash/count")
    return int(payload.get("count") or 0)


async def request_restore_from_trash(
    tx_id: ObjectId,
    cashier_id: Optional[ObjectId] = None,
) -> str:
    """Ask accountant to restore a trashed row.

    Returns ``restore_requested``, ``already_requested``, or ``not_found``.
    """
    body: dict = {}
    if cashier_id is not None:
        body["cashier_id"] = str(cashier_id)
    payload = await api_client.request(
        "POST",
        f"v1/transactions/{tx_id}/request-restore",
        json=body,
    )
    result = str(payload.get("result") or "not_found")
    if result in {"restore_requested", "already_requested", "not_found"}:
        return result
    return "not_found"


async def bulk_request_restore_from_trash(tx_ids: List[ObjectId]) -> int:
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/transactions/trash/bulk-request-restore",
        json={"ids": [str(oid) for oid in tx_ids]},
    )
    return int(payload.get("updated") or 0)


async def cancel_restore_request(tx_id: ObjectId) -> bool:
    payload = await api_client.request(
        "POST", f"v1/transactions/{tx_id}/cancel-restore"
    )
    return bool(payload.get("ok"))


async def search_transactions(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    truck: str = "",
    limit: int = 500,
) -> List[Transaction]:
    params: dict = {"limit": limit}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    if keyword.strip():
        params["keyword"] = keyword.strip()
    if truck.strip():
        params["truck"] = truck.strip()
    payload = await api_client.request("GET", "v1/transactions/search", params=params)
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_overview_stats() -> dict:
    """Aggregate today stats, this-month stats, and recent 8 transactions."""
    payload = await api_client.request("GET", "v1/transactions/overview-stats")
    recent = [_tx_from_api(doc) for doc in payload.get("recent") or []]
    _empty = {
        "count": 0,
        "tzs_total": 0.0,
        "receipt_received": 0,
        "receipt_pending": 0,
        "receipt_missing": 0,
        "verified": 0,
        "unverified": 0,
    }

    def _bucket(raw) -> dict:
        row = dict(_empty)
        src = raw or {}
        for key in _empty:
            if key == "tzs_total":
                row[key] = float(src.get(key) or 0.0)
            else:
                row[key] = int(src.get(key) or 0)
        return row

    return {
        "today": _bucket(payload.get("today")),
        "month": _bucket(payload.get("month")),
        "recent": recent,
    }


_OVERVIEW_PERIODS = ("month", "3m", "year")
_CURRENCY_KEYS = {"TZS": "tzs", "USD": "usd", "ZMW": "zmw"}


def _parse_overview_day(raw) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _overview_period_spec(period: str, year: int, today: date | None = None) -> dict:
    """Labels / bucket keys for cashier Overview payee trend periods."""
    import calendar
    from datetime import timedelta

    today = today or date.today()
    period = period if period in _OVERVIEW_PERIODS else "year"
    as_of = today if year == today.year else (
        date(year, 12, 31) if year < today.year else date(year, 1, 1)
    )

    if period == "month":
        start = date(as_of.year, as_of.month, 1)
        keys = [date(as_of.year, as_of.month, day) for day in range(1, as_of.day + 1)]
        labels = [str(day) for day in range(1, as_of.day + 1)]
        subtitle = (
            f"Spend by day, {calendar.month_name[as_of.month]} {as_of.year}"
        )
        grain = "day"
        end = as_of
    elif period == "3m":
        start_month = as_of.month - 2
        start_year = as_of.year
        while start_month < 1:
            start_month += 12
            start_year -= 1
        start = date(start_year, start_month, 1)
        fy_start = date(year, 1, 1)
        if start < fy_start:
            start = fy_start
        end = as_of
        grain = "week"
        keys = []
        labels = []
        cursor = start - timedelta(days=start.weekday())
        while cursor <= end:
            keys.append(cursor)
            label_day = start if cursor < start else cursor
            labels.append(f"{label_day.day} {calendar.month_abbr[label_day.month]}")
            cursor += timedelta(days=7)
        if start.month == as_of.month and start.year == as_of.year:
            subtitle = (
                f"Spend by week, {calendar.month_name[as_of.month]} {as_of.year}"
            )
        elif start.year == as_of.year:
            subtitle = (
                f"Spend by week, {calendar.month_abbr[start.month]} to "
                f"{calendar.month_abbr[as_of.month]} {as_of.year}"
            )
        else:
            subtitle = (
                f"Spend by week, {calendar.month_abbr[start.month]} {start.year} "
                f"to {calendar.month_abbr[as_of.month]} {as_of.year}"
            )
    else:
        grain = "month"
        start = date(year, 1, 1)
        end_month = as_of.month if as_of.year == year else 12
        end = date(year, end_month, calendar.monthrange(year, end_month)[1])
        if as_of.year == year:
            end = min(end, as_of)
        keys = [date(year, month, 1) for month in range(1, end_month + 1)]
        labels = [calendar.month_abbr[month] for month in range(1, end_month + 1)]
        if end_month == 1:
            subtitle = f"Spend by month, Jan {year}"
        elif end_month == 12:
            subtitle = f"Spend by month, {year}"
        else:
            subtitle = (
                f"Spend by month, Jan to {calendar.month_abbr[end_month]} {year}"
            )

    return {
        "period": period,
        "grain": grain,
        "start": start,
        "end": end,
        "keys": keys,
        "labels": labels,
        "subtitle": subtitle,
    }


def _bucket_day(day: date, grain: str) -> date:
    if grain == "month":
        return date(day.year, day.month, 1)
    if grain == "week":
        from datetime import timedelta
        return day - timedelta(days=day.weekday())
    return day


def bucket_payee_spend(
    payee_series: list[dict],
    period: str,
    year: int,
    currency: str = "TZS",
    today: date | None = None,
) -> dict:
    """Roll top-payee daily money into Overview line-chart buckets."""
    spec = _overview_period_spec(period, year, today)
    cur_key = _CURRENCY_KEYS.get((currency or "TZS").upper(), "tzs")
    index = {key: i for i, key in enumerate(spec["keys"])}
    series = []
    for item in payee_series or []:
        values = [0.0] * len(spec["keys"])
        daily = item.get("daily") or {}
        for raw_day, money in daily.items():
            day = _parse_overview_day(raw_day)
            if day is None or day < spec["start"] or day > spec["end"]:
                continue
            slot = index.get(_bucket_day(day, spec["grain"]))
            if slot is None:
                continue
            if isinstance(money, dict):
                values[slot] += float(money.get(cur_key) or 0.0)
            else:
                values[slot] += float(money or 0.0)
        series.append(
            {
                "key": item.get("key") or item.get("label") or "payee",
                "label": item.get("label") or item.get("key") or "Payee",
                "values": values,
            }
        )
    return {
        "period": spec["period"],
        "labels": spec["labels"],
        "subtitle": spec["subtitle"],
        "series": series,
    }


async def get_overview_charts(year: int | None = None) -> dict:
    """Item breakdown + top-payee daily series for cashier Overview charts."""
    params: dict = {}
    if year is not None:
        params["year"] = int(year)
    payload = await api_client.request(
        "GET", "v1/transactions/overview-charts", params=params or None
    )
    items = []
    for row in payload.get("items") or []:
        items.append(
            {
                "name": row.get("name") or "Uncategorised",
                "tzs": float(row.get("tzs") or 0.0),
                "usd": float(row.get("usd") or 0.0),
                "zmw": float(row.get("zmw") or 0.0),
            }
        )
    payees = []
    for row in (payload.get("payees") or {}).get("series") or []:
        daily_raw = row.get("daily") or {}
        daily: dict = {}
        for day_key, money in daily_raw.items():
            if isinstance(money, dict):
                daily[str(day_key)] = {
                    "tzs": float(money.get("tzs") or 0.0),
                    "usd": float(money.get("usd") or 0.0),
                    "zmw": float(money.get("zmw") or 0.0),
                }
        payees.append(
            {
                "key": row.get("key") or row.get("label") or "payee",
                "label": row.get("label") or row.get("key") or "Payee",
                "daily": daily,
            }
        )
    return {
        "year": int(payload.get("year") or year or date.today().year),
        "items": items,
        "payees": payees,
    }


async def get_transactions_by_category(
    cashier_id, category_name: str, description: str = "", limit: int = 500
) -> List[Transaction]:
    params: dict = {"category_name": category_name, "limit": limit}
    if cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    if description.strip():
        params["description"] = description.strip()
    payload = await api_client.request(
        "GET", "v1/transactions/by-category", params=params
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


def _csv_param(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _parse_summary_date(raw) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw or "").strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    return date.fromisoformat(text)


async def get_daily_summaries(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    truck: str = "",
    category_name="",
    sub_item_match="",
    descriptions="",
    daily_import_id: str = "",
    limit: int = 365,
) -> list:
    """Aggregate transactions by register day, returning one summary dict per day.

    Grouping uses ``import_primary_date`` when set (daily-import register day),
    otherwise Excel ``date``. Prior-day Excel rows inside an upload therefore
    stay on that upload's day transaction instead of spawning a separate TXN-*.

    Each dict has: date (date), entries_count (int), total_tzs (float), total_refund (float).
    Filters narrow which *entries* are counted before the group-by, so a truck
    filter returns days that contain that truck with counts/totals for that truck only.

    ``category_name`` / ``descriptions`` accept a string or list (multi-select).
    ``sub_item_match`` is kept for backward compatibility (maps into descriptions).
    """
    desc_filter = descriptions if descriptions not in ("", None, []) else sub_item_match
    params: dict = {"limit": limit}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    if keyword.strip():
        params["keyword"] = keyword.strip()
    if truck.strip():
        params["truck"] = truck.strip()
    cat = _csv_param(category_name)
    if cat:
        params["category_name"] = cat
    desc = _csv_param(desc_filter)
    if desc:
        params["descriptions"] = desc
    if daily_import_id:
        params["daily_import_id"] = str(daily_import_id).strip()
    payload = await api_client.request(
        "GET", "v1/transactions/daily-summaries", params=params
    )
    result = []
    for row in payload.get("items") or []:
        item = dict(row)
        item["date"] = _parse_summary_date(item.get("date"))
        cashier_ids = []
        for cid in item.get("cashier_ids") or []:
            if cid is None:
                continue
            cashier_ids.append(ObjectId(str(cid)) if ObjectId.is_valid(str(cid)) else cid)
        item["cashier_ids"] = cashier_ids
        result.append(item)
    return result


async def get_transactions_flat(
    date_from: date = None,
    date_to: date = None,
    keyword: str = "",
    category_name="",
    sub_item_match="",
    descriptions="",
    daily_import_id: str = "",
    limit: int = 1000,
) -> List[Transaction]:
    """Return individual transactions matching all supplied filters."""
    desc_filter = descriptions if descriptions not in ("", None, []) else sub_item_match
    params: dict = {"limit": limit}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    if keyword.strip():
        params["keyword"] = keyword.strip()
    if daily_import_id:
        params["daily_import_id"] = str(daily_import_id).strip()
    if isinstance(category_name, (list, tuple)):
        params["category_name"] = ",".join(str(v) for v in category_name if str(v).strip())
    elif category_name:
        params["category_name"] = str(category_name)
    if isinstance(desc_filter, (list, tuple)):
        params["descriptions"] = ",".join(str(v) for v in desc_filter if str(v).strip())
    elif desc_filter:
        params["descriptions"] = str(desc_filter)
    payload = await api_client.request("GET", "v1/transactions/flat", params=params)
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_browse_items(
    date_from: date = None,
    date_to: date = None,
    descriptions=None,
) -> List[str]:
    """Distinct item / category names in the browse date window (cascading)."""
    params: dict = {}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    desc = _csv_param(descriptions)
    if desc:
        params["descriptions"] = desc
    payload = await api_client.request(
        "GET", "v1/transactions/browse/items", params=params
    )
    return list(payload.get("items") or [])


async def get_browse_descriptions(
    date_from: date = None,
    date_to: date = None,
    category_name=None,
) -> List[str]:
    """Distinct descriptions in the browse date window (cascading by items)."""
    params: dict = {}
    if date_from:
        params["date_from"] = date_from.isoformat()
    if date_to:
        params["date_to"] = date_to.isoformat()
    cat = _csv_param(category_name)
    if cat:
        params["category_name"] = cat
    payload = await api_client.request(
        "GET", "v1/transactions/browse/descriptions", params=params
    )
    return list(payload.get("items") or [])


async def get_available_months() -> list:
    """Return (year, month) tuples for every month that has transactions, newest first."""
    payload = await api_client.request("GET", "v1/transactions/meta/months")
    return [
        (int(row["year"]), int(row["month"]))
        for row in payload.get("items") or []
        if row.get("year") is not None and row.get("month") is not None
    ]


async def get_available_years() -> list:
    """Calendar years that have transactions, newest first (always includes recent years)."""
    payload = await api_client.request("GET", "v1/transactions/meta/years")
    return [int(year) for year in payload.get("items") or []]


# In-memory description history for Excel-style autocomplete across all days.
_desc_ranked: Optional[List[str]] = None
_desc_counts: Optional[dict] = None
_desc_lock = asyncio.Lock()
_DESC_CACHE_LIMIT = 5000


def invalidate_description_cache() -> None:
    global _desc_ranked, _desc_counts
    _desc_ranked = None
    _desc_counts = None


def _rebuild_desc_ranked() -> None:
    global _desc_ranked
    counts = _desc_counts or {}
    _desc_ranked = sorted(counts.keys(), key=lambda d: (-counts[d], d))


async def ensure_description_cache() -> List[str]:
    """Load distinct descriptions (all days) ranked by frequency."""
    global _desc_ranked, _desc_counts
    if _desc_ranked is not None:
        return _desc_ranked
    async with _desc_lock:
        if _desc_ranked is not None:
            return _desc_ranked
        payload = await api_client.request(
            "GET",
            "v1/transactions/descriptions",
            params={"prefix": "", "limit": _DESC_CACHE_LIMIT},
        )
        ranked = [
            str(name).strip()
            for name in payload.get("items") or []
            if str(name).strip()
        ]
        counts = {name: max(_DESC_CACHE_LIMIT - index, 1) for index, name in enumerate(ranked)}
        _desc_counts = counts
        _desc_ranked = ranked
        return _desc_ranked


def search_descriptions_sync(prefix: str, limit: int = 12) -> Optional[List[str]]:
    """Prefix filter against the warm description cache (no await).

    Returns ``None`` when the cache has not been loaded yet — callers should
    fall through to the async path. Safe during modal dialogs / nested tasks.
    """
    if _desc_ranked is None:
        return None
    value = (prefix or "").strip().lower()
    if not value:
        return []
    out: List[str] = []
    for description in _desc_ranked:
        if description.lower().startswith(value):
            out.append(description)
            if len(out) >= limit:
                break
    return out


def remember_description(text: str) -> None:
    """Boost a just-entered description so same-session typing can Tab-complete it."""
    global _desc_ranked, _desc_counts
    if _desc_ranked is None:
        return
    name = (text or "").strip().upper()
    if not name:
        return
    counts = _desc_counts if _desc_counts is not None else {}
    counts[name] = counts.get(name, 0) + 1
    _desc_counts = counts
    _rebuild_desc_ranked()


async def search_descriptions(prefix: str, limit: int = 12) -> List[str]:
    """
    Return distinct descriptions whose prefix matches (case-insensitive),
    sorted by frequency so the most-used descriptions appear first.
    Uses the warm in-memory cache covering all days in the system.
    """
    if not prefix.strip():
        return []
    cached = search_descriptions_sync(prefix, limit)
    if cached is not None:
        return cached
    payload = await api_client.request(
        "GET",
        "v1/transactions/descriptions",
        params={"prefix": prefix.strip(), "limit": limit},
    )
    return [str(name) for name in payload.get("items") or [] if str(name).strip()]


async def resolve_mapping_fields_for_description(
    description: str,
) -> tuple[Optional[str], Optional[str]]:
    """Item + payee for a description: saved map first, then prior item history.

    Returns ``(item_name, payee)``. Payee only comes from a saved description
    map (history fallback fills item only). Either side may be None.
    """
    from tahmeed.services.description_mapping_service import (
        normalize_description,
        resolve_mapping_for_description,
    )

    key = normalize_description(description)
    if not key:
        return None, None
    try:
        mapping = await resolve_mapping_for_description(description)
        if mapping is not None:
            item = (mapping.category_name or "").strip() or None
            payee = (mapping.payee or "").strip() or None
            if item or payee:
                return item, payee
    except Exception:
        pass

    payload = await api_client.request(
        "GET",
        "v1/transactions/resolve-item",
        params={"description": key},
    )
    item = (str(payload.get("item") or "").strip() or None)
    payee = (str(payload.get("payee") or "").strip() or None)
    return item, payee


async def resolve_item_name_for_description(description: str) -> Optional[str]:
    """Item/category for a description (wrapper around mapping-fields resolve)."""
    item, _payee = await resolve_mapping_fields_for_description(description)
    return item


async def get_rejected_transactions_for_cashier(
    cashier_id: ObjectId,
    limit: int = 200,
) -> List[Transaction]:
    """Active rejected entries for this cashier (excludes discarded), newest first."""
    payload = await api_client.request(
        "GET",
        "v1/transactions/cashier/rejected",
        params={"cashier_id": str(cashier_id), "limit": limit},
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def get_discarded_transactions_for_cashier(
    cashier_id: ObjectId,
    limit: int = 200,
) -> List[Transaction]:
    """Soft-discarded rejected entries for this cashier, newest first."""
    payload = await api_client.request(
        "GET",
        "v1/transactions/cashier/discarded",
        params={"cashier_id": str(cashier_id), "limit": limit},
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def discard_transactions(
    tx_ids: List[ObjectId],
    cashier_id: ObjectId,
) -> int:
    """Soft-discard rejected entries owned by this cashier. Returns modified count."""
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/transactions/cashier/discard",
        json={
            "ids": [str(oid) for oid in tx_ids],
            "cashier_id": str(cashier_id),
        },
    )
    return int(payload.get("updated") or 0)


async def restore_discarded_transactions(
    tx_ids: List[ObjectId],
    cashier_id: ObjectId,
) -> int:
    """Move discarded entries back to the active Rejected list. Returns modified count."""
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/transactions/cashier/restore-discarded",
        json={
            "ids": [str(oid) for oid in tx_ids],
            "cashier_id": str(cashier_id),
        },
    )
    return int(payload.get("updated") or 0)


async def delete_discarded_transactions(
    tx_ids: List[ObjectId],
    cashier_id: ObjectId,
) -> int:
    """Permanently delete discarded entries owned by this cashier. Returns deleted count."""
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/transactions/cashier/delete-discarded",
        json={
            "ids": [str(oid) for oid in tx_ids],
            "cashier_id": str(cashier_id),
        },
    )
    return int(payload.get("deleted") or 0)


async def resubmit_rejected_transactions(
    cashier_id: ObjectId,
    updates_by_id: dict,
) -> int:
    """Apply field updates and clear rejection/discard flags for owned rejected rows.

    ``updates_by_id`` maps ObjectId → field updates dict (may be empty for
    resubmit-without-edit). Returns number of successfully updated documents.
    """
    if not updates_by_id:
        return 0
    body = {
        "cashier_id": str(cashier_id),
        "updates_by_id": {
            str(tx_id): jsonable_document(dict(fields or {}))
            for tx_id, fields in updates_by_id.items()
        },
    }
    payload = await api_client.request(
        "POST", "v1/transactions/cashier/resubmit", json=body
    )
    return int(payload.get("updated") or 0)


async def check_for_duplicates(
    truck_number: str,
    amount: float,
    item: str,
    description: str,
    days: int,
    exclude_id: Optional[ObjectId] = None,
) -> List[Transaction]:
    """Return existing transactions within the last `days` days that share
    truck_number, amount, item, and description with the candidate entry."""
    body: dict = {
        "truck_number": truck_number or "",
        "amount": float(amount or 0.0),
        "item": item or "",
        "description": description or "",
        "days": int(days),
    }
    if exclude_id is not None:
        body["exclude_id"] = str(exclude_id)
    payload = await api_client.request(
        "POST", "v1/transactions/check-duplicates", json=body
    )
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def count_draft_transactions(
    cashier_id=None,
    *,
    all_cashiers: bool = False,
) -> int:
    """Count unsubmitted draft rows (for sidebar badge)."""
    params: dict = {"all_cashiers": "true" if all_cashiers else "false"}
    if not all_cashiers and cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    payload = await api_client.request(
        "GET", "v1/transactions/drafts/count", params=params
    )
    return int(payload.get("count") or 0)


async def get_draft_day_summaries(
    cashier_id=None,
    *,
    all_cashiers: bool = False,
    limit: int = 120,
) -> List[dict]:
    """Calendar days that have at least one draft entry, newest first."""
    params: dict = {
        "all_cashiers": "true" if all_cashiers else "false",
        "limit": limit,
    }
    if not all_cashiers and cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    payload = await api_client.request(
        "GET", "v1/transactions/drafts/day-summaries", params=params
    )
    result = []
    for row in payload.get("items") or []:
        item = dict(row)
        item["date"] = _parse_summary_date(item.get("date"))
        cashier_ids = []
        for cid in item.get("cashier_ids") or []:
            if cid is None:
                continue
            cashier_ids.append(
                ObjectId(str(cid)) if ObjectId.is_valid(str(cid)) else cid
            )
        item["cashier_ids"] = cashier_ids
        result.append(item)
    return result


async def get_open_day_summaries(
    cashier_id=None,
    *,
    all_cashiers: bool = False,
    limit: int = 120,
) -> List[dict]:
    """Calendar days with open (unverified) register rows — Day Issues All days."""
    params: dict = {
        "all_cashiers": "true" if all_cashiers else "false",
        "limit": limit,
    }
    if not all_cashiers and cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    payload = await api_client.request(
        "GET", "v1/transactions/open-day-summaries", params=params
    )
    result = []
    for row in payload.get("items") or []:
        item = dict(row)
        item["date"] = _parse_summary_date(item.get("date"))
        cashier_ids = []
        for cid in item.get("cashier_ids") or []:
            if cid is None:
                continue
            cashier_ids.append(
                ObjectId(str(cid)) if ObjectId.is_valid(str(cid)) else cid
            )
        item["cashier_ids"] = cashier_ids
        result.append(item)
    return result


async def get_draft_transactions(
    register_date: date,
    cashier_id=None,
    *,
    merged: bool = False,
) -> List[Transaction]:
    """Draft rows for one register calendar day."""
    params: dict = {
        "date": register_date.isoformat(),
        "merged": "true" if merged else "false",
    }
    if not merged and cashier_id is not None:
        params["cashier_id"] = str(cashier_id)
    payload = await api_client.request("GET", "v1/transactions/drafts", params=params)
    return [_tx_from_api(doc) for doc in payload.get("items") or []]


async def discard_draft_transactions(
    tx_ids: List[ObjectId],
    actor_id: ObjectId,
    *,
    allow_any_cashier: bool = False,
) -> int:
    """Hard-delete draft rows. Cashiers may only discard their own unless
    ``allow_any_cashier`` is True (accountant merged register)."""
    if not tx_ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/transactions/drafts/discard",
        json={
            "ids": [str(oid) for oid in tx_ids],
            "cashier_id": str(actor_id),
            "allow_any_cashier": allow_any_cashier,
        },
    )
    return int(payload.get("deleted") or 0)


async def discard_draft_day(
    register_date: date,
    actor_id: ObjectId,
    *,
    allow_any_cashier: bool = False,
) -> int:
    """Hard-delete every draft row for one register calendar day."""
    payload = await api_client.request(
        "POST",
        "v1/transactions/drafts/discard-day",
        json={
            "date": register_date.isoformat(),
            "cashier_id": str(actor_id),
            "allow_any_cashier": allow_any_cashier,
        },
    )
    return int(payload.get("deleted") or 0)


async def insert_pending_edit(
    original_tx_id: ObjectId,
    updates: dict,
    cashier_id: ObjectId,
) -> ObjectId:
    """Insert (or refresh) a pending-edit document instead of modifying the original.

    The original approved transaction stays untouched in Master Expenses.
    The pending doc carries original_transaction_id so the accountant's
    re_approve_transaction can cascade the new values back to the original.
    Re-editing the same original updates the existing pending doc in place.
    """
    try:
        payload = await api_client.request(
            "POST",
            f"v1/transactions/{original_tx_id}/pending-edit",
            json={
                "updates": jsonable_document(dict(updates)),
                "cashier_id": str(cashier_id),
            },
        )
    except ApiError as exc:
        if exc.status_code == 404:
            raise ValueError(
                f"Original transaction {original_tx_id} not found"
            ) from exc
        raise
    pending_id = payload.get("_id") or payload.get("id")
    if not pending_id:
        raise ValueError("Pending edit response missing id")
    return ObjectId(str(pending_id))
