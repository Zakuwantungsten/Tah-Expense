"""Tests for import truck fleet scan / apply helpers."""

from __future__ import annotations

from tahmeed.services.import_truck_check import (
    apply_truck_resolutions,
    scan_import_trucks,
    skipped_docs_from_pairs,
    truck_field_for,
)
from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue


def test_truck_field_for_known_feeds() -> None:
    assert truck_field_for("toll_plaza") == "vehicle_reg"
    assert truck_field_for("diesel_infinity") == "truck_no"
    assert truck_field_for("congo_expenses") == "truck_no"
    assert truck_field_for("rpa_schedule") == "truck_and_trailer"
    assert truck_field_for("bonds") == "truck_and_trailer"
    assert truck_field_for("unknown") is None


def test_scan_normalizes_and_matches_fleet() -> None:
    rows = [
        {"truck_no": "T688EAF"},
        {"truck_no": "T999 ZZZ"},
        {"truck_no": ""},
        {"truck_no": "weird/99"},
    ]
    fleet = {"T688 EAF"}
    result = scan_import_trucks(rows, "truck_no", fleet)
    assert rows[0]["truck_no"] == "T688 EAF"
    assert result.ok_count == 1
    assert result.empty_count == 1
    kinds = {iss.kind for iss in result.issues}
    assert "not_in_registry" in kinds
    assert "invalid_format" in kinds
    assert len(result.issues) == 2


def test_scan_matches_freeform_fleet_plates() -> None:
    """Motorcycle/car plates in the fleet must not be flagged invalid_format."""
    rows = [
        {"truck_no": "MC 123 ABC"},
        {"truck_no": "weird/99"},
    ]
    fleet = {"MC 123 ABC"}
    result = scan_import_trucks(rows, "truck_no", fleet)
    assert rows[0]["truck_no"] == "MC 123 ABC"
    assert result.ok_count == 1
    assert len(result.issues) == 1
    assert result.issues[0].kind == "invalid_format"
    assert result.issues[0].original == "weird/99"


def test_scan_skips_parking_congo_deposits() -> None:
    """Deposit rows must import without not-in-registry / invalid-format flags."""
    rows = [
        {
            "transaction_type": "Deposit",
            "vehicle_no": "-",
            "direction": "-",
            "gate_in": "-",
            "amount": "30000",
            "ledger_id": "LED141016",
        },
        {
            "transaction_type": "Parking",
            "vehicle_no": "T999 ZZZ",
            "amount": "-100",
        },
        {
            "transaction_type": "Parking",
            "vehicle_no": "-",
            "amount": "-50",
        },
    ]
    fleet = {"T688 EAF"}
    result = scan_import_trucks(rows, "vehicle_no", fleet)
    assert result.deposit_count == 1
    assert rows[0].get("is_deposit") is True
    assert rows[0]["vehicle_no"] == ""
    assert rows[0]["direction"] == ""
    # Placeholder plate on a non-deposit is blanked, not flagged
    assert rows[2]["vehicle_no"] == ""
    assert len(result.issues) == 1
    assert result.issues[0].kind == "not_in_registry"
    assert result.issues[0].row == 1


def test_blank_truck_placeholders() -> None:
    from tahmeed.services.import_truck_check import (
        is_blank_truck_value,
        is_deposit_transaction,
    )
    assert is_deposit_transaction("Deposit")
    assert is_deposit_transaction("DEPOSIT")
    assert not is_deposit_transaction("Parking")
    assert is_blank_truck_value("-")
    assert is_blank_truck_value("N/A")
    assert is_blank_truck_value("")
    assert not is_blank_truck_value("T688EAF")


def test_apply_resolutions_omit_and_allow() -> None:
    rows = [
        {"truck_no": "T111 AAA"},
        {"truck_no": "T222 BBB"},
        {"truck_no": "T333 CCC"},
    ]
    issues = [
        TruckIssue(row=0, original="T111 AAA", kind="not_in_registry",
                   corrected="T111 AAA", omit_row=True),
        TruckIssue(row=1, original="T222 BBB", kind="not_in_registry",
                   corrected="T880 CUL", allow_anyway=False),
        TruckIssue(row=2, original="T333 CCC", kind="not_in_registry",
                   corrected="T333 CCC", allow_anyway=True),
    ]
    # Pretend row 1 was Apply-fixed to a fleet number
    to_save, skipped = apply_truck_resolutions(rows, "truck_no", issues)
    assert len(skipped) == 1
    assert skipped[0][0]["truck_no"] == "T111 AAA"
    assert len(to_save) == 2
    assert to_save[0]["truck_no"] == "T880 CUL"
    assert to_save[1].get("fleet_override") is True


def test_skipped_docs_keep_target_upload_id() -> None:
    row = {"truck_no": "T999 XXX", "feed_type": "diesel_infinity"}
    iss = TruckIssue(row=0, original="T999 XXX", kind="not_in_registry", omit_row=True)
    docs = skipped_docs_from_pairs(
        [(row, iss)],
        feed_key="diesel_infinity",
        truck_field="truck_no",
        target_upload_id="upload-abc",
        source_filename="fuel.xlsx",
        sheet_label="Sheet1",
    )
    assert len(docs) == 1
    assert docs[0]["target_upload_id"] == "upload-abc"
    assert docs[0]["record"]["upload_id"] == "upload-abc"
    assert docs[0]["source_filename"] == "fuel.xlsx"
    assert docs[0]["source_row"] == 1
    assert docs[0]["save_target"] == "imported_feeds"


def test_skipped_docs_recon_save_target() -> None:
    from tahmeed.services.import_truck_check import save_target_for

    assert save_target_for("rpa_schedule") == "reconciliation"
    assert save_target_for("bonds") == "reconciliation"
    row = {"truck_and_trailer": "T999 XXX", "table": "rpa_schedule"}
    iss = TruckIssue(row=0, original="T999 XXX", kind="not_in_registry", omit_row=True)
    docs = skipped_docs_from_pairs(
        [(row, iss)],
        feed_key="rpa_schedule",
        truck_field="truck_and_trailer",
        target_upload_id="upload-rpa",
        source_filename="rpa.xlsx",
    )
    assert docs[0]["save_target"] == "reconciliation"
    assert docs[0]["truck_field"] == "truck_and_trailer"


def test_split_truck_combo_cell() -> None:
    from tahmeed.services.import_truck_check import (
        split_leading_truck,
        split_truck_combo_cell,
        truck_and_trailer_search_regex,
    )

    assert split_truck_combo_cell("T688 EAF / T123 TRA") == ["T688 EAF", "T123 TRA"]
    assert split_truck_combo_cell("T688 EAF & T123 TRA") == ["T688 EAF", "T123 TRA"]
    assert split_truck_combo_cell("T688 EAF AND T123 TRA") == ["T688 EAF", "T123 TRA"]
    assert split_truck_combo_cell("T688 EAF") is None
    assert split_truck_combo_cell("weird/99") is None
    assert split_leading_truck("T469EKZ/T689ELK") == ("T469EKZ", "/T689ELK")
    assert split_leading_truck("T469 EKZ / T689 ELK") == ("T469 EKZ", " / T689 ELK")
    assert split_leading_truck("T688 EAF") is None
    assert split_leading_truck("weird/99") is None

    rx = truck_and_trailer_search_regex("T469 EKZ")
    assert rx is not None
    compiled = __import__("re").compile(rx, __import__("re").IGNORECASE)
    assert compiled.search("T469EKZ/T689ELK")
    assert compiled.search("T469 EKZ / T689 ELK")
    assert compiled.search("T469 EKZ")
    assert not compiled.search("T689 ELK")
    assert not compiled.search("T4690 EKZ")


def test_scan_truck_and_trailer_combo() -> None:
    """SM Burhani gates the leading truck; unknown trailers pass; two trailers flag."""
    from tahmeed.services.import_truck_check import combo_suffix_of

    rows = [
        {"truck_and_trailer": "T469EKZ/T689ELK"},
        {"truck_and_trailer": "T688EAF / T999ZZZ"},
        {"truck_and_trailer": "T999ZZZ/T689ELK"},
        {"truck_and_trailer": "T724CPQ/T631DZX/T632DZX"},
        {"truck_and_trailer": "weird/99"},
        {"truck_and_trailer": "T688EAF/T631DZX/T999ZZZ"},
        {"truck_and_trailer": "T999ZZZ/T631DZX/T632DZX"},
    ]
    fleet = {"T469 EKZ", "T688 EAF", "T689 ELK", "T724 CPQ", "T631 DZX", "T632 DZX"}
    result = scan_import_trucks(rows, "truck_and_trailer", fleet)
    assert rows[0]["truck_and_trailer"] == "T469 EKZ/T689 ELK"
    assert rows[1]["truck_and_trailer"] == "T688 EAF/T999 ZZZ"
    assert result.ok_count == 2
    originals = {iss.original for iss in result.issues}
    assert "T688EAF / T999ZZZ" not in originals
    assert "T724CPQ/T631DZX/T632DZX" in originals
    assert "T688EAF/T631DZX/T999ZZZ" in originals
    assert "T999ZZZ/T689ELK" in originals
    assert "T999ZZZ/T631DZX/T632DZX" in originals
    assert "weird/99" in originals
    kinds = {iss.kind for iss in result.issues}
    assert "not_in_registry" in kinds
    assert "invalid_format" in kinds
    truck_issue = next(iss for iss in result.issues if iss.original == "T999ZZZ/T689ELK")
    assert truck_issue.combo_parts == []
    assert truck_issue.combo_suffix == combo_suffix_of("T999ZZZ/T689ELK")
    assert truck_issue.combo_suffix == "/T689ELK"
    two_trl = next(iss for iss in result.issues if iss.original == "T724CPQ/T631DZX/T632DZX")
    assert two_trl.kind == "invalid_format"
    assert two_trl.combo_parts == []
    assert two_trl.combo_suffix == "/T631DZX/T632DZX"
    bad_truck_two = next(
        iss for iss in result.issues if iss.original == "T999ZZZ/T631DZX/T632DZX"
    )
    assert bad_truck_two.kind == "not_in_registry"
    assert bad_truck_two.combo_suffix == "/T631DZX/T632DZX"


def test_scan_flags_two_trailers_ampersand_or_comma() -> None:
    from tahmeed.services.import_truck_check import is_two_trailer_cell

    assert is_two_trailer_cell("T724CPQ/T631DZX/T632DZX")
    assert is_two_trailer_cell("T724CPQ/T631DZX & T632DZX")
    assert is_two_trailer_cell("T724CPQ/T631DZX,T632DZX")
    assert not is_two_trailer_cell("T724CPQ/T631DZX")
    rows = [
        {"truck_and_trailer": "T724CPQ/T631DZX & T632DZX"},
        {"truck_and_trailer": "T724CPQ/T631DZX,T632DZX"},
    ]
    fleet = {"T724 CPQ", "T631 DZX", "T632 DZX"}
    result = scan_import_trucks(rows, "truck_and_trailer", fleet)
    assert result.ok_count == 0
    assert {iss.original for iss in result.issues} == {
        "T724CPQ/T631DZX & T632DZX",
        "T724CPQ/T631DZX,T632DZX",
    }
