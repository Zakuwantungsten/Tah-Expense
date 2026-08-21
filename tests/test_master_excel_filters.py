"""Master Expenses TZS/USD split + Excel header filter helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from tahmeed.ui.accountant.master_expenses import _amount_cells, _COLS, _COL_TZS, _COL_USD
from tahmeed.ui.widgets.excel_column_filter import (
    ExcelColumnFilterPopup,
    SORT_ASC,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_master_columns_include_tzs_and_usd_side_by_side() -> None:
    labels = [c[0] for c in _COLS]
    assert labels[_COL_TZS] == "TZS"
    assert labels[_COL_USD] == "USD"
    assert _COL_USD == _COL_TZS + 1


def test_amount_cells_split_by_currency() -> None:
    from datetime import datetime
    from tahmeed.models.transaction import Transaction

    tzs_tx = Transaction(
        date=datetime(2026, 1, 1),
        description="a",
        truck_number="",
        amount=-12000,
        currency="TZS",
    )
    usd_tx = Transaction(
        date=datetime(2026, 1, 1),
        description="b",
        truck_number="",
        amount=45.5,
        currency="USD",
    )
    assert _amount_cells(tzs_tx) == ("-12,000", "—")
    assert _amount_cells(usd_tx) == ("—", "45.50")


def test_amount_cells_dual_tzs_and_usd() -> None:
    from datetime import datetime
    from tahmeed.models.transaction import Transaction

    dual = Transaction(
        date=datetime(2026, 1, 1),
        description="c",
        truck_number="",
        amount=10000,
        currency="TZS",
        amount_usd=25.5,
    )
    assert _amount_cells(dual) == ("10,000", "25.50")


def test_excel_popup_sort_a_to_z_emits_asc() -> None:
    _app()
    seen = {"sort": None}
    popup = ExcelColumnFilterPopup(
        {"Parking", "Fuel", "Bonus"},
        {"Parking"},
        column_label="ITEM",
        sort_kind="text",
    )
    popup.sort_requested.connect(lambda m: seen.__setitem__("sort", m))
    sort_btns = [b for b in popup.findChildren(QPushButton) if "A → Z" in b.text()]
    assert sort_btns
    sort_btns[0].click()
    assert seen["sort"] == SORT_ASC
