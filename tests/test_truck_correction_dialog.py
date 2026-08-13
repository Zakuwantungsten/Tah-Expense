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


def test_combo_card_shows_truck_only(qapp) -> None:
    """SM Burhani fix dialog edits the truck; trailers stay on the saved cell."""
    issues = [
        TruckIssue(
            row=0,
            original="T843EKT/T691ELK",
            kind="not_in_registry",
            combo_suffix="/T691ELK",
        )
    ]
    dlg = TruckCorrectionDialog(
        issues,
        fleet={"T880 CUL"},
        fleet_kinds={"T880 CUL": "truck"},
        import_mode=True,
    )
    rw = dlg._rows[0]
    assert rw.part_edits == []
    assert rw.edit.text() == "T843 EKT"
    assert "trailer" not in rw.kind_label.text().lower()
    assert rw.allow_btn is not None and rw.skip_row_btn is not None
    row_widgets = []
    for i in range(rw.card.layout().count()):
        item = rw.card.layout().itemAt(i)
        lay = item.layout() if item is not None else None
        if lay is None:
            continue
        widgets = [
            lay.itemAt(j).widget()
            for j in range(lay.count())
            if lay.itemAt(j) is not None and lay.itemAt(j).widget() is not None
        ]
        if rw.edit in widgets:
            row_widgets = widgets
            break
    assert rw.edit in row_widgets
    assert rw.allow_btn in row_widgets
    assert rw.skip_row_btn in row_widgets
    rw.edit.setText("T880 CUL")
    dlg._fleet.add("T880 CUL")
    cell = dlg._with_combo_suffix(rw.issue, "T880 CUL")
    assert cell == "T880 CUL/T691ELK"
    dlg.close()


def test_combo_card_two_trailers_keeps_suffix(qapp) -> None:
    issues = [
        TruckIssue(
            row=0,
            original="T724CPQ/T631DZX/T632DZX",
            kind="invalid_format",
            combo_suffix="/T631DZX/T632DZX",
        )
    ]
    dlg = TruckCorrectionDialog(
        issues,
        fleet={"T724 CPQ"},
        fleet_kinds={"T724 CPQ": "truck"},
        import_mode=True,
    )
    rw = dlg._rows[0]
    assert rw.part_edits == []
    assert rw.edit.text() == "T724 CPQ"
    assert dlg._is_two_trailer_issue(rw.issue)
    assert "two trailers" in rw.kind_label.text().lower()
    assert "allow anyway" in rw.kind_label.text().lower()
    assert dlg._with_combo_suffix(rw.issue, "T724 CPQ") == "T724 CPQ/T631DZX/T632DZX"
    dlg.close()
