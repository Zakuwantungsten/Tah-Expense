"""Register auto-fill fills Item + Payee from description maps (blank cells only)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from tahmeed.ui.cashier.excel_grid import DailyRegister
from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_PAYEE


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _stub_register() -> SimpleNamespace:
    """Minimal stand-in so we can call DailyRegister._auto_fill_from_mapping."""
    table = QTableWidget(1, max(COL_ITEM, COL_PAYEE, COL_DESC) + 1)
    stub = SimpleNamespace(
        _table=table,
        _auto_fill_row=None,
        _bulk_mutating=False,
        _validate_item_cell=lambda *_a, **_k: None,
        _update_truck_required_highlight=lambda *_a, **_k: None,
        _schedule_finalize_row_edit=lambda: None,
    )
    stub._auto_fill_from_mapping = DailyRegister._auto_fill_from_mapping.__get__(
        stub, DailyRegister
    )
    return stub


@pytest.mark.asyncio
async def test_auto_fill_sets_item_and_payee_when_blank(monkeypatch) -> None:
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("PARKING CONGO"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))

    async def fake_resolve(description: str):
        assert "PARKING" in description.upper()
        return "Parking", "PARKING DAR"

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.resolve_mapping_fields_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "PARKING CONGO")
    assert reg._table.item(0, COL_ITEM).text() == "Parking"
    assert reg._table.item(0, COL_PAYEE).text() == "PARKING DAR"


@pytest.mark.asyncio
async def test_auto_fill_preserves_existing_payee(monkeypatch) -> None:
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("PARKING CONGO"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem("MANUAL PAYEE"))

    async def fake_resolve(description: str):
        return "Parking", "PARKING DAR"

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.resolve_mapping_fields_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "PARKING CONGO")
    assert reg._table.item(0, COL_ITEM).text() == "Parking"
    assert reg._table.item(0, COL_PAYEE).text() == "MANUAL PAYEE"


@pytest.mark.asyncio
async def test_auto_fill_fills_payee_when_item_already_set(monkeypatch) -> None:
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("AC GAS"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem("Spares"))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))

    async def fake_resolve(description: str):
        return "Spares", "ROADMASTERS SPARES LTD"

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.resolve_mapping_fields_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "AC GAS")
    assert reg._table.item(0, COL_ITEM).text() == "Spares"
    assert reg._table.item(0, COL_PAYEE).text() == "ROADMASTERS SPARES LTD"
