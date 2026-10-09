"""Phase 6.1 — reconciliation query / totals helpers."""

from datetime import datetime

import pytest

from app.reconciliation_ops import (
    all_records_query,
    build_query,
    default_bonds_station_docs,
    station_display_name,
    station_query_value,
    station_slug,
    totals_from_aggregate,
    truck_field_search_regex,
    truck_search_clauses,
    unique_stations_in_order,
    upload_record_query,
    upload_records_sort,
    uploads_pipeline,
    validate_table,
)


def test_validate_table() -> None:
    assert validate_table("bonds") == "bonds"
    assert validate_table("rpa_schedule") == "rpa_schedule"
    with pytest.raises(ValueError):
        validate_table("other")


def test_station_helpers() -> None:
    assert station_query_value("all") == ""
    assert station_query_value("nakonde") == "nakonde"
    assert station_display_name("kasumbalesa") == "Kasumbalesa"
    assert station_slug("New Station!") == "new_station"
    assert unique_stations_in_order(["nakonde", "all", "nakonde", "sakania"]) == [
        "nakonde",
        "sakania",
    ]


def test_build_query_station_and_search() -> None:
    q = build_query("bonds", station="nakonde", search="ABC")
    assert q["entity"] == "sm_burhani"
    assert q["table"] == "bonds"
    assert q["station"] == "nakonde"
    assert "$or" in q


def test_plate_search_only_hits_truck_field() -> None:
    clauses = truck_search_clauses("T103 DVL")
    assert len(clauses) == 1
    assert "truck_and_trailer" in clauses[0]
    assert truck_field_search_regex("hello") is None
    text_clauses = truck_search_clauses("hello")
    assert len(text_clauses) > 1


def test_all_records_year_month_filter() -> None:
    q = all_records_query("rpa_schedule", year=2026, month=4)
    assert "$and" in q
    date_clause = q["$and"][0]["$or"][0]["t1_date"]
    assert date_clause["$gte"] == datetime(2026, 4, 1)
    assert date_clause["$lt"] == datetime(2026, 5, 1)


def test_upload_record_query_and_sort() -> None:
    q = upload_record_query("u1", "bonds", station="all", search="")
    assert q["upload_id"] == "u1"
    assert "station" not in q
    assert upload_records_sort("")[0][0] == "source_index"
    assert upload_records_sort("nakonde")[0][0] == "sr_no"


def test_totals_from_aggregate() -> None:
    empty = totals_from_aggregate([])
    assert empty["count"] == 0
    assert empty["variance"] == 0.0
    filled = totals_from_aggregate(
        [{"count": 2, "charge_total": 100.0, "confirmed_total": 40.0, "disputed_count": 1}]
    )
    assert filled["invoiced"] == 100.0
    assert filled["confirmed"] == 40.0
    assert filled["variance"] == 60.0
    assert filled["disputed"] == 1


def test_uploads_pipeline_and_bonds_seed() -> None:
    pipe = uploads_pipeline("bonds")
    assert pipe[0]["$match"]["table"] == "bonds"
    seed = default_bonds_station_docs()
    assert len(seed) == 3
    assert seed[0]["slug"] == "nakonde"
