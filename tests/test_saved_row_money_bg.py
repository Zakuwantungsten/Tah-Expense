"""Saved rows: TZS / USD must use the same saved/draft row background."""
from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from PySide6.QtGui import QColor


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = ObjectId()
    user.role = "cashier"
    user.username = "cash1"
    return DailyRegister(user, [])


def test_fill_saved_row_tzs_usd_use_saved_background(register):
    from tahmeed.models.transaction import Transaction
    from tahmeed.ui.cashier.register_delegates import (
        COL_DESC,
        COL_TZS,
        COL_USD,
        SAVED_BG,
    )

    tx = Transaction(
        date=datetime(2026, 10, 8),
        description="REFUND TO FLOAT",
        truck_number="T1",
        amount=202500.0,
        currency="TZS",
        register_status="submitted",
    )
    tx._id = ObjectId()
    register._table.setRowCount(1)
    register._fill_saved_row(0, tx)

    desc_bg = register._table.item(0, COL_DESC).background().color()
    tzs_bg = register._table.item(0, COL_TZS).background().color()
    usd_bg = register._table.item(0, COL_USD).background().color()
    assert desc_bg == QColor(SAVED_BG)
    assert tzs_bg == QColor(SAVED_BG)
    assert usd_bg == QColor(SAVED_BG)
    assert register._table.item(0, COL_TZS).text() == "202,500.00"
