"""Enter in the mapping dialog must Assign & Continue, not Cancel."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from bson import ObjectId
from PySide6.QtWidgets import QApplication, QPushButton

from tahmeed.models.category import Category
from tahmeed.ui.dialogs.description_mapping_dialog import DescriptionMappingDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_assign_continue_is_default_enter_action() -> None:
    _app()
    cats = [Category(_id=ObjectId(), name="Parking")]
    dlg = DescriptionMappingDialog(
        description="PARKING KURASINI",
        row_count=3,
        categories=cats,
        remaining=5,
        total=5,
        cancel_label="Cancel",
    )
    buttons = dlg.findChildren(QPushButton)
    by_text = {b.text().replace("&", ""): b for b in buttons}
    assert "Assign  Continue" in by_text or "Assign & Continue" in by_text
    assign = next(b for b in buttons if "Assign" in b.text().replace("&", ""))
    cancel = next(b for b in buttons if b.text().replace("&", "") == "Cancel")
    assert assign.isDefault()
    assert not cancel.isDefault()
    assert not cancel.autoDefault()
    line = dlg._combo.lineEdit()
    assert line is not None
    # Typing an item then Enter should accept via the same path as the button.
    line.setText("Parking")
    line.returnPressed.emit()
    assert dlg.result() == DescriptionMappingDialog.Accepted
    assert dlg.selected_category() is not None
    assert dlg.selected_category().name == "Parking"
