"""Imported feeds HTTP routes (Phase 8)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Callable

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request, status

from .dependencies import require_roles
from .errors import ApiError
from .feed_ops import (
    COLLECTION_SEPARATE,
    DIESEL_SLUGS,
    FeedSpec,
    RECON_SKIPPED_FEED_KEYS,
    SEPARATE_SKIPPED_FEED_KEYS,
    afritrack_all_query,
    afritrack_totals_pipeline,
    afritrack_upload_query,
    afritrack_uploads_pipeline,
    apply_skipped_truck_update,
    collect_existing_feed_keys,
    diesel_all_query,
    diesel_all_totals_pipeline,
    diesel_batch_content_hash,
    diesel_file_labels_pipeline,
    diesel_filename_sheet_query,
    diesel_totals_pipeline,
    diesel_upload_query,
    diesel_uploads_pipeline,
    empty_afritrack_totals,
    empty_diesel_totals,
    empty_insurance_totals,
    empty_parking_totals,
    empty_rahntech_totals,
    empty_toll_totals,
    empty_usd_ledger_totals,
    empty_zambia_totals,
    existing_feed_keys_query,
    feed_collection,
    finalize_diesel_totals,
    finalize_usd_ledger_totals,
    finalize_zambia_totals,
    group_skipped_records_for_reupload,
    insurance_existing_keys_query,
    insurance_query,
    insurance_totals_pipeline,
    ledger_sort_clauses,
    norm_sheet_label,
    parking_congo_all_query,
    parking_congo_deposits_query,
    parking_congo_totals_pipeline,
    parking_congo_upload_query,
    parking_congo_uploads_pipeline,
    prepare_imported_feed_docs,
    prepare_separate_expense_docs,
    prepare_skipped_import_docs,
    rahntech_all_query,
    rahntech_totals_pipeline,
    rahntech_upload_query,
    rahntech_uploads_pipeline,
    skipped_import_query,
    toll_plaza_all_query,
    toll_totals_pipeline,
    toll_upload_query,
    toll_uploads_pipeline,
    unique_diesel_file_labels,
    usd_ledger_all_query,
    usd_ledger_totals_pipeline,
    usd_ledger_upload_query,
    usd_ledger_uploads_pipeline,
    validate_feed,
    years_from_legacy_dates,
    years_from_transaction_dates,
    zambia_batch_content_hash,
    zambia_parking_all_query,
    zambia_parking_totals_pipeline,
    zambia_parking_upload_query,
    zambia_parking_uploads_pipeline,
)
from .import_ops import ensure_import_indexes, insert_many_idempotent
from .reconciliation_ops import entry_document_from_row
from .schemas import (
    DieselDupCheckRequest,
    FeedExistingKeysRequest,
    FeedImportRequest,
    SkippedImportIdsRequest,
    SkippedImportSaveRequest,
    SkippedImportTruckPatch,
    ZambiaContentHashRequest,
    ZambiaSheetLabelsRequest,
)
from .security import utcnow
from .serialization import json_safe, object_id

feeds = APIRouter(prefix="/feeds", tags=["feeds"])
Manager = Annotated[dict[str, Any], Depends(require_roles("admin", "accountant"))]

QueryBuilder = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class LedgerHandler:
    all_query: QueryBuilder
    upload_query: Callable[[str, str], dict[str, Any]]
    uploads_pipeline: Callable[[], list[dict[str, Any]]]
    totals_pipeline: Callable[[dict[str, Any]], list[dict[str, Any]]]
    empty_totals: Callable[[], dict[str, Any]]
    all_tie_break: tuple[tuple[str, int], ...]
    upload_default_sort: str
    legacy_date_field: str
    finalize_totals: Callable[[dict[str, Any] | None], dict[str, Any]] | None = None
    supports_credit_only: bool = False
    supports_file_labels: bool = False
    supports_money_filters: bool = False
    is_diesel: bool = False
    is_usd_ledger: bool = False
    years_on_import_date: bool = False
    feed_type: str = ""


def _diesel_handler(feed_type: str) -> LedgerHandler:
    return LedgerHandler(
        all_query=lambda **kw: diesel_all_query(
            feed_type,
            search=kw.get("search", ""),
            year=kw.get("year", 0),
            month=kw.get("month", 0),
            date_from=kw.get("date_from"),
            date_to=kw.get("date_to"),
            file_labels=kw.get("file_labels"),
        ),
        upload_query=lambda uid, search: diesel_upload_query(
            feed_type, uid, search
        ),
        uploads_pipeline=lambda: diesel_uploads_pipeline(feed_type),
        totals_pipeline=diesel_totals_pipeline,
        empty_totals=empty_diesel_totals,
        all_tie_break=(("date", -1), ("import_date", -1)),
        upload_default_sort="date",
        legacy_date_field="date",
        supports_file_labels=True,
        is_diesel=True,
        feed_type=feed_type,
    )


_HANDLERS: dict[str, LedgerHandler] = {
    "toll-plaza": LedgerHandler(
        all_query=toll_plaza_all_query,
        upload_query=toll_upload_query,
        uploads_pipeline=toll_uploads_pipeline,
        totals_pipeline=toll_totals_pipeline,
        empty_totals=empty_toll_totals,
        all_tie_break=(("toll_date", -1), ("import_date", -1)),
        upload_default_sort="toll_date",
        legacy_date_field="toll_date",
    ),
    "parking-congo": LedgerHandler(
        all_query=parking_congo_all_query,
        upload_query=parking_congo_upload_query,
        uploads_pipeline=parking_congo_uploads_pipeline,
        totals_pipeline=parking_congo_totals_pipeline,
        empty_totals=empty_parking_totals,
        all_tie_break=(("payment_date", -1), ("import_date", -1)),
        upload_default_sort="payment_date",
        legacy_date_field="payment_date",
    ),
    "rahntech": LedgerHandler(
        all_query=rahntech_all_query,
        upload_query=rahntech_upload_query,
        uploads_pipeline=rahntech_uploads_pipeline,
        totals_pipeline=rahntech_totals_pipeline,
        empty_totals=empty_rahntech_totals,
        all_tie_break=(("sales_date", -1), ("import_date", -1)),
        upload_default_sort="sales_date",
        legacy_date_field="sales_date",
    ),
    "zambia-parking": LedgerHandler(
        all_query=zambia_parking_all_query,
        upload_query=zambia_parking_upload_query,
        uploads_pipeline=zambia_parking_uploads_pipeline,
        totals_pipeline=zambia_parking_totals_pipeline,
        empty_totals=empty_zambia_totals,
        all_tie_break=(("date", -1), ("import_date", -1)),
        upload_default_sort="row_index",
        legacy_date_field="date",
        finalize_totals=finalize_zambia_totals,
        supports_credit_only=True,
    ),
    "diesel-infinity": _diesel_handler("diesel_infinity"),
    "diesel-lake-zambia": _diesel_handler("diesel_lake_zambia"),
    "diesel-lake-tunduma": _diesel_handler("diesel_lake_tunduma"),
    "diesel-gbp": _diesel_handler("diesel_gbp"),
}


def _usd_handler(expense_type: str) -> LedgerHandler:
    return LedgerHandler(
        all_query=lambda **kw: usd_ledger_all_query(
            expense_type,
            search=kw.get("search", ""),
            year=kw.get("year", 0),
            month=kw.get("month", 0),
            date_from=kw.get("date_from"),
            date_to=kw.get("date_to"),
            money_in_only=kw.get("money_in_only", False),
            called_out_only=kw.get("called_out_only", False),
        ),
        upload_query=lambda uid, search: usd_ledger_upload_query(
            expense_type, uid, search
        ),
        uploads_pipeline=lambda: usd_ledger_uploads_pipeline(expense_type),
        totals_pipeline=usd_ledger_totals_pipeline,
        empty_totals=empty_usd_ledger_totals,
        all_tie_break=(("date_str", -1), ("created_at", -1)),
        upload_default_sort="row_index",
        legacy_date_field="date_str",
        finalize_totals=finalize_usd_ledger_totals,
        supports_money_filters=True,
        is_usd_ledger=True,
        feed_type=expense_type,
    )


_HANDLERS["kimvi"] = _usd_handler("ahmed_kimvi")
_HANDLERS["congo"] = _usd_handler("congo_expenses")
_HANDLERS["afritrack"] = LedgerHandler(
    all_query=afritrack_all_query,
    upload_query=afritrack_upload_query,
    uploads_pipeline=afritrack_uploads_pipeline,
    totals_pipeline=afritrack_totals_pipeline,
    empty_totals=empty_afritrack_totals,
    all_tie_break=(("period", -1), ("row_index", 1)),
    upload_default_sort="row_index",
    legacy_date_field="import_date",
    years_on_import_date=True,
)

_INSURANCE_SLUGS = frozenset({"comesa", "third-party"})
_USD_SHEET_SLUGS = frozenset({"kimvi", "congo"})


def _database(request: Request) -> Any:
    return request.app.state.database.db


def _feed(feed: str) -> FeedSpec:
    try:
        return validate_feed(feed)
    except ValueError as exc:
        raise ApiError(422, "invalid_feed", str(exc)) from None


def _handler(spec: FeedSpec) -> LedgerHandler:
    h = _HANDLERS.get(spec.slug)
    if h is None:
        raise ApiError(
            501,
            "feed_not_implemented",
            f"Feed '{spec.slug}' not implemented yet",
        )
    return h


def _is_insurance(spec: FeedSpec) -> bool:
    return spec.slug in _INSURANCE_SLUGS


def _require_uploads(spec: FeedSpec) -> None:
    if not spec.has_uploads:
        raise ApiError(
            422,
            "uploads_unsupported",
            f"Feed '{spec.slug}' does not use upload batches",
        )


async def _audit(
    request: Request,
    *,
    action: str,
    actor: dict[str, Any],
    feed: str,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        await _database(request).operation_events.insert_one(
            {
                "ts": utcnow(),
                "action": action,
                "actor_id": str(actor.get("_id") or ""),
                "actor_username": actor.get("username"),
                "actor_role": actor.get("role"),
                "entity_type": "feed_import",
                "entity_ids": [feed],
                "outcome": "ok",
                "error": None,
                "request_id": getattr(request.state, "request_id", None)
                or uuid.uuid4().hex,
                "client": "api",
                "details": details or {},
            }
        )
    except Exception:
        pass


def _all_query(
    handler: LedgerHandler,
    *,
    search: str,
    year: int,
    month: int,
    date_from: datetime | None,
    date_to: datetime | None,
    credit_only: bool,
    file_labels: list[str] | None = None,
    money_in_only: bool = False,
    called_out_only: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "search": search,
        "year": year,
        "month": month,
        "date_from": date_from,
        "date_to": date_to,
    }
    if handler.supports_credit_only:
        kwargs["credit_only"] = credit_only
    if handler.supports_file_labels:
        kwargs["file_labels"] = file_labels
    if handler.supports_money_filters:
        kwargs["money_in_only"] = money_in_only
        kwargs["called_out_only"] = called_out_only
    return handler.all_query(**kwargs)


def _parse_file_labels(raw: str | None) -> list[str]:
    if not raw:
        return []
    return unique_diesel_file_labels(
        [p.strip() for p in raw.split(",") if p.strip()]
    )


def _parse_object_ids(raw_ids: list[str]) -> list[ObjectId]:
    oids: list[ObjectId] = []
    for raw in raw_ids:
        try:
            oids.append(object_id(str(raw).strip()))
        except ValueError:
            continue
    return oids


def _valid_skipped_id(value: str) -> ObjectId:
    try:
        return object_id(value)
    except ValueError:
        raise ApiError(422, "invalid_id", "Resource id is invalid") from None


# --- Skipped import rows (static paths before /{feed}/...) --------------------


@feeds.post("/skipped", status_code=status.HTTP_201_CREATED)
async def save_skipped_rows(
    body: SkippedImportSaveRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    if not body.rows:
        return {"inserted": 0}
    payload = prepare_skipped_import_docs(body.rows)
    result = await _database(request).skipped_import_rows.insert_many(
        payload, ordered=False
    )
    await _audit(
        request,
        action="feed.skipped.save",
        actor=manager,
        feed="skipped",
        details={"inserted": len(result.inserted_ids)},
    )
    return {"inserted": len(result.inserted_ids)}


@feeds.get("/skipped/count")
async def count_skipped_rows(
    request: Request,
    manager: Manager,
    feed_key: str = Query(..., min_length=1),
    search: str = Query(""),
) -> dict[str, Any]:
    del manager
    query = skipped_import_query(feed_key, search)
    count = await _database(request).skipped_import_rows.count_documents(query)
    return {"count": int(count)}


@feeds.get("/skipped")
async def list_skipped_rows(
    request: Request,
    manager: Manager,
    feed_key: str = Query(..., min_length=1),
    search: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
) -> list[dict[str, Any]]:
    del manager
    query = skipped_import_query(feed_key, search)
    cursor = (
        _database(request)
        .skipped_import_rows.find(query)
        .sort([("skipped_at", -1), ("source_row", 1)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return json_safe(docs)


@feeds.patch("/skipped/{doc_id}")
async def patch_skipped_truck(
    doc_id: str,
    body: SkippedImportTruckPatch,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    oid = _valid_skipped_id(doc_id)
    coll = _database(request).skipped_import_rows
    doc = await coll.find_one({"_id": oid})
    if not doc:
        raise ApiError(404, "not_found", "Skipped import row not found")
    fields = apply_skipped_truck_update(doc, body.truck_value)
    result = await coll.update_one({"_id": oid}, {"$set": fields})
    return {
        "ok": True,
        "matched": int(result.matched_count),
        "modified": int(result.modified_count),
    }


@feeds.post("/skipped/delete")
async def delete_skipped_rows(
    body: SkippedImportIdsRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    oids = _parse_object_ids(body.ids)
    if not oids:
        return {"deleted": 0}
    result = await _database(request).skipped_import_rows.delete_many(
        {"_id": {"$in": oids}}
    )
    await _audit(
        request,
        action="feed.skipped.delete",
        actor=manager,
        feed="skipped",
        details={"deleted": int(result.deleted_count)},
    )
    return {"deleted": int(result.deleted_count)}


@feeds.post("/skipped/reupload")
async def reupload_skipped_rows(
    body: SkippedImportIdsRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    oids = _parse_object_ids(body.ids)
    if not oids:
        return {"saved": 0}
    db = _database(request)
    docs = await db.skipped_import_rows.find(
        {"_id": {"$in": oids}}
    ).to_list(length=None)
    if not docs:
        return {"saved": 0}

    await ensure_import_indexes(db)
    grouped = group_skipped_records_for_reupload(docs)
    saved = 0
    actor = manager.get("_id")
    now = utcnow()
    if hasattr(now, "tzinfo") and now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    for feed_key, records in grouped.items():
        if not records:
            continue
        if feed_key in RECON_SKIPPED_FEED_KEYS:
            default_table = (
                "rpa_schedule" if feed_key == "rpa_schedule" else "bonds"
            )
            recon_docs = []
            for index, rec in enumerate(records):
                row = dict(rec)
                table = str(row.get("table") or default_table)
                if table not in ("bonds", "rpa_schedule"):
                    table = default_table
                recon_docs.append(
                    entry_document_from_row(
                        table,
                        row,
                        source_index=index,
                        imported_by=actor if isinstance(actor, ObjectId) else None,
                        now=now,
                    )
                )
            if recon_docs:
                result = await db.reconciliation_entries.insert_many(
                    recon_docs, ordered=False
                )
                saved += len(result.inserted_ids)
        elif feed_key in SEPARATE_SKIPPED_FEED_KEYS:
            stamped = prepare_separate_expense_docs(
                records, expense_type=feed_key
            )
            inserted, _ = await insert_many_idempotent(
                db.separate_expenses, stamped
            )
            saved += inserted
        else:
            stamped = prepare_imported_feed_docs(records)
            inserted, _ = await insert_many_idempotent(
                db.imported_feeds, stamped
            )
            saved += inserted

    await db.skipped_import_rows.delete_many({"_id": {"$in": oids}})
    await _audit(
        request,
        action="feed.skipped.reupload",
        actor=manager,
        feed="skipped",
        details={"saved": saved, "requested": len(oids)},
    )
    return {"saved": saved}


# --- Records ------------------------------------------------------------------


@feeds.get("/{feed}/records")
async def list_feed_records(
    feed: str,
    request: Request,
    manager: Manager,
    search: str = Query(""),
    year: int = Query(0, ge=0),
    month: str = Query(""),
    status: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    credit_only: bool = Query(False),
    money_in_only: bool = Query(False),
    called_out_only: bool = Query(False),
    file_labels: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    sort_field: str = Query(""),
    sort_asc: bool = Query(False),
) -> list[dict[str, Any]]:
    del manager
    spec = _feed(feed)
    coll = feed_collection(_database(request), spec)

    if _is_insurance(spec):
        query = insurance_query(
            spec.disc_value, search=search, month=month, status=status
        )
        sort_clauses = ledger_sort_clauses(
            sort_field or "month",
            sort_asc if sort_field else True,
            spec.sort_allowed,
            default="month",
            tie_break=(("name", 1),),
        )
    else:
        handler = _handler(spec)
        month_i = int(month) if str(month).isdigit() else 0
        query = _all_query(
            handler,
            search=search,
            year=year,
            month=month_i,
            date_from=date_from,
            date_to=date_to,
            credit_only=credit_only,
            file_labels=_parse_file_labels(file_labels),
            money_in_only=money_in_only,
            called_out_only=called_out_only,
        )
        sort_clauses = ledger_sort_clauses(
            sort_field or spec.default_sort,
            sort_asc,
            spec.sort_allowed,
            default=spec.default_sort,
            tie_break=handler.all_tie_break,
        )

    cursor = coll.find(query).sort(sort_clauses).skip(skip).limit(limit)
    return json_safe(await cursor.to_list(length=limit))


@feeds.get("/{feed}/records/count")
async def count_feed_records(
    feed: str,
    request: Request,
    manager: Manager,
    search: str = Query(""),
    year: int = Query(0, ge=0),
    month: str = Query(""),
    status: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    credit_only: bool = Query(False),
    money_in_only: bool = Query(False),
    called_out_only: bool = Query(False),
    file_labels: str = Query(""),
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    if _is_insurance(spec):
        query = insurance_query(
            spec.disc_value,
            search=search,
            month=month,
            status=status,
        )
    else:
        handler = _handler(spec)
        month_i = int(month) if str(month).isdigit() else 0
        query = _all_query(
            handler,
            search=search,
            year=year,
            month=month_i,
            date_from=date_from,
            date_to=date_to,
            credit_only=credit_only,
            file_labels=_parse_file_labels(file_labels),
            money_in_only=money_in_only,
            called_out_only=called_out_only,
        )
    count = await feed_collection(_database(request), spec).count_documents(query)
    return {"count": int(count)}


@feeds.get("/{feed}/totals")
async def feed_totals(
    feed: str,
    request: Request,
    manager: Manager,
    search: str = Query(""),
    year: int = Query(0, ge=0),
    month: str = Query(""),
    status: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    credit_only: bool = Query(False),
    money_in_only: bool = Query(False),
    called_out_only: bool = Query(False),
    file_labels: str = Query(""),
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    coll = feed_collection(_database(request), spec)

    if _is_insurance(spec):
        query = insurance_query(
            spec.disc_value, month=month, status=status
        )
        result = await coll.aggregate(insurance_totals_pipeline(query)).to_list(1)
        return json_safe(result[0] if result else empty_insurance_totals())

    handler = _handler(spec)
    month_i = int(month) if str(month).isdigit() else 0
    query = _all_query(
        handler,
        search=search,
        year=year,
        month=month_i,
        date_from=date_from,
        date_to=date_to,
        credit_only=credit_only,
        file_labels=_parse_file_labels(file_labels),
        money_in_only=money_in_only,
        called_out_only=called_out_only,
    )
    if handler.is_diesel:
        result = await coll.aggregate(diesel_all_totals_pipeline(query)).to_list(1)
        return json_safe(
            finalize_diesel_totals(
                result[0] if result else None, feed_type=handler.feed_type
            )
        )
    result = await coll.aggregate(handler.totals_pipeline(query)).to_list(1)
    row = result[0] if result else None
    if handler.finalize_totals:
        return json_safe(handler.finalize_totals(row))
    return json_safe(row if row else handler.empty_totals())


@feeds.get("/{feed}/years")
async def feed_years(
    feed: str,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    if _is_insurance(spec):
        raise ApiError(
            422,
            "years_unsupported",
            f"Feed '{spec.slug}' does not expose calendar years",
        )
    handler = _handler(spec)
    coll = feed_collection(_database(request), spec)
    years: set[int] = set()
    if handler.years_on_import_date:
        date_field = "import_date"
    elif handler.is_usd_ledger:
        date_field = "expense_date"
    else:
        date_field = "transaction_date"
    pipeline = [
        {
            "$match": {
                spec.disc_field: spec.disc_value,
                date_field: {"$exists": True, "$ne": None},
            }
        },
        {"$group": {"_id": {"$year": f"${date_field}"}}},
    ]
    years.update(
        years_from_transaction_dates(
            await coll.aggregate(pipeline).to_list(length=None)
        )
    )
    if not handler.years_on_import_date:
        legacy = await coll.distinct(
            handler.legacy_date_field,
            {
                spec.disc_field: spec.disc_value,
                "$or": [
                    {date_field: {"$exists": False}},
                    {date_field: None},
                ],
            },
        )
        years.update(years_from_legacy_dates(legacy))
    return {"items": sorted(years, reverse=True)}


# --- Parking Congo deposits (static paths before /{feed}/...) -----------------


@feeds.get("/parking-congo/deposits")
async def list_parking_congo_deposits(
    request: Request,
    manager: Manager,
    search: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    del manager
    query = parking_congo_deposits_query(search, date_from, date_to)
    cursor = (
        _database(request)
        .imported_feeds.find(query)
        .sort(
            [
                ("transaction_date", -1),
                ("payment_date", -1),
                ("import_date", -1),
            ]
        )
        .skip(skip)
        .limit(limit)
    )
    return json_safe(await cursor.to_list(length=limit))


@feeds.get("/parking-congo/deposits/count")
async def count_parking_congo_deposits(
    request: Request,
    manager: Manager,
    search: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
) -> dict[str, Any]:
    del manager
    query = parking_congo_deposits_query(search, date_from, date_to)
    count = await _database(request).imported_feeds.count_documents(query)
    return {"count": int(count)}


@feeds.get("/parking-congo/deposits/totals")
async def parking_congo_deposit_totals(
    request: Request,
    manager: Manager,
    search: str = Query(""),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
) -> dict[str, Any]:
    del manager
    query = parking_congo_deposits_query(search, date_from, date_to)
    result = await _database(request).imported_feeds.aggregate(
        parking_congo_totals_pipeline(query)
    ).to_list(1)
    return json_safe(result[0] if result else empty_parking_totals())


# --- Zambia duplicate checks --------------------------------------------------


@feeds.post("/zambia-parking/sheet-exists")
async def zambia_sheet_exists(
    body: ZambiaSheetLabelsRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    label = (body.labels[0] if body.labels else "").strip()
    if not label:
        return {"exists": False}
    count = await _database(request).imported_feeds.count_documents(
        {
            "feed_type": "zambia_parking",
            "sheet_label": {
                "$regex": f"^{re.escape(label)}$",
                "$options": "i",
            },
        }
    )
    return {"exists": count > 0}


@feeds.post("/zambia-parking/existing-sheet-labels")
async def zambia_existing_sheet_labels(
    body: ZambiaSheetLabelsRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    clean = [str(l).strip() for l in body.labels if l and str(l).strip()]
    if not clean:
        return {"labels": []}
    clauses = [
        {
            "sheet_label": {
                "$regex": f"^{re.escape(l)}$",
                "$options": "i",
            }
        }
        for l in clean
    ]
    found = await _database(request).imported_feeds.distinct(
        "sheet_label",
        {"feed_type": "zambia_parking", "$or": clauses},
    )
    found_upper = {norm_sheet_label(s) for s in found if s}
    return {
        "labels": [l for l in clean if norm_sheet_label(l) in found_upper]
    }


@feeds.post("/zambia-parking/content-hash-matches")
async def zambia_content_hash_matches(
    body: ZambiaContentHashRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    content_hash = (body.content_hash or "").strip()
    if not content_hash and body.rows:
        content_hash = zambia_batch_content_hash(body.rows)
    if not content_hash:
        return {"matches": []}
    ids = await _database(request).imported_feeds.distinct(
        "upload_id",
        {"feed_type": "zambia_parking", "content_hash": content_hash},
    )
    matches = []
    for uid in ids:
        key = str(uid or "").strip()
        if key:
            matches.append({"upload_id": key, "content_hash": content_hash})
    return {"matches": matches}


# --- Uploads ------------------------------------------------------------------


@feeds.get("/{feed}/uploads")
async def list_feed_uploads(
    feed: str,
    request: Request,
    manager: Manager,
) -> list[dict[str, Any]]:
    del manager
    spec = _feed(feed)
    _require_uploads(spec)
    handler = _handler(spec)
    rows = await feed_collection(_database(request), spec).aggregate(
        handler.uploads_pipeline()
    ).to_list(length=None)
    return json_safe(rows)


@feeds.get("/{feed}/uploads/{upload_id}/records")
async def list_upload_records(
    feed: str,
    upload_id: str,
    request: Request,
    manager: Manager,
    search: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    sort_field: str = Query(""),
    sort_asc: bool = Query(True),
) -> list[dict[str, Any]]:
    del manager
    spec = _feed(feed)
    _require_uploads(spec)
    handler = _handler(spec)
    query = handler.upload_query(upload_id, search)
    sort_clauses = ledger_sort_clauses(
        sort_field or handler.upload_default_sort,
        sort_asc,
        spec.sort_allowed,
        default=handler.upload_default_sort,
        tie_break=(("import_date", -1),),
    )
    cursor = (
        feed_collection(_database(request), spec)
        .find(query)
        .sort(sort_clauses)
        .skip(skip)
        .limit(limit)
    )
    return json_safe(await cursor.to_list(length=limit))


@feeds.get("/{feed}/uploads/{upload_id}/records/count")
async def count_upload_records(
    feed: str,
    upload_id: str,
    request: Request,
    manager: Manager,
    search: str = Query(""),
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    _require_uploads(spec)
    handler = _handler(spec)
    query = handler.upload_query(upload_id, search)
    count = await feed_collection(_database(request), spec).count_documents(query)
    return {"count": int(count)}


@feeds.get("/{feed}/uploads/{upload_id}/totals")
async def upload_totals(
    feed: str,
    upload_id: str,
    request: Request,
    manager: Manager,
    search: str = Query(""),
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    _require_uploads(spec)
    handler = _handler(spec)
    query = handler.upload_query(upload_id, search)
    result = await feed_collection(_database(request), spec).aggregate(
        handler.totals_pipeline(query)
    ).to_list(1)
    row = result[0] if result else None
    if handler.finalize_totals:
        return json_safe(handler.finalize_totals(row))
    return json_safe(row if row else handler.empty_totals())


@feeds.delete("/{feed}/uploads/{upload_id}")
async def delete_feed_upload(
    feed: str,
    upload_id: str,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    spec = _feed(feed)
    _require_uploads(spec)
    _handler(spec)  # ensure implemented
    uid = (upload_id or "").strip()
    if not uid:
        return {"deleted": 0}
    result = await feed_collection(_database(request), spec).delete_many(
        {spec.disc_field: spec.disc_value, "upload_id": uid}
    )
    deleted = int(result.deleted_count or 0)
    await _audit(
        request,
        action="feed.upload_delete",
        actor=manager,
        feed=spec.slug,
        details={"upload_id": uid, "deleted": deleted},
    )
    return {"deleted": deleted}


# --- Existing keys + import ---------------------------------------------------


@feeds.get("/{feed}/file-labels")
async def list_file_labels(
    feed: str,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    if spec.slug not in DIESEL_SLUGS:
        raise ApiError(
            422,
            "file_labels_unsupported",
            f"Feed '{spec.slug}' does not expose file labels",
        )
    docs = await feed_collection(_database(request), spec).aggregate(
        diesel_file_labels_pipeline(spec.disc_value)
    ).to_list(length=None)
    labels = unique_diesel_file_labels(str(d.get("_id") or "") for d in docs)
    return {"items": labels}


@feeds.post("/{feed}/duplicate-check")
async def diesel_duplicate_check(
    feed: str,
    body: DieselDupCheckRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    if spec.slug not in DIESEL_SLUGS:
        raise ApiError(
            422,
            "dup_check_unsupported",
            f"Feed '{spec.slug}' does not support diesel duplicate checks",
        )
    matches: list[dict[str, Any]] = []
    content_hash = (body.content_hash or "").strip()
    if not content_hash and body.rows:
        content_hash = diesel_batch_content_hash(body.rows)
    coll = feed_collection(_database(request), spec)
    if content_hash:
        ids = await coll.distinct(
            "upload_id",
            {"feed_type": spec.disc_value, "content_hash": content_hash},
        )
        for uid in ids:
            key = str(uid or "").strip()
            if key:
                matches.append(
                    {"upload_id": key, "content_hash": content_hash, "reason": "hash"}
                )
    fname_q = diesel_filename_sheet_query(
        spec.disc_value, body.source_filename, body.sheet_label
    )
    if fname_q:
        ids = await coll.distinct("upload_id", fname_q)
        seen = {m["upload_id"] for m in matches}
        for uid in ids:
            key = str(uid or "").strip()
            if key and key not in seen:
                matches.append(
                    {
                        "upload_id": key,
                        "source_filename": body.source_filename,
                        "sheet_label": body.sheet_label,
                        "reason": "filename",
                    }
                )
    return {"matches": matches}


@feeds.post("/{feed}/existing-keys")
async def existing_keys(
    feed: str,
    body: FeedExistingKeysRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    if not body.keys:
        return {"keys": []}
    coll = feed_collection(_database(request), spec)
    if _is_insurance(spec):
        docs = await coll.find(
            insurance_existing_keys_query(spec.disc_value, body.keys),
            {"dedup_id": 1},
        ).to_list(length=None)
        found = {str(d["dedup_id"]) for d in docs if d.get("dedup_id")}
        return {"keys": sorted(found)}
    cursor = coll.find(
        existing_feed_keys_query(body.keys),
        {
            "receipt_no": 1,
            "serial": 1,
            "ticket_no": 1,
            "lpo_no": 1,
            "ledger_id": 1,
            "trip_number": 1,
        },
    )
    docs = await cursor.to_list(length=None)
    return {"keys": sorted(collect_existing_feed_keys(docs))}


@feeds.post("/{feed}/sheet-labels")
async def existing_sheet_labels(
    feed: str,
    body: ZambiaSheetLabelsRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    del manager
    spec = _feed(feed)
    if spec.slug not in _USD_SHEET_SLUGS and spec.slug != "zambia-parking":
        raise ApiError(
            422,
            "sheet_labels_unsupported",
            f"Feed '{spec.slug}' does not use sheet labels",
        )
    clean = [str(l).strip() for l in body.labels if l and str(l).strip()]
    if not clean:
        return {"labels": []}
    clauses = [
        {
            "sheet_label": {
                "$regex": f"^{re.escape(l)}$",
                "$options": "i",
            }
        }
        for l in clean
    ]
    found = await feed_collection(_database(request), spec).distinct(
        "sheet_label",
        {spec.disc_field: spec.disc_value, "$or": clauses},
    )
    found_upper = {norm_sheet_label(s) for s in found if s}
    return {
        "labels": [l for l in clean if norm_sheet_label(l) in found_upper]
    }


@feeds.post("/{feed}/import", status_code=status.HTTP_201_CREATED)
async def import_feed_rows(
    feed: str,
    body: FeedImportRequest,
    request: Request,
    manager: Manager,
) -> dict[str, Any]:
    spec = _feed(feed)
    if not body.rows:
        return {"inserted": 0, "duplicates_skipped": 0}

    db = _database(request)
    await ensure_import_indexes(db)
    coll = feed_collection(db, spec)
    if spec.collection == COLLECTION_SEPARATE:
        stamped = prepare_separate_expense_docs(
            body.rows, expense_type=spec.disc_value
        )
    else:
        stamped = prepare_imported_feed_docs(body.rows)
        for doc in stamped:
            doc[spec.disc_field] = spec.disc_value

    inserted, dupes = await insert_many_idempotent(coll, stamped)
    await _audit(
        request,
        action="feed.import",
        actor=manager,
        feed=spec.slug,
        details={"inserted": inserted, "duplicates_skipped": dupes},
    )
    return {"inserted": inserted, "duplicates_skipped": dupes}
