"""Merged view: cashiers may only delete their own saved rows."""
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


def test_owns_saved_row_cashier_own_only(merged_register):
    assert merged_register._owns_saved_row(0) is True
    assert merged_register._owns_saved_row(1) is False


def test_owned_saved_rows_filters_others(merged_register):
    assert merged_register._owned_saved_rows([0, 1]) == [0]
    assert merged_register._owned_saved_rows([1]) == []


def test_accountant_owns_all_saved_rows(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = "acct-1"
    user.role = "accountant"
    reg = DailyRegister(user, [])
    reg._saved_count = 2
    reg._saved_ids = {0: "tx-a", 1: "tx-b"}
    reg._saved_txs = {
        0: SimpleNamespace(_id="tx-a", cashier_id="cashier-1", verified=False),
        1: SimpleNamespace(_id="tx-b", cashier_id="cashier-2", verified=False),
    }
    assert reg._owns_saved_row(0) is True
    assert reg._owns_saved_row(1) is True
    assert reg._owned_saved_rows([0, 1]) == [0, 1]


def test_delete_saved_rows_skips_others_without_api(merged_register, monkeypatch):
    """UI ownership filter must run before any delete API call."""
    called = []

    async def boom(*_a, **_k):
        called.append(True)
        raise AssertionError("should not call API for others-only selection")

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.request_or_delete_transaction", boom
    )
    prompts = []

    def fake_info(parent, title, text):
        prompts.append((title, text))
        return 0

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", fake_info)
    merged_register._delete_saved_rows([1])
    assert called == []
    assert prompts
    assert "own" in prompts[0][1].lower()
