"""Re-assign selected description maps to Payee + Item."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from bson import ObjectId
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from tahmeed.models.category import Category
from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.ui.accountant.manage_description_maps import _MappingEditorDialog

_PAYEES = ["SPARE PARTS", "PARKING DAR", "TRIANGLE"]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mapping(
    description: str,
    item: str = "Parking",
    payee: str = "PARKING DAR",
) -> DescriptionMapping:
    return DescriptionMapping(
        description_key=description.upper(),
        description=description,
        category_id=ObjectId(),
        category_name=item,
        payee=payee,
        _id=ObjectId(),
    )


def _set_payee(dlg: _MappingEditorDialog, payee: str) -> None:
    idx = dlg._payee.findText(payee)
    if idx >= 0:
        dlg._payee.setCurrentIndex(idx)
    else:
        dlg._payee.setEditText(payee)


def test_edit_existing_item_assignment() -> None:
    _app()
    cats = [Category(_id=ObjectId(), name="Parking"), Category(_id=ObjectId(), name="Fuel")]
    dlg = _MappingEditorDialog(
        cats, mapping=_mapping("TRIANGLE", "Parking"), payees=_PAYEES,
    )
    _set_payee(dlg, "SPARE PARTS")
    dlg._combo.setCurrentIndex(1)
    dlg._accept()
    assert dlg.result() == QDialog.Accepted
    assignment = dlg.result_assignment
    assert assignment is not None
    assert assignment.create_new is False
    assert assignment.category is not None
    assert assignment.category.name == "Fuel"
    assert assignment.payee == "SPARE PARTS"
    assert dlg.result_description == "TRIANGLE"


def test_bulk_reassign_existing_item() -> None:
    _app()
    cats = [Category(_id=ObjectId(), name="Parking")]
    entries = [_mapping("TRIANGLE"), _mapping("LATRA"), _mapping("FUEL DAR")]
    dlg = _MappingEditorDialog(cats, mappings=entries, payees=_PAYEES)
    assert dlg.windowTitle() == "Re-assign 3 Mappings"
    assert dlg._desc.isHidden()
    # Shared Item + Payee across selection are prefilled for many→one edit.
    assert dlg._combo.currentText() == "Parking"
    assert dlg._payee.currentText() == "PARKING DAR"
    buttons = [b.text().replace("&", "") for b in dlg.findChildren(QPushButton)]
    assert "Re-assign" in buttons
    assert any("New Item" in text for text in buttons)
    _set_payee(dlg, "TRIANGLE")
    dlg._combo.setCurrentIndex(0)
    dlg._accept()
    assert dlg.result() == QDialog.Accepted
    assert dlg.result_assignment is not None
    assert dlg.result_assignment.create_new is False
    assert dlg.result_assignment.category is not None
    assert dlg.result_assignment.category.name == "Parking"
    assert dlg.result_assignment.payee == "TRIANGLE"


def test_bulk_reassign_mixed_items_leaves_item_blank() -> None:
    _app()
    cats = [
        Category(_id=ObjectId(), name="Parking"),
        Category(_id=ObjectId(), name="Fuel"),
    ]
    entries = [
        _mapping("TRIANGLE", item="Parking", payee="PARKING DAR"),
        _mapping("LATRA", item="Fuel", payee="PARKING DAR"),
    ]
    dlg = _MappingEditorDialog(cats, mappings=entries, payees=_PAYEES)
    assert dlg._combo.currentText() == ""
    assert dlg._payee.currentText() == "PARKING DAR"


def test_accept_requires_payee(monkeypatch) -> None:
    _app()
    warnings: list[str] = []
    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_description_maps.show_warning",
        lambda *_a, **_k: warnings.append("warned"),
    )
    cats = [Category(_id=ObjectId(), name="Parking")]
    dlg = _MappingEditorDialog(cats, mapping=_mapping("TRIANGLE"), payees=_PAYEES)
    dlg._payee.setCurrentIndex(-1)
    dlg._payee.setEditText("")
    dlg._combo.setCurrentIndex(0)
    dlg._accept()
    assert dlg.result() != QDialog.Accepted
    assert warnings == ["warned"]
    assert dlg.result_assignment is None


def test_reassign_to_new_item_opens_items_tab_dialog(monkeypatch) -> None:
    _app()
    captured = {}

    class _FakeItemDialog:
        def __init__(self, item=None, parent=None, prefill_name=""):
            captured["prefill"] = prefill_name
            self.result_data = {
                "name": "Council Fees",
                "description": "",
                "color": "#112233",
                "icon": "mdi.cash",
                "sidebar_name": "",
                "show_in_sidebar": False,
                "show_in_cashier_sidebar": False,
                "requires_receipt": True,
                "requires_truck": True,
                "lock_description": False,
            }

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_description_maps.ItemDialog",
        _FakeItemDialog,
    )
    entries = [_mapping("LATRA"), _mapping("COUNCIL DSM")]
    dlg = _MappingEditorDialog([], mappings=entries, payees=_PAYEES)
    _set_payee(dlg, "SPARE PARTS")
    dlg._on_assign_new()
    assert captured["prefill"] == ""
    assert dlg.result() == QDialog.Accepted
    assignment = dlg.result_assignment
    assert assignment is not None
    assert assignment.create_new is True
    assert assignment.new_item_name == "Council Fees"
    assert assignment.payee == "SPARE PARTS"
    assert assignment.new_item_fields["color"] == "#112233"
    assert assignment.new_item_fields["requires_receipt"] is True


def test_single_edit_new_item_prefills_description(monkeypatch) -> None:
    _app()
    captured = {}

    class _FakeItemDialog:
        def __init__(self, item=None, parent=None, prefill_name=""):
            captured["prefill"] = prefill_name
            self.result_data = {"name": "Parking", "color": "#4A90D9"}

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_description_maps.ItemDialog",
        _FakeItemDialog,
    )
    dlg = _MappingEditorDialog(
        [], mapping=_mapping("PARKING KURASINI"), payees=_PAYEES,
    )
    _set_payee(dlg, "PARKING DAR")
    dlg._on_assign_new()
    assert captured["prefill"] == "PARKING KURASINI"
    assert dlg.result_assignment is not None
    assert dlg.result_assignment.create_new is True
    assert dlg.result_assignment.payee == "PARKING DAR"


def test_toolbar_sits_on_search_row() -> None:
    _app()
    from tahmeed.ui.accountant.manage_description_maps import DescriptionMapsWidget

    widget = DescriptionMapsWidget()
    assert widget._search.parent() is widget._reassign_btn.parent()
    assert widget._search.parent() is widget._delete_btn.parent()
    assert not hasattr(widget, "_next_btn")
    assert not hasattr(widget, "_prev_btn")
    assert widget._table.columnCount() == 4
    headers = [
        widget._table.horizontalHeaderItem(i).text()
        for i in range(widget._table.columnCount())
    ]
    assert headers == ["#", "Description", "Item", "Payee"]
    widget._rows = [
        _mapping("PARKING KURASINI 7TH OCTB", item="Parking", payee="PARKING DAR"),
    ]
    widget._rebuild_table()
    assert widget._table.item(0, 1).text() == "PARKING KURASINI 7TH OCTB"
    assert widget._table.item(0, 2).text() == "Parking"
    assert widget._table.item(0, 3).text() == "PARKING DAR"
    widget._update_status()
    assert "Showing" in widget._footer.text()


def test_add_mapping_new_item_requires_description(monkeypatch) -> None:
    _app()
    opened = {"called": False}
    warnings: list[str] = []

    class _FakeItemDialog:
        def __init__(self, *a, **k):
            opened["called"] = True

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_description_maps.ItemDialog",
        _FakeItemDialog,
    )
    monkeypatch.setattr(
        "tahmeed.ui.accountant.manage_description_maps.show_warning",
        lambda *_a, **_k: warnings.append("warned"),
    )
    dlg = _MappingEditorDialog([], payees=_PAYEES)
    dlg._on_assign_new()
    assert opened["called"] is False
    assert warnings == ["warned"]
    dlg._desc.setText("TRIANGLE")
    # Still blocked until payee is set
    dlg._on_assign_new()
    assert opened["called"] is False
    assert len(warnings) == 2
    _set_payee(dlg, "TRIANGLE")
    dlg._on_assign_new()
    assert opened["called"] is True
