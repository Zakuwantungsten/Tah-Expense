"""Manage Payees Amount column + currency on create + QuickReport open."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox

from tahmeed.ui.accountant.manage_payees import (
    ManagePayeesWidget,
    _AddPayeeDialog,
    _money,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_money_formats_tzs_and_usd() -> None:
    assert _money(0, 0) == "—"
    assert _money(1200, 0) == "TZS 1,200"
    assert "USD 10.50" in _money(0, 10.5)
    assert "TZS 100" in _money(100, 2.25)
    assert "USD 2.25" in _money(100, 2.25)


def test_add_payee_dialog_defaults_currency_to_tzs() -> None:
    _app()
    dlg = _AddPayeeDialog()
    assert dlg.result_currency == "TZS"
    currency = dlg.findChildren(QComboBox)[0]
    assert currency.currentText() == "TZS"
    dlg._inp.setText("city council")
    currency.setCurrentText("USD")
    dlg._accept()
    assert dlg.result_name == "CITY COUNCIL"
    assert dlg.result_currency == "USD"
    dlg.deleteLater()


@pytest.mark.asyncio
async def test_load_fetches_usage_and_opens_report(monkeypatch) -> None:
    _app()
    widget = ManagePayeesWidget()
    opened: list[str] = []

    async def _fake_list(**_kwargs):
        return [
            {"name": "SPARE PARTS", "currency": "TZS", "active": True},
            {"name": "PARKING DAR", "currency": "USD", "active": True},
        ]

    async def _fake_count(**_kwargs):
        return 2

    async def _fake_usage(names):
        assert "SPARE PARTS" in names
        return {
            "spare parts": {"count": 2, "tzs": 1500.0, "usd": 0.0},
            "parking dar": {"count": 1, "tzs": 0.0, "usd": 40.0},
        }

    async def _fake_restrict():
        return None

    monkeypatch.setattr(
        "tahmeed.services.payee_service.list_payees", _fake_list,
    )
    monkeypatch.setattr(
        "tahmeed.services.payee_service.count_payees", _fake_count,
    )
    monkeypatch.setattr(
        "tahmeed.services.accountant_service.get_payees_usage_totals",
        _fake_usage,
    )
    monkeypatch.setattr(widget, "_load_restrict_setting", _fake_restrict)
    monkeypatch.setattr(widget, "_open_report", lambda name: opened.append(name))

    await widget._load()

    assert widget._table.columnCount() == 5
    assert widget._table.horizontalHeaderItem(4).text() == "Amount"
    assert widget._table.item(0, 4).text() == "TZS 1,500"
    assert "USD 40.00" in widget._table.item(1, 4).text()
    # Status is plain text (no Active/Inactive badge widget).
    assert widget._table.item(0, 3).text() == "Active"
    assert widget._table.cellWidget(0, 3) is None
    assert "Showing 2 of 2" in widget._page_info.text()
    assert not hasattr(widget, "_prev_btn")
    assert not hasattr(widget, "_next_btn")

    widget._on_row_double_clicked(0, 1)
    assert opened == ["SPARE PARTS"]
    widget.deleteLater()


@pytest.mark.asyncio
async def test_load_more_appends_rows(monkeypatch) -> None:
    _app()
    widget = ManagePayeesWidget()
    calls: list[dict] = []

    async def _fake_list(**kwargs):
        calls.append(dict(kwargs))
        skip = int(kwargs.get("skip") or 0)
        if skip == 0:
            return [{"name": "A", "currency": "TZS", "active": True}]
        return [{"name": "B", "currency": "USD", "active": False}]

    async def _fake_count(**_kwargs):
        return 2

    async def _fake_usage(_names):
        return {}

    async def _fake_restrict():
        return None

    monkeypatch.setattr("tahmeed.services.payee_service.list_payees", _fake_list)
    monkeypatch.setattr("tahmeed.services.payee_service.count_payees", _fake_count)
    monkeypatch.setattr(
        "tahmeed.services.accountant_service.get_payees_usage_totals",
        _fake_usage,
    )
    monkeypatch.setattr(widget, "_load_restrict_setting", _fake_restrict)
    # Avoid recursive fill-if-needed while asserting a single append.
    monkeypatch.setattr(widget, "_fill_if_needed", lambda: None)

    await widget._load()
    assert widget._table.rowCount() == 1
    assert widget._table.item(0, 1).text() == "A"

    await widget._load_more()
    assert widget._table.rowCount() == 2
    assert widget._table.item(1, 1).text() == "B"
    assert widget._table.item(1, 3).text() == "Inactive"
    assert any(c.get("skip") == 1 for c in calls)
    assert "Showing 2 of 2" in widget._page_info.text()
    widget.deleteLater()


@pytest.mark.asyncio
async def test_do_add_passes_currency(monkeypatch) -> None:
    _app()
    widget = ManagePayeesWidget()
    captured: dict = {}

    async def _fake_add(name, *, currency="TZS", ref_num=""):
        captured["name"] = name
        captured["currency"] = currency

    async def _fake_load():
        return None

    monkeypatch.setattr("tahmeed.services.payee_service.add_payee", _fake_add)
    monkeypatch.setattr(widget, "_load", _fake_load)

    await widget._do_add("LAKE OIL", "USD")
    assert captured == {"name": "LAKE OIL", "currency": "USD"}
    widget.deleteLater()
