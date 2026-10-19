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
