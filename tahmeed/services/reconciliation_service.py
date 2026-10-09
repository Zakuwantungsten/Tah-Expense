"""Reconciliation service — SM Burhani Bonds / RPA via the HTTPS API.

Excel parsing stays on the desktop; persistence and queries go through
``/v1/reconciliation/{table}/...``. Sync helpers for station chips remain local.
"""

from __future__ import annotations

import re
from typing import List, Optional

from bson import ObjectId

from tahmeed.models.reconciliation import ReconciliationEntry
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, jsonable_document

ENTITY = "sm_burhani"
ALL_STATION_SLUG = "all"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def station_query_value(slug: str) -> str:
    """Empty string means no station filter (the All chip)."""
    s = (slug or "").strip()
    return "" if not s or s == ALL_STATION_SLUG else s


def station_display_name(slug: str) -> str:
    s = (slug or "").strip()
    if not s or s == ALL_STATION_SLUG:
        return "All"
    return s.replace("_", " ").title()


def unique_station_docs(docs: List[dict]) -> List[dict]:
    """Keep the first row for each slug so chips are not drawn twice."""
    seen: set = set()
    out: List[dict] = []
    for doc in docs:
        slug = str(doc.get("slug") or "").strip()
        if not slug or slug == ALL_STATION_SLUG or slug in seen:
            continue
        seen.add(slug)
        out.append(doc)
    return out


def unique_stations_in_order(slugs) -> List[str]:
    """First-seen station slugs, skipping blanks and the All chip."""
    seen: set = set()
    out: List[str] = []
    for raw in slugs:
        s = str(raw or "").strip()
        if not s or s == ALL_STATION_SLUG or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def format_station_counts(slugs) -> str:
    """'Nakonde 67, Kasumbalesa 110' in file order."""
    from collections import Counter

    values = [str(s or "").strip() for s in slugs]
    ordered = unique_stations_in_order(values)
    counts = Counter(values)
    return ", ".join(
        f"{station_display_name(slug)} {counts[slug]:,}" for slug in ordered
    )


def _truck_search_clauses(search: str) -> list:
    """Match SM Burhani truck cells; plate queries do not scan other columns.

    Kept for shared desktop tests; server-side search lives in the API.
    """
    from tahmeed.services.import_truck_check import truck_field_search_regex

    text = search.strip()
    plate_rx = truck_field_search_regex(text)
    if plate_rx:
        return [{"truck_and_trailer": {"$regex": plate_rx, "$options": "i"}}]
    rx = {"$regex": re.escape(text), "$options": "i"}
    return [
        {"sm_ref_no": rx},
        {"prn_number": rx},
        {"entry_reg_no": rx},
        {"t1_no": rx},
        {"importer": rx},
        {"exporter": rx},
        {"consignment": rx},
        {"truck_and_trailer": rx},
    ]


def _upload_station_sort_key(doc: dict) -> tuple:
    """Sort key for upload-station aggregates (kept for unit tests)."""
    idx = doc.get("first_idx")
    if isinstance(idx, (int, float)):
        return (0, int(idx), "")
    return (1, 0, str(doc.get("first_id") or ""))


def _entry_from_api(document: dict) -> ReconciliationEntry:
    return ReconciliationEntry.from_doc(desktop_document(document))


def _row_write_body(entry: ReconciliationEntry) -> dict:
    doc = entry.to_doc()
    for key in ("_id", "entity", "table", "dedup_key", "imported_by", "source_index"):
        doc.pop(key, None)
    return jsonable_document(doc)


def _base(table: str) -> str:
    return f"v1/reconciliation/{table}"


# ── Read ────────────────────────────────────────────────────────────────────────

async def get_reconciliation_rows(
    table: str,
    station: str = "",
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> List[ReconciliationEntry]:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/rows",
        params={
            "station": station_query_value(station),
            "search": search,
            "limit": limit,
            "skip": skip,
        },
    )
    return [_entry_from_api(doc) for doc in (payload or {}).get("items") or []]


async def count_reconciliation_rows(
    table: str, station: str = "", search: str = ""
) -> int:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/rows/count",
        params={
            "station": station_query_value(station),
            "search": search,
        },
    )
    return int((payload or {}).get("total") or 0)


async def get_reconciliation_totals(table: str, station: str = "") -> dict:
    """Charge totals + confirmed/disputed counts for the reconciliation summary."""
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/totals",
        params={"station": station_query_value(station)},
    )
    return dict(payload or {})


async def get_recon_uploads(table: str) -> List[dict]:
    """Return one summary doc per import batch for an SM Burhani table type."""
    payload = await api_client.request("GET", f"{_base(table)}/uploads")
    items = []
    for doc in (payload or {}).get("items") or []:
        item = desktop_document(doc) if isinstance(doc, dict) else {}
        # UI still reads upload_doc["_id"] as the upload id.
        upload_id = item.get("upload_id") or item.get("_id")
        if upload_id is not None:
            item["_id"] = upload_id
            item["upload_id"] = upload_id
        item["stations"] = unique_stations_in_order(item.get("stations") or [])
        items.append(item)
    return items


async def get_recon_all_records(
    table: str,
    station: str = "",
    search: str = "",
    year: int = 0,
    month: int = 0,
    limit: int = 50,
    skip: int = 0,
) -> List[ReconciliationEntry]:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/all",
        params={
            "station": station_query_value(station),
            "search": search,
            "year": year,
            "month": month,
            "limit": limit,
            "skip": skip,
        },
    )
    return [_entry_from_api(doc) for doc in (payload or {}).get("items") or []]


async def count_recon_all_records(
    table: str, station: str = "", search: str = "", year: int = 0, month: int = 0
) -> int:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/all/count",
        params={
            "station": station_query_value(station),
            "search": search,
            "year": year,
            "month": month,
        },
    )
    return int((payload or {}).get("total") or 0)


async def get_recon_all_totals(
    table: str, station: str = "", search: str = "", year: int = 0, month: int = 0
) -> dict:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/all/totals",
        params={
            "station": station_query_value(station),
            "search": search,
            "year": year,
            "month": month,
        },
    )
    return dict(payload or {})


async def get_recon_available_years(table: str, station: str = "") -> List[int]:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/years",
        params={"station": station_query_value(station)},
    )
    return [int(y) for y in (payload or {}).get("items") or []]


async def get_recon_upload_stations(upload_id: str, table: str) -> List[dict]:
    """Distinct stations present in one upload, in file order."""
    if not upload_id:
        return []
    payload = await api_client.request(
        "GET", f"{_base(table)}/uploads/{upload_id}/stations"
    )
    return list((payload or {}).get("items") or [])


async def get_recon_upload_records(
    upload_id: str,
    table: str,
    station: str = "",
    search: str = "",
    limit: int = 50,
    skip: int = 0,
) -> List[ReconciliationEntry]:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/uploads/{upload_id}/records",
        params={
            "station": station_query_value(station),
            "search": search,
            "limit": limit,
            "skip": skip,
        },
    )
    return [_entry_from_api(doc) for doc in (payload or {}).get("items") or []]


async def count_recon_upload_records(
    upload_id: str, table: str, station: str = "", search: str = ""
) -> int:
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/uploads/{upload_id}/records/count",
        params={
            "station": station_query_value(station),
            "search": search,
        },
    )
    return int((payload or {}).get("total") or 0)


async def get_recon_upload_totals(
    upload_id: str, table: str, station: str = ""
) -> dict:
    """Charge totals + confirmed/disputed counts for one upload batch."""
    payload = await api_client.request(
        "GET",
        f"{_base(table)}/uploads/{upload_id}/totals",
        params={"station": station_query_value(station)},
    )
    return dict(payload or {})


async def delete_recon_upload(upload_id: str, table: str) -> int:
    payload = await api_client.request(
        "DELETE", f"{_base(table)}/uploads/{upload_id}"
    )
    return int((payload or {}).get("deleted") or 0)


# ── Dedup + write ────────────────────────────────────────────────────────────────

async def get_existing_recon_keys(keys: List[str], table: str) -> set:
    """Return PRN|ENTRY REG keys already stored for this table only."""
    if not keys:
        return set()
    payload = await api_client.request(
        "POST",
        f"{_base(table)}/existing-keys",
        json={"keys": list(keys)},
    )
    return {k for k in (payload or {}).get("keys") or [] if k}


async def save_reconciliation_rows(entries: List[ReconciliationEntry]) -> int:
    if not entries:
        return 0
    table = entries[0].table or "bonds"
    payload = await api_client.request(
        "POST",
        f"{_base(table)}/rows",
        json={"rows": [_row_write_body(e) for e in entries]},
    )
    return int((payload or {}).get("inserted") or 0)


async def set_row_status(
    row_id: ObjectId,
    confirmed: Optional[bool] = None,
    disputed: Optional[bool] = None,
    dispute_note: Optional[str] = None,
    *,
    table: str = "bonds",
) -> bool:
    body: dict = {}
    if confirmed is not None:
        body["confirmed"] = confirmed
    if disputed is not None:
        body["disputed"] = disputed
    if dispute_note is not None:
        body["dispute_note"] = dispute_note
    if not body:
        return False
    payload = await api_client.request(
        "PATCH",
        f"{_base(table)}/rows/{row_id}",
        json=body,
    )
    return bool((payload or {}).get("updated"))


# ── Stations (dynamic tabs) ─────────────────────────────────────────────────────

async def get_recon_stations(table: str = "bonds") -> List[dict]:
    """Return the station list for a table (server seeds Bonds defaults once)."""
    payload = await api_client.request("GET", f"{_base(table)}/stations")
    return unique_station_docs(list((payload or {}).get("items") or []))


async def ensure_recon_stations(table: str, slugs: List[str]) -> None:
    """Create/activate station chips for every station found in an import."""
    ordered = unique_stations_in_order(slugs)
    if not ordered:
        return
    await api_client.request(
        "POST",
        f"{_base(table)}/stations/ensure",
        json={"slugs": ordered},
    )


async def add_recon_station(
    name: str, border_post: str = "", table: str = "bonds"
) -> dict:
    payload = await api_client.request(
        "POST",
        f"{_base(table)}/stations",
        json={"name": name, "border_post": border_post},
    )
    return desktop_document(payload) if isinstance(payload, dict) else {}
