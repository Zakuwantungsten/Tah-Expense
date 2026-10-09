"""Phase 8.1 — feed registry and shared helpers."""

from datetime import datetime

import pytest

from app.feed_ops import (
    apply_skipped_truck_update,
    collect_existing_feed_keys,
    feed_search_clause,
    group_skipped_records_for_reupload,
    ledger_sort_clauses,
    parking_congo_all_query,
    parking_congo_deposits_query,
    prepare_imported_feed_doc,
    skipped_import_query,
    toll_date_filter,
    toll_plaza_all_query,
    toll_uploads_pipeline,
    validate_feed,
)


def test_validate_feed_registry() -> None:
    toll = validate_feed("toll-plaza")
    assert toll.disc_value == "toll_plaza"
    assert toll.collection == "imported_feeds"
    assert toll.truck_field == "vehicle_reg"
    assert validate_feed("kimvi").collection == "separate_expenses"
    assert validate_feed("diesel-lake-zambia").disc_value == "diesel_lake_zambia"
    with pytest.raises(ValueError):
        validate_feed("nope")


def test_feed_search_plate_only_hits_truck_field() -> None:
    clause = feed_search_clause("T103 DVL", ("vehicle_reg",), ("toll_plaza",))
    assert clause is not None
    assert "vehicle_reg" in clause
    assert "toll_plaza" not in str(clause)

    text = feed_search_clause("nakonde", ("vehicle_reg",), ("toll_plaza",))
    assert text is not None
    assert "$or" in text or "toll_plaza" in text


def test_toll_plaza_all_query_year_and_search() -> None:
    q = toll_plaza_all_query(search="T1 AA", year=2026, month=3)
    assert "$and" in q
    assert any("feed_type" in str(c) for c in q["$and"])


def test_toll_date_filter_month() -> None:
    f = toll_date_filter(2026, 4)
    assert "$or" in f
    td = f["$or"][0]["transaction_date"]
    assert td["$gte"] == datetime(2026, 4, 1)
    assert td["$lt"] == datetime(2026, 5, 1)


def test_ledger_sort_uses_truck_sort_key() -> None:
    clauses = ledger_sort_clauses(
        "vehicle_reg",
        True,
        frozenset({"vehicle_reg", "toll_date"}),
        default="toll_date",
    )
    assert clauses[0][0] == "truck_sort_key"
    assert clauses[1] == ("vehicle_reg", 1)


def test_prepare_toll_doc_normalizes_date() -> None:
    now = datetime(2026, 10, 9, 12, 0, 0)
    doc = prepare_imported_feed_doc(
        {
            "feed_type": "toll_plaza",
            "toll_date": "01 Mar 2026",
            "vehicle_reg": "t 103 dvl",
            "tender_amount": "100",
            "toll_plaza": "nakonde",
            "_raw": {"x": 1},
        },
        now=now,
    )
    assert "_raw" not in doc
    assert doc["import_date"] == now
    assert isinstance(doc["transaction_date"], datetime)
    assert doc["vehicle_reg"] == "T 103 DVL"
    assert doc["toll_plaza"] == "NAKONDE"
    assert doc["truck_sort_key"].startswith("1|") or doc["truck_sort_key"].startswith(
        "2|"
    )


def test_prepare_parking_deposit_flags() -> None:
    doc = prepare_imported_feed_doc(
        {
            "feed_type": "parking_congo",
            "payment_date": "2026-05-01",
            "transaction_type": "deposit",
            "vehicle_no": "-",
            "direction": "n/a",
        },
        now=datetime(2026, 10, 9),
    )
    assert doc["is_deposit"] is True
    assert doc["vehicle_no"] == ""
    assert doc["direction"] == ""


def test_collect_existing_keys_and_uploads_pipeline() -> None:
    keys = collect_existing_feed_keys(
        [{"receipt_no": "R1", "serial": ""}, {"ticket_no": "T9"}]
    )
    assert keys == {"R1", "T9"}
    pipe = toll_uploads_pipeline()
    assert pipe[0]["$match"]["feed_type"] == "toll_plaza"


def test_parking_congo_queries() -> None:
    q = parking_congo_all_query(search="T1", year=2026, month=5)
    assert "$and" in q
    dep = parking_congo_deposits_query(search="L1")
    assert dep["$and"][0]["feed_type"] == "parking_congo"


def test_skipped_import_helpers() -> None:
    q = skipped_import_query("toll_plaza", search="T103")
    assert q["feed_key"] == "toll_plaza"
    assert "$or" in q

    doc = {
        "_id": "abc",
        "truck_field": "vehicle_reg",
        "truck_value": "OLD",
        "record": {"vehicle_reg": "OLD", "receipt_no": "R1"},
        "feed_key": "toll_plaza",
        "target_upload_id": "up-1",
        "source_filename": "f.xlsx",
    }
    fields = apply_skipped_truck_update(doc, "T103 DVL")
    assert fields["truck_value"] == "T103 DVL"
    assert fields["record"]["vehicle_reg"] == "T103 DVL"

    grouped = group_skipped_records_for_reupload([doc])
    assert "toll_plaza" in grouped
    rec = grouped["toll_plaza"][0]
    assert rec["upload_id"] == "up-1"
    assert rec["skipped_row_id"] == "abc"
    assert rec["source_filename"] == "f.xlsx"
