"""Validation of bulk import sessions: mappings, trucks (trucks + trailers), days."""

from datetime import date

from bson import ObjectId

from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.services.import_session_parser import StagedRow
from tahmeed.services.import_session_service import ImportSession
from tahmeed.services.import_session_validation import (
    DAY_BLOCKED,
    DAY_PUSHED,
    DAY_READY,
    ISSUE_BAD_ITEM,
    ISSUE_BAD_PAYEE,
    ISSUE_INVALID_TRUCK,
    ISSUE_UNMAPPED,
    KIND_BAD_MAP,
    KIND_IGNORED,
    KIND_UNMAPPED,
    SOURCE_GLOBAL,
    SOURCE_TEMP,
    ItemIndex,
    ItemRef,
    is_fleet_truck,
    rank_name_matches,
    rank_truck_matches,
    references_from_snapshot,
    validate_session,
)

SPARES_ID = ObjectId()
FUEL_ID = ObjectId()
D1 = date(2026, 10, 1)
D2 = date(2026, 10, 2)


def _snapshot() -> dict:
    return {
        "categories": [
            {"_id": SPARES_ID, "name": "DIRECT COSTS:SPARES"},
            {"_id": FUEL_ID, "name": "DIRECT COSTS:FUEL"},
            {"_id": ObjectId(), "name": "ADMIN:FUEL"},
            {"_id": ObjectId(), "name": "OLD ITEM", "active": False},
        ],
        "payees": [{"name": "SPARE PARTS"}, {"name": "PUMA"}],
        "trucks": [{"number": "T100 ABC"}, {"number": "T853 EKS"}],
        "trailers": [{"number": "T689 ELK"}],
        "motor_vehicles": [{"number": "T555 CAR"}],
        "settings": {"allowed_truck_labels": ["WORKSHOP"]},
    }


def _row(n: int, desc: str, truck: str = "", day: date = D1, amount: float = 100.0) -> StagedRow:
    return StagedRow(
        row_id=f"f|S|{n}",
        file_key="f",
        source_file=f"MATUMIZI YA {day.strftime('%d-%m-%y')}.xlsx",
        excel_row=n,
        batch_date=day,
        description=desc,
        amount=amount,
        truck_raw=truck,
    )


def _session(*rows: StagedRow) -> ImportSession:
    session = ImportSession.new()
    session.rows = list(rows)
    return session


def _global(desc: str, cat_id, cat_name: str, payee: str) -> DescriptionMapping:
    return DescriptionMapping(
        description_key=desc, description=desc, category_id=cat_id,
        category_name=cat_name, payee=payee,
    )


def test_item_index_full_leaf_and_preferred_parent() -> None:
    index = ItemIndex([
        ItemRef(1, "Direct Costs:Spares"),
        ItemRef(2, "DIRECT COSTS:FUEL"),
        ItemRef(3, "ADMIN:FUEL"),
        ItemRef(4, "COST OF SALES:DIRECT COSTS:TYRES"),
    ])
    assert index.resolve("direct costs:spares").id == 1
    assert index.resolve("Spares").id == 1
    assert index.resolve("Other:Spares").id == 1
    assert index.resolve("FUEL").id == 2
    assert index.resolve("Direct Costs:Tyres").id == 4
    assert index.resolve("nothing") is None


def test_fleet_is_trucks_trailers_and_motor_vehicles() -> None:
    refs = references_from_snapshot(_snapshot())
    assert refs.fleet == {"T100 ABC", "T853 EKS", "T689 ELK", "T555 CAR"}
    assert "WORKSHOP" in refs.place_labels


def test_unmapped_temp_and_global_resolution() -> None:
    refs = references_from_snapshot(
        _snapshot(),
        {"DIESEL": _global("DIESEL", FUEL_ID, "DIRECT COSTS:FUEL", "PUMA")},
    )
    session = _session(_row(1, "DIESEL"), _row(2, "GET VALVE"), _row(3, "MYSTERY"))
    session.set_mapping("GET VALVE", "Spares", "spare parts")
    result = validate_session(session, refs)

    diesel = result.rows["f|S|1"]
    assert (diesel.item, diesel.category_id, diesel.payee, diesel.mapping_source) == (
        "DIRECT COSTS:FUEL", FUEL_ID, "PUMA", SOURCE_GLOBAL,
    )
    valve = result.rows["f|S|2"]
    assert (valve.item, valve.category_id, valve.payee, valve.mapping_source) == (
        "DIRECT COSTS:SPARES", SPARES_ID, "SPARE PARTS", SOURCE_TEMP,
    )
    assert result.rows["f|S|3"].issues == (ISSUE_UNMAPPED,)
    day = result.day(D1)
    assert day.status == DAY_BLOCKED
    assert day.reasons == ["1 unmapped description(s)"]


def test_priority_and_disabling_temp_mappings() -> None:
    refs = references_from_snapshot(
        _snapshot(),
        {"DIESEL": _global("DIESEL", FUEL_ID, "DIRECT COSTS:FUEL", "PUMA")},
    )
    session = _session(_row(1, "DIESEL"))
    session.set_mapping("DIESEL", "SPARES", "SPARE PARTS")
    assert validate_session(session, refs).rows["f|S|1"].mapping_source == SOURCE_TEMP

    session.options.prefer_temp_mappings = False
    assert validate_session(session, refs).rows["f|S|1"].mapping_source == SOURCE_GLOBAL

    session.options.prefer_temp_mappings = True
    session.options.use_temp_mappings = False
    assert validate_session(session, refs).rows["f|S|1"].mapping_source == SOURCE_GLOBAL


def test_bad_preferred_mapping_falls_back_to_valid_other() -> None:
    refs = references_from_snapshot(
        _snapshot(),
        {"DIESEL": _global("DIESEL", FUEL_ID, "DIRECT COSTS:FUEL", "PUMA")},
    )
    session = _session(_row(1, "DIESEL"), _row(2, "TOLLS"))
    session.set_mapping("DIESEL", "NOT AN ITEM", "PUMA")
    session.set_mapping("TOLLS", "SPARES", "NOBODY")
    result = validate_session(session, refs)
    assert result.rows["f|S|1"].mapping_source == SOURCE_GLOBAL
    assert result.rows["f|S|2"].issues == (ISSUE_BAD_PAYEE,)

    issues = result.description_issues()
    assert [(i.kind, i.description, i.detail) for i in issues] == [
        (KIND_BAD_MAP, "TOLLS", "payee not found: NOBODY"),
    ]


def test_global_mapping_without_payee_is_bad_map() -> None:
    refs = references_from_snapshot(
        _snapshot(),
        {"DIESEL": _global("DIESEL", ObjectId(), "GONE", "")},
    )
    result = validate_session(_session(_row(1, "DIESEL")), refs)
    assert set(result.rows["f|S|1"].issues) == {ISSUE_BAD_ITEM, ISSUE_BAD_PAYEE}


def test_trucks_trailer_combo_corrections_and_motor_vehicles() -> None:
    refs = references_from_snapshot(_snapshot())
    session = _session(
        _row(1, "A", "t853eks"),
        _row(2, "A", "T100ABC/T689ELK"),
        _row(3, "A", "141 EFP"),
        _row(4, "A", "141EFP"),
        _row(5, "A", "168 EGJ"),
        _row(6, "A", "T555 CAR"),
        _row(7, "A", "workshop"),
        _row(8, "A", "-"),
    )
    session.set_mapping("A", "SPARES", "SPARE PARTS")
    session.set_truck_fix("f|S|5", "")
    result = validate_session(session, refs)
    trucks = {rid: (r.truck, ISSUE_INVALID_TRUCK in r.issues) for rid, r in result.rows.items()}
    assert trucks["f|S|1"] == ("T853 EKS", False)
    assert trucks["f|S|2"] == ("T100 ABC/T689 ELK", False)
    assert trucks["f|S|3"][1] and trucks["f|S|4"][1]
    assert result.rows["f|S|5"].truck_cleared and trucks["f|S|5"] == ("", False)
    assert trucks["f|S|6"] == ("T555 CAR", False)
    assert trucks["f|S|7"] == ("WORKSHOP", False)
    assert trucks["f|S|8"] == ("", False)

    rows = result.truck_rows(fixes=session.truck_fixes)
    assert [(t.row_id, t.invalid, t.fix) for t in rows] == [
        ("f|S|3", True, None),
        ("f|S|4", True, None),
        ("f|S|5", False, ""),
    ]
    assert result.day(D1).reasons == ["2 row(s) with a wrong truck"]

    # A fix belongs to its own row only, even when another row has the same value.
    session.set_truck_fix("f|S|3", "T100 ABC")
    result = validate_session(session, refs)
    assert result.rows["f|S|3"].truck == "T100 ABC"
    assert result.rows["f|S|3"].truck_corrected
    assert ISSUE_INVALID_TRUCK in result.rows["f|S|4"].issues
    assert not result.rows["f|S|4"].truck_corrected

    # A fix that is not one of our trucks still blocks the row, unless it is allowed.
    session.set_truck_fix("f|S|4", "T999 ZZZ")
    result = validate_session(session, refs)
    assert ISSUE_INVALID_TRUCK in result.rows["f|S|4"].issues
    session.set_truck_fix("f|S|4", "T999 ZZZ", allowed=True)
    result = validate_session(session, refs)
    assert ISSUE_INVALID_TRUCK not in result.rows["f|S|4"].issues
    assert result.rows["f|S|4"].truck == "T999 ZZZ"
    allowed = {
        t.row_id: t.allowed
        for t in result.truck_rows(fixes=session.truck_fixes, allowed=session.truck_allowed)
    }
    assert allowed["f|S|4"] and not allowed["f|S|3"]


def test_is_fleet_truck_accepts_only_our_trucks() -> None:
    refs = references_from_snapshot(_snapshot())
    assert is_fleet_truck("t853eks", refs) == "T853 EKS"
    assert is_fleet_truck("T853 EKZ", refs) is None
    assert rank_name_matches("t100abc", ["T100 ABC", "T1000 X"])[0] == "T100 ABC"
    plates = ["T641 EQC", "T641 EAF", "T100 ABC"]
    assert rank_truck_matches("641 EQC", plates) == ["T641 EQC", "T641 EAF"]

    snapshot = _snapshot()
    snapshot["motor_vehicles"] = [{"number": "MC 123 ABC"}]
    refs = references_from_snapshot(snapshot)
    assert is_fleet_truck("mc 123 abc", refs) == "MC 123 ABC"


def test_day_status_ignored_pushed_and_scopes() -> None:
    refs = references_from_snapshot(_snapshot())
    session = _session(
        _row(1, "GET VALVE", day=D1, amount=300),
        _row(2, "BANK CHARGES", day=D1, amount=5),
        _row(3, "MYSTERY", day=D2),
        _row(4, "MYSTERY", day=D1),
    )
    session.set_mapping("GET VALVE", "SPARES", "SPARE PARTS")
    session.ignore(["bank charges"])
    result = validate_session(session, refs)

    d1, d2 = result.day(D1), result.day(D2)
    assert (d1.status, d1.row_count, d1.push_count, d1.ignored_count) == (DAY_BLOCKED, 3, 2, 1)
    assert d1.total_tzs == 405
    assert d2.status == DAY_BLOCKED

    all_issues = result.description_issues()
    assert [(i.kind, i.description, i.row_count, i.day_count) for i in all_issues] == [
        (KIND_UNMAPPED, "MYSTERY", 2, 2),
        (KIND_IGNORED, "BANK CHARGES", 1, 1),
    ]
    assert [i.row_count for i in result.description_issues(D2)] == [1]

    session.set_mapping("MYSTERY", "FUEL", "PUMA")
    session.mark_day_pushed(D2, row_count=1)
    result = validate_session(session, refs)
    assert result.day(D1).status == DAY_READY
    assert result.day(D2).status == DAY_PUSHED
    assert result.ready_dates() == [D1]
    assert (result.ready_count, result.blocked_count, result.pushed_count) == (1, 0, 1)
