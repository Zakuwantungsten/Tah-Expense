"""Paste bulk auto-fill from Description Maps (blank cells only; setting-gated)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.ui.cashier.excel_grid import DailyRegister
from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_PAYEE


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _stub_register(rows: int = 3) -> SimpleNamespace:
    table = QTableWidget(rows, max(COL_ITEM, COL_PAYEE, COL_DESC) + 1)
    stub = SimpleNamespace(
        _table=table,
        _auto_fill_row=None,
        _auto_fill_seq=0,
        _auto_fill_from_maps=True,
        _bulk_mutating=False,
        _saved_count=0,
        _edit_mode=False,
        _validate_item_cell=lambda *_a, **_k: None,
        _update_truck_required_highlight=lambda *_a, **_k: None,
        _schedule_finalize_row_edit=lambda: None,
        _schedule_draft_autosave=lambda: None,
        _mark_dirty=lambda *_a, **_k: None,
        _notify_day_issues_source_changed=lambda: None,
        _show_register_loading=lambda *_a, **_k: None,
        _hide_register_loading=lambda: None,
        _flush_calls=0,
    )

    def _cell_text(row, col):
        it = table.item(row, col)
        return it.text() if it is not None else ""

    def _flush(*, force=False):
        stub._flush_calls += 1

    stub._cell_text = _cell_text
    stub._flush_day_issues_source_changed = _flush
    stub._apply_mapping_fields_to_row = DailyRegister._apply_mapping_fields_to_row.__get__(
        stub, DailyRegister
    )
    stub._auto_fill_from_mapping = DailyRegister._auto_fill_from_mapping.__get__(
        stub, DailyRegister
    )
    stub._auto_fill_rows_from_mappings = (
        DailyRegister._auto_fill_rows_from_mappings.__get__(stub, DailyRegister)
    )
    return stub


@pytest.mark.asyncio
async def test_bulk_paste_autofill_applies_maps_to_blank_cells(monkeypatch) -> None:
    _app()
    reg = _stub_register()
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("LATRA FEE"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))
    reg._table.setItem(1, COL_DESC, QTableWidgetItem("UNKNOWN DESC"))
    reg._table.setItem(1, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(1, COL_PAYEE, QTableWidgetItem(""))
    reg._table.setItem(2, COL_DESC, QTableWidgetItem("DIESEL"))
    reg._table.setItem(2, COL_ITEM, QTableWidgetItem("Manual Item"))
    reg._table.setItem(2, COL_PAYEE, QTableWidgetItem(""))

    mapping = SimpleNamespace(
        category_name="Parking",
        payee="CITY COUNCIL",
    )
    diesel = SimpleNamespace(category_name="Fuel", payee="SHELL")

    async def fake_get(descs):
        out = {}
        for d in descs:
            key = normalize_description(d)
            if key == normalize_description("LATRA FEE"):
                out[key] = mapping
            if key == normalize_description("DIESEL"):
                out[key] = diesel
        return out

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.get_mappings_for_descriptions",
        fake_get,
    )

    await reg._auto_fill_rows_from_mappings([0, 1, 2])

    assert reg._table.item(0, COL_ITEM).text() == "Parking"
    assert reg._table.item(0, COL_PAYEE).text() == "CITY COUNCIL"
    assert reg._table.item(1, COL_ITEM).text() == ""
    assert reg._table.item(1, COL_PAYEE).text() == ""
    # Existing Item preserved; blank Payee filled.
    assert reg._table.item(2, COL_ITEM).text() == "Manual Item"
    assert reg._table.item(2, COL_PAYEE).text() == "SHELL"
    assert reg._flush_calls == 1


@pytest.mark.asyncio
async def test_bulk_autofill_skipped_when_setting_off(monkeypatch) -> None:
    _app()
    reg = _stub_register(1)
    reg._auto_fill_from_maps = False
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("LATRA FEE"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))

    called = {"n": 0}

    async def fake_get(_descs):
        called["n"] += 1
        return {}

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.get_mappings_for_descriptions",
        fake_get,
    )

    await reg._auto_fill_rows_from_mappings([0])
    assert called["n"] == 0
    assert reg._table.item(0, COL_ITEM).text() == ""
    assert reg._flush_calls == 1


@pytest.mark.asyncio
async def test_single_autofill_respects_setting_off(monkeypatch) -> None:
    _app()
    reg = _stub_register(1)
    reg._auto_fill_from_maps = False
    reg._table.setItem(0, COL_DESC, QTableWidgetItem("LATRA FEE"))
    reg._table.setItem(0, COL_ITEM, QTableWidgetItem(""))
    reg._table.setItem(0, COL_PAYEE, QTableWidgetItem(""))

    called = {"n": 0}

    async def fake_resolve(_d):
        called["n"] += 1
        return SimpleNamespace(category_name="Parking", payee="CITY COUNCIL")

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_mapping_for_description",
        fake_resolve,
    )
    await reg._auto_fill_from_mapping(0, "LATRA FEE")
    assert called["n"] == 0
    assert reg._table.item(0, COL_ITEM).text() == ""
