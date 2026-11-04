"""Bulk multi-sheet truck gate applies one dialog across sheets."""

from __future__ import annotations

from tahmeed.services.import_truck_check import apply_truck_resolutions
from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue


def test_apply_resolutions_per_sheet_slice() -> None:
    """Issues remapped to local row indexes apply only within that sheet."""
    sheet_a = [
        {"truck_no": "T111 AAA", "sheet_label": "A", "upload_id": "u1"},
        {"truck_no": "BAD1", "sheet_label": "A", "upload_id": "u1"},
    ]
    sheet_b = [
        {"truck_no": "BAD2", "sheet_label": "B", "upload_id": "u2"},
        {"truck_no": "T222 BBB", "sheet_label": "B", "upload_id": "u2"},
    ]
    # After combined scan, issues at global rows 1 and 2 → local 1 on A, 0 on B
    a_issues = [
        TruckIssue(row=1, original="BAD1", kind="not_in_registry", corrected="T688 EAF"),
    ]
    b_issues = [
        TruckIssue(row=0, original="BAD2", kind="not_in_registry", omit_row=True),
    ]
    a_save, a_skip = apply_truck_resolutions(sheet_a, "truck_no", a_issues)
    b_save, b_skip = apply_truck_resolutions(sheet_b, "truck_no", b_issues)
    assert len(a_save) == 2
    assert a_save[1]["truck_no"] == "T688 EAF"
    assert not a_skip
    assert len(b_save) == 1
    assert b_save[0]["truck_no"] == "T222 BBB"
    assert len(b_skip) == 1
    assert b_skip[0][0]["upload_id"] == "u2"


def test_upload_browse_headers_wrap_action() -> None:
    from tahmeed.ui.accountant.feed_upload_actions import upload_browse_headers

    headers = upload_browse_headers(["UPLOAD DATE", "FILE"])
    assert headers[0] == ""
    assert headers[-1] == "ACTION"
    assert headers[1:-1] == ["UPLOAD DATE", "FILE"]
