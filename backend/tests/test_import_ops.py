"""Phase 7.1 — daily/master import helpers."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import BulkWriteError

from app.import_ops import (
    backfill_blank_daily_payees,
    build_issue_query,
    daily_import_row_key,
    daily_uploads_pipeline,
    doc_money_parts,
    insert_many_idempotent,
    month_label,
    normalize_payee_text,
    normalize_receipt,
    null_daily_import_id_filter,
    reset_import_index_cache,
    unpack_daily_uploads_page,
    verified_doc_from_daily_payload,
    verified_doc_from_master_payload,
)


def test_daily_import_row_key_stable() -> None:
    payload = {
        "serial": 12,
        "date": datetime(2026, 3, 1, 10, 0, 0),
        "description": " Diesel ",
        "truck_number": "t 123 abc",
        "amount": 1000,
        "item": "FUEL",
        "lpo_do": "L1",
        "do_number": "D1",
        "currency": "tzs",
    }
    a = daily_import_row_key(payload)
    b = daily_import_row_key(dict(payload))
    assert a == b
    assert len(a) == 40
    payload["amount"] = 1001
    assert daily_import_row_key(payload) != a


def test_month_label_and_receipt() -> None:
    assert month_label(datetime(2026, 10, 9)) == "Oct 26"
    assert normalize_receipt("") == "pending"
    assert normalize_receipt("No Receipt") == "missing"
    assert normalize_receipt("received") == "received"


def test_normalize_payee_text_does_not_force_empty() -> None:
    assert normalize_payee_text(" shell  station ") == "SHELL STATION"
    assert normalize_payee_text("") == ""
    assert normalize_payee_text(None) == ""


def test_verified_doc_from_daily_payload() -> None:
    verified_by = ObjectId()
    now = datetime(2026, 10, 9, 12, 0, 0)
    doc = verified_doc_from_daily_payload(
        {
            "date": "2026-03-01",
            "description": "Fuel",
            "truck_number": "t 1 aa",
            "amount": 500,
            "currency": "tzs",
            "category_name": "DIESEL",
            "payee": "shell station",
            "ref_float": "REFUND TO FLOAT",
            "daily_import_id": "upload-1",
            "daily_import_source": "daily.xlsx",
            "date_discrepancy": True,
            "import_primary_date": "2026-03-02",
        },
        verified_by=verified_by,
        day_order=3,
        now=now,
    )
    assert doc["verified"] is True
    assert doc["verified_by"] == verified_by
    assert doc["cashier_id"] == verified_by
    assert doc["item"] == "DIESEL"
    assert doc["payee"] == "SHELL STATION"
    assert doc["notes_flag"] is True
    assert doc["day_order"] == 3
    assert doc["register_status"] == "submitted"
    assert doc["month"] == "Mar 26"
    assert doc["daily_import_id"] == "upload-1"
    assert doc["date_discrepancy"] is True
    assert doc["truck_sort_key"].startswith("1|") or doc["truck_sort_key"].startswith(
        "2|"
    )
    assert "import_row_key" not in doc


def test_verified_doc_from_master_payload() -> None:
    verified_by = ObjectId()
    doc = verified_doc_from_master_payload(
        {
            "date": datetime(2026, 4, 15),
            "description": "Tyre",
            "truck_number": "T99 XYZ",
            "amount": 200,
            "category_name": "TYRES",
            "payee": "tyre world",
            "serial": 42,
            "memo": "front",
            "notes": "urgent",
            "receipt_raw": "yes",
        },
        upload_id="m-upload",
        source_filename="master.xlsx",
        verified_by=verified_by,
    )
    assert doc["master_import_id"] == "m-upload"
    assert doc["master_import_source"] == "master.xlsx"
    assert doc["master_serial"] == 42
    assert doc["payee"] == "TYRE WORLD"
    assert doc["memo"] == "front | urgent"
    assert doc["receipt_status"] == "received"
    assert doc["verified"] is True
    assert doc["verified_by"] == verified_by


def test_build_issue_query_filters() -> None:
    cashier = ObjectId()
    q = build_issue_query(
        search="fuel",
        truck="T1",
        cashier_id=str(cashier),
        date_from=datetime(2026, 1, 1),
        date_to=datetime(2026, 1, 31),
        item="DIESEL",
    )
    assert q["verified"] == {"$ne": True}
    assert q["rejected"] == {"$ne": True}
    assert q["truck_number"]["$options"] == "i"
    assert q["cashier_id"] == cashier
    assert q["date"]["$gte"] == datetime(2026, 1, 1)
    assert any("possible_duplicate" in str(c) for c in q["$and"])


def test_uploads_pipeline_and_null_filter() -> None:
    pipe = daily_uploads_pipeline(limit=25, skip=10)
    assert pipe[0]["$match"]["daily_import_id"]["$type"] == "string"
    group = pipe[1]["$group"]
    assert "total_tzs" in group
    assert "total_usd" in group
    facet = pipe[-1]["$facet"]
    assert facet["items"] == [{"$skip": 10}, {"$limit": 25}]
    assert facet["meta"] == [{"$count": "total"}]
    filt = null_daily_import_id_filter()
    assert "$or" in filt


def test_unpack_daily_uploads_page() -> None:
    items, total = unpack_daily_uploads_page(
        [{"items": [{"_id": "a"}, {"_id": "b"}], "meta": [{"total": 5}]}]
    )
    assert [r["_id"] for r in items] == ["a", "b"]
    assert total == 5
    assert unpack_daily_uploads_page([]) == ([], 0)
    assert unpack_daily_uploads_page([{"items": [], "meta": []}]) == ([], 0)


def test_doc_money_parts_tzs_usd_and_both() -> None:
    assert doc_money_parts({"amount": -1000, "currency": "TZS"}) == (-1000.0, 0.0)
    assert doc_money_parts({"amount": -50, "currency": "USD"}) == (0.0, -50.0)
    assert doc_money_parts(
        {"amount": -50, "currency": "USD", "amount_usd": -50}
    ) == (0.0, -50.0)
    assert doc_money_parts(
        {"amount": -1000, "currency": "TZS", "amount_usd": -20}
    ) == (-1000.0, -20.0)


@pytest.mark.asyncio
async def test_insert_many_idempotent_counts_dupes() -> None:
    collection = MagicMock()
    collection.insert_many = AsyncMock(
        side_effect=BulkWriteError(
            {
                "nInserted": 2,
                "writeErrors": [
                    {"code": 11000, "index": 2},
                    {"code": 11000, "index": 3},
                ],
            }
        )
    )
    inserted, dupes = await insert_many_idempotent(
        collection, [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}]
    )
    assert inserted == 2
    assert dupes == 2


@pytest.mark.asyncio
async def test_insert_many_idempotent_reraises_other_errors() -> None:
    collection = MagicMock()
    collection.insert_many = AsyncMock(
        side_effect=BulkWriteError(
            {"nInserted": 0, "writeErrors": [{"code": 50, "index": 0}]}
        )
    )
    with pytest.raises(BulkWriteError):
        await insert_many_idempotent(collection, [{"a": 1}])


def test_reset_import_index_cache() -> None:
    reset_import_index_cache()


class _KeyedTransactions:
    def __init__(self) -> None:
        self.documents: list[dict] = []
        self.keys: set[tuple] = set()

    async def create_index(self, *_args, **_kwargs) -> None:
        return None

    async def insert_many(self, docs, ordered: bool = False):
        inserted_ids = []
        errors = []
        for index, doc in enumerate(docs):
            key = (doc.get("daily_import_id"), doc.get("import_row_key"))
            if key in self.keys:
                errors.append({"index": index, "code": 11000})
                continue
            self.keys.add(key)
            self.documents.append(doc)
            inserted_ids.append(index)
        if errors:
            raise BulkWriteError(
                {"nInserted": len(inserted_ids), "writeErrors": errors}
            )
        return type("Result", (), {"inserted_ids": inserted_ids})()

    async def bulk_write(self, ops, ordered: bool = False):
        modified = 0
        for op in ops:
            filt = getattr(op, "_filter", None) or getattr(op, "filter", None) or {}
            doc = getattr(op, "_doc", None) or getattr(op, "doc", None) or {}
            upload_id = filt.get("daily_import_id")
            row_key = filt.get("import_row_key")
            payee = (doc.get("$set") or {}).get("payee")
            if not upload_id or not row_key or not payee:
                continue
            for stored in self.documents:
                if (
                    stored.get("daily_import_id") == upload_id
                    and stored.get("import_row_key") == row_key
                    and not (stored.get("payee") or "").strip()
                ):
                    stored["payee"] = payee
                    modified += 1
        return type("Result", (), {"modified_count": modified})()


class _IndexOnly:
    async def create_index(self, *_args, **_kwargs) -> None:
        return None


@pytest.mark.asyncio
async def test_commit_one_thousand_then_same_keys_insert_zero() -> None:
    from types import SimpleNamespace

    from app.import_ops import commit_daily_to_master

    reset_import_index_cache()
    transactions = _KeyedTransactions()
    db = SimpleNamespace(
        transactions=transactions,
        imported_feeds=_IndexOnly(),
        separate_expenses=_IndexOnly(),
    )
    rows = [
        {
            "date": "2026-03-01T00:00:00",
            "description": f"Fuel {index}",
            "truck_number": "T1 AA",
            "amount": 10 + index,
            "category_name": "DIESEL",
            "daily_import_id": "up-1000",
            "daily_import_source": "daily.xlsx",
        }
        for index in range(1000)
    ]
    first = await commit_daily_to_master(db, rows)
    second = await commit_daily_to_master(db, rows)

    assert first["inserted"] == 1000
    assert first["duplicates_skipped"] == 0
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 1000
    assert len(transactions.documents) == 1000
    assert first["upload_id"] == "up-1000"


@pytest.mark.asyncio
async def test_commit_repush_backfills_blank_payee() -> None:
    """Re-push with mapped payee fills rows that were stored with blank payee."""
    from types import SimpleNamespace

    from app.import_ops import commit_daily_to_master

    reset_import_index_cache()
    transactions = _KeyedTransactions()
    db = SimpleNamespace(
        transactions=transactions,
        imported_feeds=_IndexOnly(),
        separate_expenses=_IndexOnly(),
    )
    blank = {
        "date": "2026-03-01T00:00:00",
        "description": "GET VALVE",
        "truck_number": "T1 AA",
        "amount": 500,
        "category_name": "DIRECT COSTS:SPARES",
        "payee": "",
        "daily_import_id": "up-payee",
        "daily_import_source": "daily.xlsx",
        "serial": 7,
    }
    first = await commit_daily_to_master(db, [blank])
    assert first["inserted"] == 1
    assert transactions.documents[0]["payee"] == ""

    filled = dict(blank)
    filled["payee"] = "spare parts"
    second = await commit_daily_to_master(db, [filled])
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 1
    assert len(transactions.documents) == 1
    assert transactions.documents[0]["payee"] == "SPARE PARTS"


@pytest.mark.asyncio
async def test_backfill_blank_daily_payees_skips_nonblank() -> None:
    collection = MagicMock()
    collection.bulk_write = AsyncMock(
        return_value=type("Result", (), {"modified_count": 1})()
    )
    docs = [
        {
            "daily_import_id": "up-1",
            "import_row_key": "key-1",
            "payee": "PUMA",
        },
        {
            "daily_import_id": "up-1",
            "import_row_key": "",
            "payee": "IGNORED",
        },
    ]
    modified = await backfill_blank_daily_payees(collection, docs)
    assert modified == 1
    collection.bulk_write.assert_awaited_once()
    ops = collection.bulk_write.await_args.args[0]
    assert len(ops) == 1
    assert ops[0]._filter["import_row_key"] == "key-1"
    assert ops[0]._doc["$set"]["payee"] == "PUMA"
