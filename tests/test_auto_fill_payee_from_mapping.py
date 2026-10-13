"""Register auto-fill fills Item + Payee from Description Maps only (blank cells)."""

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
        _auto_fill_seq=0,
        _auto_fill_from_maps=True,
        _bulk_mutating=False,
        _validate_item_cell=lambda *_a, **_k: None,
        _update_truck_required_highlight=lambda *_a, **_k: None,
        _schedule_finalize_row_edit=lambda: None,
        _notify_day_issues_source_changed=lambda: None,
        _show_register_loading=lambda *_a, **_k: None,
        _hide_register_loading=lambda: None,
    )

    def _cell_text(row, col):
        it = table.item(row, col)
        return it.text().strip() if it is not None else ""

    stub._cell_text = _cell_text
    stub._apply_mapping_fields_to_row = DailyRegister._apply_mapping_fields_to_row.__get__(
        stub, DailyRegister
    )
    stub._auto_fill_from_mapping = DailyRegister._auto_fill_from_mapping.__get__(
        stub, DailyRegister
    )
    return stub


def _mapping(*, item: str, payee: str):
    return SimpleNamespace(category_name=item, payee=payee)


@pytest.mark.asyncio
async def test_auto_fill_sets_item_and_payee_when_blank(monkeypatch) -> None:
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("PARKING CONGO"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))

    async def fake_resolve(description: str):
        assert "PARKING" in description.upper()
        return _mapping(item="Parking", payee="PARKING DAR")

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_mapping_for_description",
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
        return _mapping(item="Parking", payee="PARKING DAR")

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_mapping_for_description",
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
        return _mapping(item="Spares", payee="ROADMASTERS SPARES LTD")

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_mapping_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "AC GAS")
    assert reg._table.item(0, COL_ITEM).text() == "Spares"
    assert reg._table.item(0, COL_PAYEE).text() == "ROADMASTERS SPARES LTD"


@pytest.mark.asyncio
async def test_auto_fill_no_map_leaves_blank(monkeypatch) -> None:
    """History-only descriptions must not invent Item/Payee without a map."""
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("UNMAPPED HISTORY DESC"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))

    async def fake_resolve(_description: str):
        return None

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_mapping_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "UNMAPPED HISTORY DESC")
    assert reg._table.item(0, COL_ITEM).text() == ""
    assert reg._table.item(0, COL_PAYEE).text() == ""


@pytest.mark.asyncio
async def test_auto_fill_uses_live_cell_description(monkeypatch) -> None:
    """Prefer committed cell text over the kick-time argument (Tab settle)."""
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("MAPPED FULL TEXT"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))
    seen = {}

    async def fake_resolve(description: str):
        seen["desc"] = description
        return _mapping(item="Parking", payee="CITY")

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_mapping_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "stale partial")
    assert seen["desc"] == "MAPPED FULL TEXT"
    assert reg._table.item(0, COL_ITEM).text() == "Parking"
