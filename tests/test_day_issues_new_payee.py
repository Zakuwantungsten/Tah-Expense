"""Day Issues mapping dialog can introduce a new payee like New Item."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tahmeed.models.category import Category
from tahmeed.ui.cashier.day_issues import MapDescriptionsDialog, payee_needs_create


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_payee_needs_create_normalizes_case_and_spaces() -> None:
    assert payee_needs_create("  new co  ", ["CITY"]) is True
    assert payee_needs_create("CITY", ["city"]) is False


def test_map_dialog_select_payee_marks_create(qapp) -> None:
    cats = [Category(name="Parking")]
    dlg = MapDescriptionsDialog(
        ["LATRA FEE"],
        cats,
        ["CITY COUNCIL"],
    )
    assert dlg.result_create_payee is False
    dlg._select_payee("BRAND NEW PAYEE", mark_create=True)
    assert "BRAND NEW PAYEE" in dlg._payees
    assert "BRAND NEW PAYEE" in dlg._pending_new_payees
    assert dlg._payee.text() == "BRAND NEW PAYEE"
    assert payee_needs_create("BRAND NEW PAYEE", dlg._known_payees_at_open) is True
    dlg.close()


def test_map_dialog_tab_accepts_first_suggestion(qapp) -> None:
    """Tab commits the first completer match, then moves focus to the next field."""
    cats = [Category(name="Parking Fee"), Category(name="Diesel")]
    dlg = MapDescriptionsDialog(
        ["LATRA FEE"],
        cats,
        ["CITY COUNCIL", "SHELL OIL"],
    )
    dlg.show()
    qapp.processEvents()

    payee = dlg._payee
    payee.setFocus()
    payee.setText("cit")
    payee._typed = "cit"
    payee._on_text_edited("cit")
    payee._completer.setCompletionPrefix("cit")
    payee._completer.complete()
    payee._apply_first_suggestion_preview()
    qapp.processEvents()

    QTest.keyClick(payee, Qt.Key_Tab)
    qapp.processEvents()
    assert payee.text() == "CITY COUNCIL"
    assert dlg.focusWidget() is dlg._item

    item = dlg._item
    item.setText("park")
    item._typed = "park"
    item._on_text_edited("park")
    item._completer.setCompletionPrefix("park")
    item._completer.complete()
    item._apply_first_suggestion_preview()
    qapp.processEvents()

    QTest.keyClick(item, Qt.Key_Tab)
    qapp.processEvents()
    assert item.text() == "Parking Fee"
    dlg.close()


def test_new_item_joins_suggestions_before_payee_is_filled(qapp) -> None:
    """New Item stays in the open dialog and is offered while typing."""
    dlg = MapDescriptionsDialog(
        ["LATRA FEE"],
        [Category(name="Parking")],
        ["CITY COUNCIL"],
    )
    dlg._select_item(
        "Custom Bonds",
        fields={"name": "Custom Bonds", "color": "#112233"},
        mark_create=True,
    )
    assert dlg._payee.text() == ""
    assert dlg.result_assignment is None
    assert "Custom Bonds" in dlg._item._values
    assert dlg._item.text() == "Custom Bonds"
    assert "custom bonds" in dlg._pending_new_items

    dlg._item.setText("cust")
    dlg._item._typed = "cust"
    dlg._item._on_text_edited("cust")
    ranked = dlg._item._model.stringList()
    assert ranked[0] == "Custom Bonds"
    dlg._select_item(
        "Custom Bonds",
        fields={"name": "Custom Bonds", "color": "#112233"},
        mark_create=True,
    )

    dlg._select_payee("BONDS DESK", mark_create=True)
    dlg._accept()
    assignment = dlg.result_assignment
    assert assignment is not None
    assert assignment.create_new is True
    assert assignment.new_item_name == "Custom Bonds"
    assert assignment.new_item_fields["color"] == "#112233"
    assert dlg.result_payee == "BONDS DESK"
    dlg.close()


def _bulk_dialog(monkeypatch, descriptions, answer=None):
    from bson import ObjectId
    from PySide6.QtWidgets import QMessageBox

    from tahmeed.ui.cashier import day_issues as di
    from tahmeed.ui.dialogs import import_session_dialogs as dlg_mod

    def no_form(*_a, **_k):
        raise AssertionError("no Add Item / Add Payee form in bulk mode")

    def fake_exec(preview) -> int:
        preview._on_accept()
        return preview.result()

    monkeypatch.setattr(di, "ItemDialog", no_form)
    monkeypatch.setattr(di, "_NewPayeeDialog", no_form)
    monkeypatch.setattr(
        dlg_mod, "show_question",
        lambda *_a, **_k: answer or QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(di.BulkMapPreviewDialog, "exec", fake_exec)
    return MapDescriptionsDialog(
        descriptions,
        [Category(name="Parking", _id=ObjectId())],
        ["CITY COUNCIL"],
    )


def test_bulk_payee_per_description(qapp, monkeypatch) -> None:
    dlg = _bulk_dialog(monkeypatch, ["JOHN DOE", "city council", "MARY  ANN"])
    dlg._payee_each.setChecked(True)
    assert not dlg._payee.isEnabled()
    dlg._item.setText("parking")
    dlg._accept()
    assert dlg.result_assignment is None
    assert dlg.result_rows == [
        ("JOHN DOE", "JOHN DOE", "Parking"),
        ("city council", "CITY COUNCIL", "Parking"),
        ("MARY  ANN", "MARY ANN", "Parking"),
    ]
    assert dlg.result_new_payees == {"JOHN DOE": "TZS", "MARY ANN": "TZS"}
    assert dlg.result_new_items == {}
    dlg.close()


def test_bulk_item_per_description_needs_no_forms(qapp, monkeypatch) -> None:
    from tahmeed.ui.dialogs.import_session_dialogs import BULK_ITEM_FIELDS

    dlg = _bulk_dialog(monkeypatch, ["GET VALVE", "parking", "TYRE REPAIR"])
    dlg._item_each.setChecked(True)
    dlg._payee.setText("new garage")
    dlg._accept()
    assert [r[2] for r in dlg.result_rows] == ["GET VALVE", "Parking", "TYRE REPAIR"]
    assert {r[1] for r in dlg.result_rows} == {"NEW GARAGE"}
    assert dlg.result_new_payees == {"NEW GARAGE": "TZS"}
    assert dlg.result_new_items == {
        "GET VALVE": {**BULK_ITEM_FIELDS, "name": "GET VALVE"},
        "TYRE REPAIR": {**BULK_ITEM_FIELDS, "name": "TYRE REPAIR"},
    }
    dlg.close()


def test_bulk_declined_confirmation_saves_nothing(qapp, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    dlg = _bulk_dialog(
        monkeypatch, ["GET VALVE"], answer=QMessageBox.StandardButton.No
    )
    dlg._item_each.setChecked(True)
    dlg._payee.setText("city council")
    dlg._accept()
    assert dlg.result_rows == [] and dlg.result_assignment is None
    dlg.close()
