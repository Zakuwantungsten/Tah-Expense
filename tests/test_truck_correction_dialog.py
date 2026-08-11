"""Tests for truck correction dialog helpers (similar-row apply + status)."""

from __future__ import annotations

import sys

import pytest

from tahmeed.ui.dialogs.truck_correction_dialog import (
    TruckCorrectionDialog,
    TruckIssue,
    _norm_key,
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_norm_key_collapses_case_and_spaces() -> None:
    assert _norm_key("T760 dn") == _norm_key("T760  DN")
    assert _norm_key("t760DN") != _norm_key("T760 DN")  # no auto-insert space


def test_status_badge_empty_then_truck_or_trailer(qapp) -> None:
    dlg = TruckCorrectionDialog(
        [],
        fleet={"T760 HDN", "T100 TRL", "MC 55 XYZ"},
        fleet_kinds={
            "T760 HDN": "truck",
            "T100 TRL": "trailer",
            "MC 55 XYZ": "motor_vehicle",
        },
        can_add=False,
    )
    label, _ = dlg._status_for_text("")
    assert label == "—"

    label, color = dlg._status_for_text("T760 HDN")
    assert label == "Truck ✓"
    assert color == "#16A34A"

    label, _ = dlg._status_for_text("T100 TRL")
    assert label == "Trailer ✓"

    label, _ = dlg._status_for_text("MC 55 XYZ")
    assert label == "Bike/Car ✓"

    label, _ = dlg._status_for_text("T999 ZZZ")
    assert label == "Not in registry"

    label, _ = dlg._status_for_text("T760 DN")
    assert label in ("Invalid format", "Not in registry")
    dlg.close()


def test_similar_open_rows_match_same_pasted_value(qapp) -> None:
    issues = [
        TruckIssue(row=0, original="T760 DN", kind="invalid_format"),
        TruckIssue(row=1, original="T760  dn", kind="invalid_format"),
        TruckIssue(row=2, original="T761 DN", kind="invalid_format"),
    ]
    dlg = TruckCorrectionDialog(
        issues,
        fleet={"T760 HDN"},
        fleet_kinds={"T760 HDN": "truck"},
    )
    assert len(dlg._rows) == 3
    first = dlg._rows[0]
    similar = dlg._similar_open_rows(first)
    assert len(similar) == 1
    assert similar[0].issue.row == 1
    dlg.close()
