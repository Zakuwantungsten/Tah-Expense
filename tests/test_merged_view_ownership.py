"""Merged / shared Parked: any cashier may delete any saved row of the day."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def merged_register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = "cashier-1"
    user.role = "cashier"
    reg = DailyRegister(user, [])
    reg._merged_mode = True
    reg._saved_count = 2
    reg._saved_ids = {0: "tx-mine", 1: "tx-other"}
    reg._saved_txs = {
        0: SimpleNamespace(_id="tx-mine", cashier_id="cashier-1", verified=False),
        1: SimpleNamespace(_id="tx-other", cashier_id="cashier-2", verified=False),
    }
    return reg


def test_cashier_can_delete_any_saved_row(merged_register):
    assert merged_register._can_delete_saved_row(0) is True
    assert merged_register._can_delete_saved_row(1) is True
    assert merged_register._can_delete_saved_row(5) is False


def test_deletable_saved_rows_include_other_cashiers(merged_register):
    assert merged_register._deletable_saved_rows([0, 1]) == [0, 1]
    assert merged_register._deletable_saved_rows([1]) == [1]


def test_delete_other_cashiers_row_calls_api(merged_register, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    deleted: list = []
    monkeypatch.setattr(
        merged_register, "_do_delete_saved_many", lambda tx_ids: deleted.extend(tx_ids)
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr("tahmeed.ui.async_utils.schedule_coro", lambda _coro: None)

    merged_register._delete_saved_rows([1])

    assert deleted == ["tx-other"]
