"""Daily Register table font zoom (Larger / Smaller toolbar)."""
from __future__ import annotations

import sys
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
def register(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid._load_table_font_pt",
        lambda: 11,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid._save_table_font_pt",
        lambda _pt: None,
    )
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = "cashier-1"
    return DailyRegister(user, [])


def test_toolbar_font_larger_smaller_clamps(register):
    from tahmeed.ui.cashier.excel_grid import (
        _TABLE_FONT_PT_MAX,
        _TABLE_FONT_PT_MIN,
    )

    assert register._table_font_pt == 11
    assert register.can_increase_font()
    assert register.can_decrease_font()

    register.toolbar_font_larger()
    assert register._table_font_pt == 12
    assert "font-size: 12pt" in register._table.styleSheet()
    assert register._table.verticalHeader().defaultSectionSize() == (
        register._row_height_for_font(12)
    )

    register.toolbar_font_smaller()
    assert register._table_font_pt == 11

    register._table_font_pt = _TABLE_FONT_PT_MAX
    register._apply_table_font_size(persist=False, emit=False)
    register.toolbar_font_larger()
    assert register._table_font_pt == _TABLE_FONT_PT_MAX
    assert not register.can_increase_font()

    register._table_font_pt = _TABLE_FONT_PT_MIN
    register._apply_table_font_size(persist=False, emit=False)
    register.toolbar_font_smaller()
    assert register._table_font_pt == _TABLE_FONT_PT_MIN
    assert not register.can_decrease_font()


def test_qb_toolbar_exposes_font_buttons():
    from tahmeed.ui.widgets.qb_txn_toolbar import QbTxnToolbar

    bar = QbTxnToolbar(register_actions=True)
    assert bar._btn_font_larger is not None
    assert bar._btn_font_smaller is not None
    bar.set_font_size_controls_enabled(can_larger=False, can_smaller=True)
    assert not bar._btn_font_larger.isEnabled()
    assert bar._btn_font_smaller.isEnabled()

    form_bar = QbTxnToolbar(register_actions=False)
    assert form_bar._btn_font_larger is None
    assert form_bar._btn_font_smaller is None
