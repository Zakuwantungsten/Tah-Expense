"""Diesel fuel import — already-uploaded detection."""

import re

from tahmeed.services.accountant_service import (
    _diesel_filename_sheet_query,
    diesel_batch_content_hash,
)


def _row(**overrides) -> dict:
    rec = {
        "date": "15 May 2026",
        "lpo_no": "LPO-1",
        "do_sdo_no": "DO-1",
        "diesel_at": "INFINITY",
        "ownership": "OWN",
        "clients_name": "",
        "destinations": "DAR",
        "truck_no": "T526 DRF",
        "ltrs": "3308",
        "price_per_ltr": "3100",
        "total_amount": "10254800",
    }
    rec.update(overrides)
    return rec


def test_hash_is_stable_and_case_insensitive() -> None:
    a = diesel_batch_content_hash([_row(), _row(lpo_no="LPO-2")])
    b = diesel_batch_content_hash([
        _row(diesel_at="infinity", truck_no="t526 drf"),
        _row(lpo_no="lpo-2"),
    ])
    assert a == b
    assert len(a) == 64


def test_hash_changes_when_rows_differ() -> None:
    original = diesel_batch_content_hash([_row()])
    changed = diesel_batch_content_hash([_row(ltrs="100")])
    assert original != changed


def test_hash_is_order_sensitive() -> None:
    forward = diesel_batch_content_hash([_row(lpo_no="A"), _row(lpo_no="B")])
    reverse = diesel_batch_content_hash([_row(lpo_no="B"), _row(lpo_no="A")])
    assert forward != reverse


def test_filename_query_requires_file_and_feed() -> None:
    assert _diesel_filename_sheet_query("", "infinity.xlsx", "INFINITY") is None
    assert _diesel_filename_sheet_query("diesel_infinity", "", "INFINITY") is None
    assert _diesel_filename_sheet_query("diesel_infinity", "   ", "") is None


def test_filename_query_matches_sheet_case_insensitively() -> None:
    query = _diesel_filename_sheet_query(
        "diesel_infinity", "Infinity May.xlsx", "INFINITY",
    )
    assert query is not None
    assert query["feed_type"] == "diesel_infinity"
    assert query["source_filename"]["$options"] == "i"
    assert query["sheet_label"]["$options"] == "i"
    assert query["source_filename"]["$regex"] == f"^{re.escape('Infinity May.xlsx')}$"
    assert query["sheet_label"]["$regex"] == f"^{re.escape('INFINITY')}$"


def test_filename_query_csv_has_no_sheet() -> None:
    query = _diesel_filename_sheet_query("diesel_infinity", "infinity.csv")
    assert query is not None
    assert "sheet_label" not in query
    assert "$or" in query
