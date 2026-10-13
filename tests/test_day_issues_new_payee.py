"""Day Issues mapping dialog can introduce a new payee like New Item."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

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
    assert payee_needs_create("BRAND NEW PAYEE", dlg._known_payees_at_open) is True
    dlg.close()
