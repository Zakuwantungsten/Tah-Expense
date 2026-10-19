"""Tests for truck correction dialog helpers (similar-row apply + status)."""

from __future__ import annotations

import sys

import pytest

from tahmeed.ui.dialogs.truck_correction_dialog import (
    TruckCorrectionDialog,
    TruckIssue,
    _norm_key,
    snapshot_row_details,
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


def test_allow_park_false_hides_park_actions(qapp) -> None:
    from PySide6.QtWidgets import QPushButton

    from tahmeed.ui.dialogs.truck_correction_dialog import (
        TruckCorrectionDialog,
        TruckIssue,
    )

    dlg = TruckCorrectionDialog(
        [TruckIssue(row=0, original="BAD", kind="not_in_registry")],
        fleet=set(),
        allow_park=False,
    )
    labels = {
        (b.text() or "").replace("&", "")
        for b in dlg.findChildren(QPushButton)
        if b.isVisibleTo(dlg)
    }
    assert "Park all" not in labels
    assert "Park remaining" not in labels
    assert "Park" not in labels
    assert "Cancel" in labels
    assert "Done" in labels
    dlg.close()


def test_register_mode_shows_allow_park_and_add_buttons(qapp) -> None:
    issues = [
        TruckIssue(row=0, original="T128 EFP", kind="not_in_registry"),
    ]
    dlg = TruckCorrectionDialog(
        issues,
        fleet={"T880 CUL"},
        fleet_kinds={"T880 CUL": "truck"},
        can_add=False,  # ignored — Add is always offered
        import_mode=False,
    )
    rw = dlg._rows[0]
    assert rw.allow_btn is not None
    assert rw.skip_row_btn is not None
    assert rw.allow_btn.text() == "Allow"
    assert rw.skip_row_btn.text() == "Park"
    assert rw.add_btn is not None
    assert rw.add_btn.text() == "Add to registry"
    dlg.close()


def test_register_park_sets_parked_flag(qapp) -> None:
    dlg = TruckCorrectionDialog(
        [TruckIssue(row=0, original="T999 ZZZ", kind="not_in_registry")],
        fleet=set(),
        import_mode=False,
    )
    rw = dlg._rows[0]
    dlg._park_row(rw)
    assert dlg._rows == []
    assert len(dlg.issues) == 1
    assert dlg.issues[0].parked is True
    assert dlg.issues[0].corrected == "T999 ZZZ"
    dlg.close()


def test_commit_registry_add_requires_confirm(qapp, monkeypatch) -> None:
    """Kind pick + confirm must both accept before queuing a registry add."""
    dlg = TruckCorrectionDialog(
        [TruckIssue(row=0, original="T999 ZZZ", kind="not_in_registry")],
        fleet=set(),
        can_add=False,
    )
    monkeypatch.setattr(dlg, "_ask_registry_kind", lambda _t: "trucks")
    monkeypatch.setattr(dlg, "_confirm_registry_add", lambda _n, _k: False)
    assert dlg._commit_registry_add("T999 ZZZ") is None
    assert dlg.pending_registry_adds == []

    monkeypatch.setattr(dlg, "_confirm_registry_add", lambda _n, _k: True)
    number = dlg._commit_registry_add("T999 ZZZ")
    assert number == "T999 ZZZ"
    assert dlg.pending_registry_adds == [("trucks", "T999 ZZZ")]
    dlg.close()


def test_allow_anyway_cancel_keeps_row(qapp, monkeypatch) -> None:
    dlg = TruckCorrectionDialog(
        [TruckIssue(row=0, original="T999 ZZZ", kind="not_in_registry")],
        fleet=set(),
    )
    monkeypatch.setattr(dlg, "_confirm_allow", lambda **_kw: False)
    rw = dlg._rows[0]
    dlg._allow_anyway_row(rw)
    assert len(dlg._rows) == 1
    assert dlg.issues == []
    dlg.close()


def test_allow_anyway_accept_resolves_override(qapp, monkeypatch) -> None:
    dlg = TruckCorrectionDialog(
        [TruckIssue(row=0, original="T999 ZZZ", kind="not_in_registry")],
        fleet=set(),
    )
    monkeypatch.setattr(dlg, "_confirm_allow", lambda **_kw: True)
    monkeypatch.setattr(dlg, "_confirm_apply_similar", lambda *_a, **_k: [])
    rw = dlg._rows[0]
    dlg._allow_anyway_row(rw)
    assert dlg._rows == []
    assert len(dlg.issues) == 1
    assert dlg.issues[0].allow_anyway is True
    assert dlg.issues[0].corrected == "T999 ZZZ"
    assert dlg.issues[0].parked is False
    dlg.close()


def test_allow_clears_stale_parked_flag(qapp, monkeypatch) -> None:
    """Allow/Apply must never leave ``parked=True`` (would auto-park after dialog)."""
    issue = TruckIssue(row=0, original="T999 ZZZ", kind="not_in_registry")
    issue.parked = True
    dlg = TruckCorrectionDialog([issue], fleet=set())
    monkeypatch.setattr(dlg, "_confirm_allow", lambda **_kw: True)
    monkeypatch.setattr(dlg, "_confirm_apply_similar", lambda *_a, **_k: [])
    dlg._allow_anyway_row(dlg._rows[0])
    assert dlg.issues[0].parked is False
    assert dlg.issues[0].allow_anyway is True
    dlg.close()


def test_allow_all_cancel_keeps_rows(qapp, monkeypatch) -> None:
    dlg = TruckCorrectionDialog(
        [
            TruckIssue(row=0, original="T111 AAA", kind="not_in_registry"),
            TruckIssue(row=1, original="T222 BBB", kind="not_in_registry"),
        ],
        fleet=set(),
    )
    monkeypatch.setattr(dlg, "_confirm_allow", lambda **_kw: False)
    dlg._allow_all_remaining()
    assert len(dlg._rows) == 2
    assert dlg.issues == []
    dlg.close()


def test_allow_all_confirm_resolves_all(qapp, monkeypatch) -> None:
    dlg = TruckCorrectionDialog(
        [
            TruckIssue(row=0, original="T111 AAA", kind="not_in_registry"),
            TruckIssue(row=1, original="T222 BBB", kind="not_in_registry"),
        ],
        fleet=set(),
    )
    monkeypatch.setattr(dlg, "_confirm_allow", lambda **_kw: True)
    dlg._allow_all_remaining()
    assert dlg._rows == []
    assert len(dlg.issues) == 2
    assert all(i.allow_anyway for i in dlg.issues)
    dlg.close()


def test_park_all_shows_branded_busy_overlay(qapp, monkeypatch) -> None:
    dlg = TruckCorrectionDialog(
        [
            TruckIssue(row=i, original=f"T{i:03d} AAA", kind="not_in_registry")
            for i in range(3)
        ],
        fleet=set(),
    )
    shown: list[str] = []
    real_run = dlg._run_bulk_busy

    def tracking_run(message: str, work):
        shown.append(message)
        return real_run(message, work)

    monkeypatch.setattr(dlg, "_run_bulk_busy", tracking_run)
    dlg._park_all_remaining()
    assert shown and "Parking" in shown[0]
    assert dlg._rows == []
    assert len(dlg.issues) == 3
    assert all(i.parked for i in dlg.issues)
    assert dlg._busy_overlay is not None
    assert dlg._busy_overlay.isHidden()
    dlg.close()


def test_allow_all_shows_branded_busy_overlay(qapp, monkeypatch) -> None:
    dlg = TruckCorrectionDialog(
        [
            TruckIssue(row=0, original="T111 AAA", kind="not_in_registry"),
            TruckIssue(row=1, original="T222 BBB", kind="not_in_registry"),
        ],
        fleet=set(),
    )
    monkeypatch.setattr(dlg, "_confirm_allow", lambda **_kw: True)
    shown: list[str] = []
    real_run = dlg._run_bulk_busy

    def tracking_run(message: str, work):
        shown.append(message)
        overlay = dlg._ensure_busy_overlay()
        assert overlay is not None
        return real_run(message, work)

    monkeypatch.setattr(dlg, "_run_bulk_busy", tracking_run)
    dlg._allow_all_remaining()
    assert shown and "Allowing" in shown[0]
    assert dlg._rows == []
    assert dlg._busy_overlay is not None
    assert dlg._busy_overlay.isHidden()
    dlg.close()


def test_snapshot_row_details_prefers_known_fields() -> None:
    details = snapshot_row_details({
        "_raw": {"x": 1},
        "description": "FUEL",
        "amount": 1200,
        "mystery": "keep",
        "empty": "",
    })
    assert details["Description"] == "FUEL"
    assert details["Amount"] == "1200"
    assert details["Mystery"] == "keep"
    assert "_raw" not in details
    assert "Empty" not in details


def test_details_tab_peep_and_see_all(qapp) -> None:
    issues = [
        TruckIssue(
            row=0,
            original="T111 AAA",
            kind="not_in_registry",
            row_details={"Description": "Toll", "Amount": "50"},
        ),
        TruckIssue(
            row=1,
            original="T222 BBB",
            kind="invalid_format",
            row_details={"Description": "Parking"},
        ),
    ]
    dlg = TruckCorrectionDialog(issues, fleet=set())
    assert dlg._tabs is not None
    assert dlg._tabs.count() == 2
    assert dlg._tabs.tabText(0) == "Incorrect trucks"
    assert dlg._tabs.tabText(1) == "Row details"

    rw = dlg._rows[0]
    assert rw.details_btn is not None
    dlg._peep_row(rw)
    assert dlg._tabs.currentIndex() == 1
    assert dlg._details_mode == "selected"
    assert dlg._details_focus_row == 0
    assert dlg._details_form is not None
    # Form has Row / Pasted / Issue + detail pairs
    assert dlg._details_form.rowCount() >= 4

    dlg._set_details_mode("all")
    assert dlg._details_table is not None
    assert dlg._details_table.rowCount() == 2
    assert dlg._details_table.item(0, 1).text() == "T111 AAA"
    assert "Toll" in dlg._details_table.item(0, 3).text()
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


def test_details_table_uses_master_expense_stripes(qapp) -> None:
    """Row details See-all table mirrors Master Expenses zebra colors."""
    from PySide6.QtGui import QColor

    from tahmeed.ui.dialogs.truck_correction_dialog import _ROW_EVEN, _ROW_ODD

    issues = [
        TruckIssue(row=0, original="T111 AAA", kind="not_in_registry"),
        TruckIssue(row=1, original="T222 BBB", kind="not_in_registry"),
        TruckIssue(row=2, original="T333 CCC", kind="not_in_registry"),
    ]
    dlg = TruckCorrectionDialog(issues, fleet=set())
    dlg._set_details_mode("all")
    table = dlg._details_table
    assert table is not None
    assert table.rowCount() == 3
    assert not table.alternatingRowColors()
    assert table.item(0, 0).background().color() == QColor(_ROW_EVEN)
    assert table.item(1, 0).background().color() == QColor(_ROW_ODD)
    assert table.item(2, 0).background().color() == QColor(_ROW_EVEN)
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
    assert "allow" in rw.kind_label.text().lower()
    assert "park" in rw.kind_label.text().lower()
    assert dlg._with_combo_suffix(rw.issue, "T724 CPQ") == "T724 CPQ/T631DZX/T632DZX"
    dlg.close()
