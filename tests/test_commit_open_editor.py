"""Flushing cell editors on leave must not call commitData with a foreign widget."""

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
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    user = MagicMock()
    user._id = "cashier-1"
    return DailyRegister(user, [])


def test_commit_open_editor_noop_when_not_editing(register, qapp):
    from PySide6.QtWidgets import QAbstractItemView

    assert register._table.state() != QAbstractItemView.EditingState
    assert register._active_item_editor() is None
    # Repeated leave-path flushes must stay silent when no editor is open.
    register._commit_open_editor()
    register._commit_open_editor()
    register._commit_open_editor()
    assert register._table.state() != QAbstractItemView.EditingState


def test_active_item_editor_resolves_viewport_child(register, qapp, monkeypatch):
    from PySide6.QtWidgets import QAbstractItemView, QApplication, QLineEdit

    table = register._table
    editor = QLineEdit(table.viewport())
    nested = QLineEdit(editor)

    monkeypatch.setattr(table, "state", lambda: QAbstractItemView.EditingState)
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: nested))

    assert register._active_item_editor() is editor
