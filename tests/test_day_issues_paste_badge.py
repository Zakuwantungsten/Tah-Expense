"""Paste/bulk Table edits must refresh the Day Issues badge (one coalesced emit)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
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
    user._id = "cashier-1"
    reg = DailyRegister(user, [])
    reg._saved_count = 0
    reg._current_date = __import__("datetime").date(2026, 3, 15)
    return reg


def test_paste_emits_day_issues_source_changed_once(register, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    emits: list[int] = []
    register.day_issues_source_changed.connect(lambda: emits.append(1))

    # Start paste below saved rows on the Description column.
    register._table.setCurrentCell(0, COL_DESC)
    QGuiApplication.clipboard().setText("LATRA FEE\nDIESEL FILL\nTOLLS")

    # Avoid truck dialogs / draft IO during the test.
    monkeypatch.setattr(register, "_finalize_truck_cells", lambda *_a, **_k: None)
    monkeypatch.setattr(register, "_schedule_draft_autosave", lambda: None)
    # Paste now schedules async map auto-fill then flush — collapse to sync flush.
    monkeypatch.setattr(
        register,
        "_schedule_bulk_auto_fill_from_maps",
        lambda _rows: register._flush_day_issues_source_changed(force=True),
    )

    register._paste()

    assert len(emits) == 1
    assert register._table.item(0, COL_DESC).text() == "LATRA FEE"
    assert register._table.item(1, COL_DESC).text() == "DIESEL FILL"
    assert register._table.item(2, COL_DESC).text() == "TOLLS"


def test_notify_during_bulk_defers_until_flush(register):
    emits: list[int] = []
    register.day_issues_source_changed.connect(lambda: emits.append(1))

    register._bulk_mutating = True
    register._notify_day_issues_source_changed()
    assert emits == []
    assert register._day_issues_notify_pending is True

    register._bulk_mutating = False
    register._flush_day_issues_source_changed()
    assert emits == [1]
    assert register._day_issues_notify_pending is False
