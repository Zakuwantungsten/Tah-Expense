"""Daily Register Excel-style column hide / unhide."""
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


def test_hide_column_and_unhide_via_span(register):
    from tahmeed.ui.cashier.register_delegates import (
        COL_MEMO, COL_REF, COL_SNO, COL_TRUCK,
    )

    assert not register._table.isColumnHidden(COL_MEMO)
    register._hide_columns([COL_MEMO])
    assert register._table.isColumnHidden(COL_MEMO)
    assert COL_MEMO in register._user_hidden_cols

    # S/NO stays locked
    register._hide_columns([COL_SNO])
    assert not register._table.isColumnHidden(COL_SNO)

    # Truck → Ref_Float spans Memo
    register._unhide_columns(
        register._table.horizontalHeader()._hidden_between(COL_TRUCK, COL_REF)
    )
    assert not register._table.isColumnHidden(COL_MEMO)
    assert COL_MEMO not in register._user_hidden_cols


def test_hide_multiple_columns(register):
    from tahmeed.ui.cashier.register_delegates import COL_MEMO, COL_OWN, COL_REF

    register._hide_columns([COL_MEMO, COL_REF, COL_OWN])
    assert register._table.isColumnHidden(COL_MEMO)
    assert register._table.isColumnHidden(COL_REF)
    assert register._table.isColumnHidden(COL_OWN)
    register._unhide_columns([COL_MEMO, COL_REF, COL_OWN])
    assert not register._table.isColumnHidden(COL_MEMO)


def test_gap_hit_detects_hidden_between(register):
    from tahmeed.ui.cashier.register_delegates import COL_MEMO, COL_REF, COL_TRUCK

    hdr = register._table.horizontalHeader()
    register._hide_columns([COL_MEMO])
    # Right edge of Truck should report Memo as gap-hidden
    truck_x = hdr.sectionViewportPosition(COL_TRUCK)
    truck_w = hdr.sectionSize(COL_TRUCK)
    gap = hdr._gap_hidden_cols_at(COL_TRUCK, truck_x + truck_w - 2)
    assert COL_MEMO in gap

    # Left edge of Ref_Float should also report Memo
    ref_x = hdr.sectionViewportPosition(COL_REF)
    gap_left = hdr._gap_hidden_cols_at(COL_REF, ref_x + 1)
    assert COL_MEMO in gap_left


def test_cashier_visibility_preserved_when_hiding(register):
    from tahmeed.ui.cashier.register_delegates import COL_CASHIER, COL_MEMO

    register._apply_column_visibility(show_cashier=True)
    assert not register._table.isColumnHidden(COL_CASHIER)
    register._hide_columns([COL_MEMO])
    assert not register._table.isColumnHidden(COL_CASHIER)
    assert register._table.isColumnHidden(COL_MEMO)
