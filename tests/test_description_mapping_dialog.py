"""Enter in the mapping dialog must Assign & Continue, not Cancel."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from bson import ObjectId
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

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
    assign = next(b for b in buttons if "Continue" in b.text().replace("&", ""))
    new_item = next(b for b in buttons if "New Item" in b.text().replace("&", ""))
    cancel = next(b for b in buttons if b.text().replace("&", "") == "Cancel")
    assert assign.isDefault()
    assert not cancel.isDefault()
    assert not cancel.autoDefault()
    assert not new_item.isDefault()
    assert not new_item.autoDefault()
    line = dlg._combo.lineEdit()
    assert line is not None
    # Typing an item then Enter should accept via the same path as the button.
    line.setText("Parking")
    line.returnPressed.emit()
    assert dlg.result() == DescriptionMappingDialog.Accepted
    assert dlg.selected_category() is not None
    assert dlg.selected_category().name == "Parking"


def test_assign_to_new_item_opens_items_tab_dialog(monkeypatch) -> None:
    _app()
    captured = {}

    class _FakeItemDialog:
        def __init__(self, item=None, parent=None, prefill_name=""):
            captured["prefill"] = prefill_name
            self.result_data = {
                "name": "Parking",
                "description": "",
                "color": "#112233",
                "icon": "mdi.parking",
                "sidebar_name": "",
                "show_in_sidebar": True,
                "show_in_cashier_sidebar": False,
                "requires_receipt": True,
                "requires_truck": False,
                "lock_description": True,
            }

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.dialogs.description_mapping_dialog.ItemDialog",
        _FakeItemDialog,
    )
    dlg = DescriptionMappingDialog(
        description="PARKING KURASINI",
        row_count=2,
        categories=[],
        remaining=1,
        total=1,
        cancel_label="Cancel",
    )
    dlg._on_assign_new()
    assert captured["prefill"] == "PARKING KURASINI"
    assert dlg.result() == DescriptionMappingDialog.Accepted
    assignment = dlg.assignment()
    assert assignment.create_new is True
    assert assignment.new_item_name == "Parking"
    assert assignment.new_item_fields["color"] == "#112233"
    assert assignment.new_item_fields["lock_description"] is True
    assert assignment.new_item_fields["requires_receipt"] is True


def test_existing_item_assignment() -> None:
    _app()
    cats = [Category(_id=ObjectId(), name="Parking")]
    dlg = DescriptionMappingDialog(
        description="PARKING KURASINI",
        row_count=1,
        categories=cats,
        remaining=1,
        cancel_label="Cancel",
    )
    dlg._combo.setCurrentIndex(0)
    dlg._on_assign()
    assignment = dlg.assignment()
    assert assignment.create_new is False
    assert assignment.category is not None
    assert assignment.category.name == "Parking"
