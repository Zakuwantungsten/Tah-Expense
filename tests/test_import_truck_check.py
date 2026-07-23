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
    assert docs[0]["save_target"] == "imported_feeds"
