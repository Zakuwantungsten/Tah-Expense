"""Phase 7.1 — daily/master import helpers."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import BulkWriteError

from app.import_ops import (
    build_issue_query,
    daily_import_row_key,
    daily_uploads_pipeline,
    insert_many_idempotent,
    month_label,
    normalize_receipt,
    null_daily_import_id_filter,
    reset_import_index_cache,
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
    pipe = daily_uploads_pipeline(limit=25)
    assert pipe[0]["$match"]["daily_import_id"]["$type"] == "string"
    assert pipe[-1]["$limit"] == 25
    filt = null_daily_import_id_filter()
    assert "$or" in filt


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
