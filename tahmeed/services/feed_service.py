"""Imported feeds + separate expenses + skipped rows via the HTTPS API (Phase 8.10).

Excel parsing stays on the desktop; persistence and ledger queries go through
``/v1/feeds/{slug}/...``. Pure hash helpers used by import dialogs live here too.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, jsonable_document

# feed_type / expense_type → URL slug
_FEED_SLUGS: Dict[str, str] = {
    "toll_plaza": "toll-plaza",
    "parking_congo": "parking-congo",
    "rahntech": "rahntech",
    "zambia_parking": "zambia-parking",
    "comesa": "comesa",
    "third_party": "third-party",
    "diesel_infinity": "diesel-infinity",
    "diesel_lake_zambia": "diesel-lake-zambia",
    "diesel_lake_tunduma": "diesel-lake-tunduma",
    "diesel_gbp": "diesel-gbp",
    "ahmed_kimvi": "kimvi",
    "congo_expenses": "congo",
    "afritrack": "afritrack",
}

_DIESEL_HASH_FIELDS = (
    "date",
    "lpo_no",
    "do_sdo_no",
    "diesel_at",
    "ownership",
    "clients_name",
    "destinations",
    "truck_no",
    "ltrs",
    "price_per_ltr",
    "total_amount",
)


def feed_slug(feed_type: str) -> str:
    key = (feed_type or "").strip()
    if key in _FEED_SLUGS:
        return _FEED_SLUGS[key]
    return key.replace("_", "-")


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _params(**kwargs: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
            continue
        if isinstance(value, int) and value == 0 and key in ("year", "month"):
            continue
        if isinstance(value, str) and value == "" and key not in (
            "search",
            "feed_key",
        ):
            continue
        if key == "month" and isinstance(value, int):
            if value > 0:
                out[key] = str(value)
            continue
        if key == "file_labels" and isinstance(value, (list, tuple)):
            labels = [str(x).strip() for x in value if str(x).strip()]
            if labels:
                out[key] = ",".join(labels)
            continue
        if key in ("date_from", "date_to"):
            if isinstance(value, datetime):
                out[key] = value.isoformat()
            elif isinstance(value, str) and value.strip():
                out[key] = value
            continue
        out[key] = value
    return out


def _docs(payload: Any) -> list:
    if not isinstance(payload, list):
        return []
    return [desktop_document(dict(row)) for row in payload]


def _hash_field(value: Any) -> str:
    return str(value or "").strip().upper()


def zambia_batch_content_hash(records: Sequence[dict]) -> str:
    """SHA-256 of ordered Zambia Parking rows (whole-file duplicate gate)."""
    lines = []
    for rec in records:
        lines.append(
            "|".join(
                [
                    _hash_field(rec.get("date")),
                    _hash_field(rec.get("type")),
                    _hash_field(rec.get("plate_num")),
                    _hash_field(rec.get("ticket_no")),
                    _hash_field(rec.get("debit")),
                    _hash_field(rec.get("credit")),
                    _hash_field(rec.get("balance")),
                    _hash_field(rec.get("heading_to")),
                ]
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def diesel_batch_content_hash(records: Sequence[dict]) -> str:
    """SHA-256 of ordered diesel rows (whole-file duplicate gate)."""
    lines = []
    for rec in records:
        lines.append(
            "|".join(_hash_field(rec.get(field)) for field in _DIESEL_HASH_FIELDS)
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ── Generic ledger helpers ───────────────────────────────────────────────────


async def list_records(slug: str, **kwargs: Any) -> list:
    payload = await api_client.request(
        "GET",
        f"v1/feeds/{slug}/records",
        params=_params(**kwargs),
    )
    return _docs(payload)


async def count_records(slug: str, **kwargs: Any) -> int:
    payload = await api_client.request(
        "GET",
        f"v1/feeds/{slug}/records/count",
        params=_params(**kwargs),
    )
    return int((payload or {}).get("count") or 0)


async def totals(slug: str, **kwargs: Any) -> dict:
    payload = await api_client.request(
        "GET",
        f"v1/feeds/{slug}/totals",
        params=_params(**kwargs),
    )
    return dict(payload or {})


async def years(slug: str) -> List[int]:
    payload = await api_client.request("GET", f"v1/feeds/{slug}/years")
    items = (payload or {}).get("items") or []
    return [int(y) for y in items]


async def list_uploads(slug: str) -> list:
    payload = await api_client.request("GET", f"v1/feeds/{slug}/uploads")
    return _docs(payload)


async def upload_records(slug: str, upload_id: str, **kwargs: Any) -> list:
    payload = await api_client.request(
        "GET",
        f"v1/feeds/{slug}/uploads/{upload_id}/records",
        params=_params(**kwargs),
    )
    return _docs(payload)


async def upload_count(slug: str, upload_id: str, **kwargs: Any) -> int:
    payload = await api_client.request(
        "GET",
        f"v1/feeds/{slug}/uploads/{upload_id}/records/count",
        params=_params(**kwargs),
    )
    return int((payload or {}).get("count") or 0)


async def upload_totals(slug: str, upload_id: str, **kwargs: Any) -> dict:
    payload = await api_client.request(
        "GET",
        f"v1/feeds/{slug}/uploads/{upload_id}/totals",
        params=_params(**kwargs),
    )
    return dict(payload or {})


async def delete_upload(slug: str, upload_id: str) -> int:
    payload = await api_client.request(
        "DELETE", f"v1/feeds/{slug}/uploads/{upload_id}"
    )
    return int((payload or {}).get("deleted") or 0)


async def existing_keys(slug: str, keys: Sequence[str]) -> set:
    if not keys:
        return set()
    payload = await api_client.request(
        "POST",
        f"v1/feeds/{slug}/existing-keys",
        json={"keys": list(keys)},
    )
    return set((payload or {}).get("keys") or [])


async def import_rows(slug: str, rows: Sequence[dict]) -> int:
    if not rows:
        return 0
    payload = await api_client.request(
        "POST",
        f"v1/feeds/{slug}/import",
        json={"rows": [jsonable_document(dict(r)) for r in rows]},
    )
    return int((payload or {}).get("inserted") or 0)


async def sheet_labels(slug: str, labels: Sequence[str]) -> set:
    clean = [str(l).strip() for l in labels if l and str(l).strip()]
    if not clean:
        return set()
    payload = await api_client.request(
        "POST",
        f"v1/feeds/{slug}/sheet-labels",
        json={"labels": clean},
    )
    return set((payload or {}).get("labels") or [])


# ── Public API matching accountant_service signatures ────────────────────────


async def get_imported_feed(
    feed_type: str,
    search: str = "",
    extra: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    del extra  # plaza filter removed; use search / dedicated endpoints
    return await list_records(
        feed_slug(feed_type), search=search, limit=limit, skip=skip
    )


async def count_imported_feed(
    feed_type: str, search: str = "", extra: str = ""
) -> int:
    del extra
    return await count_records(feed_slug(feed_type), search=search)


async def get_imported_feed_totals(feed_type: str) -> dict:
    return await totals(feed_slug(feed_type))


async def get_existing_feed_keys(
    keys: List[str], feed_type: str = "toll_plaza"
) -> set:
    return await existing_keys(feed_slug(feed_type), keys)


async def save_imported_feed(records: list) -> int:
    if not records:
        return 0
    feed_type = str(records[0].get("feed_type") or "").strip()
    if not feed_type:
        raise ValueError("Imported feed rows require feed_type")
    return await import_rows(feed_slug(feed_type), records)


async def get_toll_plaza_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "toll-plaza",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_toll_plaza_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    return await count_records(
        "toll-plaza",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_toll_plaza_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    return await totals(
        "toll-plaza",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_toll_plaza_available_years() -> List[int]:
    return await years("toll-plaza")


async def get_parking_congo_available_years() -> List[int]:
    return await years("parking-congo")


async def get_toll_plaza_uploads() -> list:
    return await list_uploads("toll-plaza")


async def get_toll_plaza_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await upload_records(
        "toll-plaza",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_toll_plaza_upload_records(upload_id: str, search: str = "") -> int:
    return await upload_count("toll-plaza", upload_id, search=search)


async def delete_toll_plaza_upload(upload_id: str) -> int:
    return await delete_upload("toll-plaza", upload_id)


async def get_parking_congo_uploads() -> list:
    return await list_uploads("parking-congo")


async def get_parking_congo_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await upload_records(
        "parking-congo",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_parking_congo_upload_records(
    upload_id: str, search: str = ""
) -> int:
    return await upload_count("parking-congo", upload_id, search=search)


async def get_parking_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "parking-congo",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_parking_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    return await count_records(
        "parking-congo",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_parking_congo_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    return await totals(
        "parking-congo",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def delete_parking_congo_upload(upload_id: str) -> int:
    return await delete_upload("parking-congo", upload_id)


async def get_parking_congo_deposits(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
) -> list:
    payload = await api_client.request(
        "GET",
        "v1/feeds/parking-congo/deposits",
        params=_params(
            search=search, date_from=date_from, date_to=date_to, limit=limit, skip=skip
        ),
    )
    return _docs(payload)


async def count_parking_congo_deposits(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    payload = await api_client.request(
        "GET",
        "v1/feeds/parking-congo/deposits/count",
        params=_params(search=search, date_from=date_from, date_to=date_to),
    )
    return int((payload or {}).get("count") or 0)


async def get_parking_congo_deposit_totals(
    search: str = "",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    payload = await api_client.request(
        "GET",
        "v1/feeds/parking-congo/deposits/totals",
        params=_params(search=search, date_from=date_from, date_to=date_to),
    )
    return dict(payload or {})


async def get_parking_congo_upload_by_id(upload_id: str) -> Optional[dict]:
    if not upload_id:
        return None
    for row in await list_uploads("parking-congo"):
        if str(row.get("_id") or row.get("upload_id") or "") == upload_id:
            return row
    return None


async def get_rahntech_uploads() -> list:
    return await list_uploads("rahntech")


async def get_rahntech_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await upload_records(
        "rahntech",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_rahntech_upload_records(upload_id: str, search: str = "") -> int:
    return await upload_count("rahntech", upload_id, search=search)


async def get_rahntech_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "rahntech",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_rahntech_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    return await count_records(
        "rahntech",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_rahntech_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    return await totals(
        "rahntech",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_rahntech_available_years() -> List[int]:
    return await years("rahntech")


async def delete_rahntech_upload(upload_id: str) -> int:
    return await delete_upload("rahntech", upload_id)


async def zambia_sheet_exists(sheet_label: str) -> bool:
    if not (sheet_label or "").strip():
        return False
    payload = await api_client.request(
        "POST",
        "v1/feeds/zambia-parking/sheet-exists",
        json={"labels": [sheet_label.strip()]},
    )
    return bool((payload or {}).get("exists"))


async def zambia_existing_sheet_labels(labels: List[str]) -> set:
    clean = [str(l).strip() for l in labels if l and str(l).strip()]
    if not clean:
        return set()
    payload = await api_client.request(
        "POST",
        "v1/feeds/zambia-parking/existing-sheet-labels",
        json={"labels": clean},
    )
    return set((payload or {}).get("labels") or [])


async def find_zambia_uploads_by_content_hash(content_hash: str) -> List[dict]:
    if not content_hash:
        return []
    payload = await api_client.request(
        "POST",
        "v1/feeds/zambia-parking/content-hash-matches",
        json={"content_hash": content_hash, "rows": []},
    )
    return list((payload or {}).get("matches") or [])


async def zambia_already_uploaded(records: List[dict]) -> List[dict]:
    return await find_zambia_uploads_by_content_hash(
        zambia_batch_content_hash(records)
    )


async def get_zambia_parking_uploads() -> list:
    return await list_uploads("zambia-parking")


async def get_zambia_parking_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await upload_records(
        "zambia-parking",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_zambia_parking_upload_records(
    upload_id: str, search: str = ""
) -> int:
    return await upload_count("zambia-parking", upload_id, search=search)


async def get_zambia_parking_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "zambia-parking",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        credit_only=credit_only,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_zambia_parking_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> int:
    return await count_records(
        "zambia-parking",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        credit_only=credit_only,
    )


async def get_zambia_parking_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    credit_only: bool = False,
) -> dict:
    return await totals(
        "zambia-parking",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        credit_only=credit_only,
    )


async def get_zambia_parking_available_years() -> List[int]:
    return await years("zambia-parking")


async def delete_zambia_parking_upload(upload_id: str) -> int:
    return await delete_upload("zambia-parking", upload_id)


async def get_insurance_feed(
    feed_type: str,
    search: str = "",
    month: str = "",
    status: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "month",
    sort_asc: bool = True,
) -> list:
    return await list_records(
        feed_slug(feed_type),
        search=search,
        month=month,
        status=status,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_insurance_feed(
    feed_type: str,
    search: str = "",
    month: str = "",
    status: str = "",
) -> int:
    return await count_records(
        feed_slug(feed_type), search=search, month=month, status=status
    )


async def get_insurance_totals(
    feed_type: str,
    month: str = "",
    status: str = "",
) -> dict:
    return await totals(feed_slug(feed_type), month=month, status=status)


async def get_existing_insurance_keys(feed_type: str, keys: List[str]) -> set:
    return await existing_keys(feed_slug(feed_type), keys)


async def get_diesel_feed(
    feed_type: str,
    search: str = "",
    truck: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list:
    term = (search or truck or "").strip()
    return await list_records(
        feed_slug(feed_type), search=term, limit=limit, skip=skip
    )


async def count_diesel_feed(
    feed_type: str, search: str = "", truck: str = ""
) -> int:
    term = (search or truck or "").strip()
    return await count_records(feed_slug(feed_type), search=term)


async def get_diesel_totals(feed_type: str) -> dict:
    return await totals(feed_slug(feed_type))


async def get_diesel_uploads(feed_type: str) -> list:
    return await list_uploads(feed_slug(feed_type))


async def get_diesel_upload_records(
    feed_type: str,
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await upload_records(
        feed_slug(feed_type),
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_diesel_upload_records(
    feed_type: str, upload_id: str, search: str = ""
) -> int:
    return await upload_count(feed_slug(feed_type), upload_id, search=search)


async def get_diesel_upload_totals(
    feed_type: str, upload_id: str, search: str = ""
) -> dict:
    return await upload_totals(feed_slug(feed_type), upload_id, search=search)


async def delete_diesel_upload(feed_type: str, upload_id: str) -> int:
    return await delete_upload(feed_slug(feed_type), upload_id)


async def get_diesel_file_labels(feed_type: str) -> List[str]:
    payload = await api_client.request(
        "GET", f"v1/feeds/{feed_slug(feed_type)}/file-labels"
    )
    return list((payload or {}).get("items") or [])


async def get_diesel_all_records(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "transaction_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        feed_slug(feed_type),
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        file_labels=file_labels,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_diesel_all_records(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> int:
    return await count_records(
        feed_slug(feed_type),
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        file_labels=file_labels,
    )


async def get_diesel_all_totals(
    feed_type: str,
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    file_labels: Optional[Sequence[str]] = None,
) -> dict:
    return await totals(
        feed_slug(feed_type),
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        file_labels=file_labels,
    )


async def get_diesel_available_years(feed_type: str) -> List[int]:
    return await years(feed_slug(feed_type))


async def find_diesel_uploads_by_content_hash(
    feed_type: str, content_hash: str
) -> List[dict]:
    if not feed_type or not content_hash:
        return []
    payload = await api_client.request(
        "POST",
        f"v1/feeds/{feed_slug(feed_type)}/duplicate-check",
        json={"content_hash": content_hash, "rows": []},
    )
    return [
        m
        for m in ((payload or {}).get("matches") or [])
        if m.get("reason") == "hash" or "content_hash" in m
    ]


async def diesel_filename_already_uploaded(
    feed_type: str,
    source_filename: str = "",
    sheet_label: str = "",
) -> List[dict]:
    if not feed_type or not (source_filename or "").strip():
        return []
    payload = await api_client.request(
        "POST",
        f"v1/feeds/{feed_slug(feed_type)}/duplicate-check",
        json={
            "content_hash": "",
            "source_filename": source_filename,
            "sheet_label": sheet_label,
            "rows": [],
        },
    )
    return [
        m
        for m in ((payload or {}).get("matches") or [])
        if m.get("reason") == "filename"
        or (m.get("source_filename") and not m.get("content_hash"))
    ]


async def diesel_already_uploaded(
    feed_type: str,
    records: List[dict],
    source_filename: str = "",
    sheet_label: str = "",
) -> List[dict]:
    content_hash = diesel_batch_content_hash(records) if records else ""
    payload = await api_client.request(
        "POST",
        f"v1/feeds/{feed_slug(feed_type)}/duplicate-check",
        json={
            "content_hash": content_hash,
            "source_filename": source_filename,
            "sheet_label": sheet_label,
            "rows": [],
        },
    )
    return list((payload or {}).get("matches") or [])


async def kimvi_sheet_exists(sheet_label: str) -> bool:
    found = await sheet_labels("kimvi", [sheet_label])
    return bool(found)


async def kimvi_existing_sheet_labels(labels: List[str]) -> set:
    return await sheet_labels("kimvi", labels)


async def save_kimvi_import(records: list) -> int:
    return await import_rows("kimvi", records)


async def get_kimvi_uploads() -> list:
    return await list_uploads("kimvi")


async def get_kimvi_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    return await upload_records(
        "kimvi",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_kimvi_upload_records(upload_id: str, search: str = "") -> int:
    return await upload_count("kimvi", upload_id, search=search)


async def get_kimvi_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "expense_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "kimvi",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        money_in_only=money_in_only,
        called_out_only=called_out_only,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_kimvi_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> int:
    return await count_records(
        "kimvi",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        money_in_only=money_in_only,
        called_out_only=called_out_only,
    )


async def get_kimvi_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict:
    return await totals(
        "kimvi",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        money_in_only=money_in_only,
        called_out_only=called_out_only,
    )


async def get_kimvi_available_years() -> List[int]:
    return await years("kimvi")


async def delete_kimvi_upload(upload_id: str) -> int:
    return await delete_upload("kimvi", upload_id)


async def congo_sheet_exists(sheet_label: str) -> bool:
    found = await sheet_labels("congo", [sheet_label])
    return bool(found)


async def congo_existing_sheet_labels(labels: List[str]) -> set:
    return await sheet_labels("congo", labels)


async def save_congo_import(records: list) -> int:
    return await import_rows("congo", records)


async def get_congo_uploads() -> list:
    return await list_uploads("congo")


async def get_congo_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    return await upload_records(
        "congo",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_congo_upload_records(upload_id: str, search: str = "") -> int:
    return await upload_count("congo", upload_id, search=search)


async def get_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "expense_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "congo",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        money_in_only=money_in_only,
        called_out_only=called_out_only,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_congo_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> int:
    return await count_records(
        "congo",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        money_in_only=money_in_only,
        called_out_only=called_out_only,
    )


async def get_congo_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict:
    return await totals(
        "congo",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        money_in_only=money_in_only,
        called_out_only=called_out_only,
    )


async def get_congo_available_years() -> List[int]:
    return await years("congo")


async def delete_congo_upload(upload_id: str) -> int:
    return await delete_upload("congo", upload_id)


async def get_afritrack_uploads() -> list:
    return await list_uploads("afritrack")


async def get_afritrack_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "import_date",
    sort_asc: bool = False,
) -> list:
    return await list_records(
        "afritrack",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_afritrack_all_records(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    return await count_records(
        "afritrack",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_afritrack_all_totals(
    search: str = "",
    year: int = 0,
    month: int = 0,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    return await totals(
        "afritrack",
        search=search,
        year=year,
        month=month,
        date_from=date_from,
        date_to=date_to,
    )


async def get_afritrack_available_years() -> List[int]:
    return await years("afritrack")


async def get_afritrack_upload_records(
    upload_id: str,
    search: str = "",
    limit: int = 50,
    skip: int = 0,
    sort_field: str = "row_index",
    sort_asc: bool = True,
) -> list:
    return await upload_records(
        "afritrack",
        upload_id,
        search=search,
        limit=limit,
        skip=skip,
        sort_field=sort_field,
        sort_asc=sort_asc,
    )


async def count_afritrack_upload_records(upload_id: str, search: str = "") -> int:
    return await upload_count("afritrack", upload_id, search=search)


async def get_afritrack_upload_totals(upload_id: str, search: str = "") -> dict:
    return await upload_totals("afritrack", upload_id, search=search)


async def delete_afritrack_upload(upload_id: str) -> int:
    return await delete_upload("afritrack", upload_id)


# ── Skipped import rows ──────────────────────────────────────────────────────


async def save_skipped_import_rows(docs: list) -> int:
    if not docs:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/feeds/skipped",
        json={"rows": [jsonable_document(dict(d)) for d in docs]},
    )
    return int((payload or {}).get("inserted") or 0)


async def list_skipped_import_rows(
    feed_key: str,
    *,
    search: str = "",
    limit: int = 200,
    skip: int = 0,
) -> list:
    payload = await api_client.request(
        "GET",
        "v1/feeds/skipped",
        params=_params(feed_key=feed_key, search=search, limit=limit, skip=skip),
    )
    return _docs(payload)


async def count_skipped_import_rows(feed_key: str, search: str = "") -> int:
    payload = await api_client.request(
        "GET",
        "v1/feeds/skipped/count",
        params=_params(feed_key=feed_key, search=search),
    )
    return int((payload or {}).get("count") or 0)


async def update_skipped_import_truck(doc_id: str, truck_value: str) -> bool:
    payload = await api_client.request(
        "PATCH",
        f"v1/feeds/skipped/{doc_id}",
        json={"truck_value": truck_value},
    )
    return bool((payload or {}).get("ok"))


async def delete_skipped_import_rows(ids: list[str]) -> int:
    if not ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/feeds/skipped/delete",
        json={"ids": list(ids)},
    )
    return int((payload or {}).get("deleted") or 0)


async def reupload_skipped_import_rows(ids: list[str]) -> int:
    if not ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/feeds/skipped/reupload",
        json={"ids": list(ids)},
    )
    return int((payload or {}).get("saved") or 0)
