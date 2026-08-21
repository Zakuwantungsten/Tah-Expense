"""Plate-aware search for Separate Expenses tabs must not match sibling trucks."""

from __future__ import annotations

import re

from tahmeed.services.accountant_service import (
    _afritrack_all_query,
    _build_insurance_query,
    _congo_entries_query,
    _feed_search_clause,
    _kimvi_entries_query,
    _parking_congo_all_query,
    _rahntech_all_query,
    _toll_plaza_all_query,
    _zambia_parking_all_query,
)
from tahmeed.services.reconciliation_service import _truck_search_clauses


def _compile_plate(clause: dict) -> re.Pattern:
    if "vehicle_reg" in clause:
        rx = clause["vehicle_reg"]["$regex"]
    elif "truck_no" in clause:
        rx = clause["truck_no"]["$regex"]
    elif "vehicle_no" in clause:
        rx = clause["vehicle_no"]["$regex"]
    elif "plate_num" in clause:
        rx = clause["plate_num"]["$regex"]
    elif "truck_number" in clause:
        rx = clause["truck_number"]["$regex"]
    elif "truck" in clause:
        rx = clause["truck"]["$regex"]
    elif "$or" in clause:
        rx = next(iter(clause["$or"][0].values()))["$regex"]
    else:
        raise AssertionError(f"no plate regex in {clause}")
    return re.compile(rx, re.IGNORECASE)


def _search_clause(query: dict) -> dict:
    if "$and" in query:
        return next(c for c in query["$and"] if c.keys() != {"feed_type"} and "expense_type" not in c)
    if "$or" in query or "vehicle_reg" in query or "truck_no" in query:
        return {k: v for k, v in query.items() if k != "feed_type"}
    raise AssertionError(f"no search clause in {query}")


def test_toll_plaza_plate_search_is_not_broad() -> None:
    query = _toll_plaza_all_query("T103 DVL")
    clause = _search_clause(query)
    assert "$or" not in clause
    assert "vehicle_reg" in clause
    compiled = _compile_plate(clause)
    assert compiled.search("T103 DVL")
    assert compiled.search("T103DVL")
    assert not compiled.search("T102 DVL")
    assert not compiled.search("T1030 DVL")


def test_toll_plaza_text_search_still_hits_plaza_name() -> None:
    query = _toll_plaza_all_query("Kapiri")
    clause = _search_clause(query)
    fields = {key for item in clause["$or"] for key in item}
    assert "toll_plaza" in fields
    assert "vehicle_reg" in fields


def test_congo_and_kimvi_plate_search_stays_on_truck() -> None:
    congo = _search_clause(_congo_entries_query("t103 dvl"))
    kimvi = _search_clause(_kimvi_entries_query("t103 dvl"))
    assert "truck_no" in congo and "$or" not in congo
    assert "truck_no" in kimvi and "$or" not in kimvi
    compiled = _compile_plate(congo)
    assert not compiled.search("T102 DVL")


def test_other_separate_expense_plate_searches() -> None:
    queries = [
        _parking_congo_all_query("T103 DVL"),
        _zambia_parking_all_query("T103 DVL"),
        _rahntech_all_query("T103 DVL"),
        _afritrack_all_query("T103 DVL"),
        _build_insurance_query("comesa", "T103 DVL"),
        _build_insurance_query("third_party", "T103 DVL"),
    ]
    for query in queries:
        clause = _search_clause(query) if "$and" in query else {
            k: v for k, v in query.items() if k != "feed_type"
        }
        compiled = _compile_plate(clause)
        assert compiled.search("T103DVL")
        assert not compiled.search("T102 DVL")


def test_sm_burhani_plate_search_only_hits_truck_cell() -> None:
    clauses = _truck_search_clauses("T103 DVL")
    assert clauses == [{"truck_and_trailer": clauses[0]["truck_and_trailer"]}]
    compiled = re.compile(clauses[0]["truck_and_trailer"]["$regex"], re.I)
    assert compiled.search("T103DVL/T200 XXX")
    assert not compiled.search("T102 DVL")


def test_feed_search_clause_text_keeps_other_fields() -> None:
    clause = _feed_search_clause("Kapiri", ("vehicle_reg",), ("toll_plaza", "receipt_no"))
    assert clause is not None
    fields = {key for item in clause["$or"] for key in item}
    assert fields == {"vehicle_reg", "toll_plaza", "receipt_no"}
