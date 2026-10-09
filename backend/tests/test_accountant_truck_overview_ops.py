"""Phase 9.1 — truck overview pure helpers."""

from datetime import datetime

from app.accountant_truck_overview_ops import (
    DIESEL_STATION_SOURCE_GROUPS,
    TRUCK_OVERVIEW_SOURCES,
    diesel_overview_query,
    filter_truck_overview_rows,
    imported_feed_overview_query,
    master_rows_from_docs,
    normalize_truck_overview_sources,
    parse_source_param,
    summarize_overview_rows,
    toll_rows_from_docs,
)


def test_normalize_sources() -> None:
    assert normalize_truck_overview_sources("all") is None
    assert normalize_truck_overview_sources("master") == {"master"}
    assert normalize_truck_overview_sources("diesel_imports") == set(
        DIESEL_STATION_SOURCE_GROUPS
    )


def test_parse_source_param() -> None:
    assert parse_source_param("") == "all"
    assert parse_source_param("master") == "master"
    assert parse_source_param("master,toll_plaza") == ["master", "toll_plaza"]


def test_diesel_query_uses_transaction_date() -> None:
    q = diesel_overview_query(
        "diesel_infinity",
        "T103 DVL",
        datetime(2026, 1, 1),
        datetime(2026, 12, 31),
    )
    assert q["feed_type"] == "diesel_infinity"
    assert "transaction_date" in q
    assert "$regex" in q["truck_no"]


def test_imported_feed_query() -> None:
    q = imported_feed_overview_query(
        "toll_plaza",
        "vehicle_reg",
        "T103 DVL",
        datetime(2026, 1, 1),
        datetime(2026, 12, 31),
    )
    assert q["feed_type"] == "toll_plaza"
    assert "transaction_date" in q


def test_filter_and_summary() -> None:
    rows = [
        {
            "source_group": "master",
            "source": "Master Expenses",
            "date": datetime(2026, 3, 1),
            "currency": "TZS",
            "amount": 10.0,
            "liters": None,
        },
        {
            "source_group": "infinity",
            "source": "Infinity Diesel",
            "date": datetime(2026, 3, 2),
            "currency": "TZS",
            "amount": 20.0,
            "liters": 5.0,
        },
        {
            "source_group": "toll_plaza",
            "source": "Toll Plaza",
            "date": datetime(2026, 3, 3),
            "currency": "ZMW",
            "amount": 30.0,
            "liters": None,
        },
    ]
    filtered = filter_truck_overview_rows(rows, source="diesel_imports")
    assert len(filtered) == 1
    assert filtered[0]["source_group"] == "infinity"
    summary = summarize_overview_rows(rows)
    assert summary["record_count"] == 3
    assert summary["tzs_total"] == 30.0
    assert summary["zmw_total"] == 30.0
    assert summary["liters_total"] == 5.0


def test_row_builders() -> None:
    master = master_rows_from_docs(
        [
            {
                "date": datetime(2026, 1, 1),
                "truck_number": "T1",
                "category_name": "Fuel",
                "item": "Parts",
                "description": "bolt",
                "currency": "TZS",
                "amount": 100,
            },
            {
                "date": datetime(2026, 1, 2),
                "truck_number": "T1",
                "category_name": "Diesel Cash",
                "item": "Diesel Cash",
                "description": "200 ltrs",
                "currency": "TZS",
                "amount": 50,
            },
        ],
        ["Diesel Cash"],
    )
    assert master[0]["source_group"] == "master"
    assert master[1]["source_group"] == "diesel_cash"
    assert master[1]["liters"] == 200.0

    toll = toll_rows_from_docs(
        [
            {
                "transaction_date": datetime(2026, 2, 1),
                "vehicle_reg": "T1",
                "toll_plaza": "NAKONDE",
                "tender_amount": 12,
            }
        ]
    )
    assert toll[0]["currency"] == "ZMW"
    assert "toll_plaza" in TRUCK_OVERVIEW_SOURCES
