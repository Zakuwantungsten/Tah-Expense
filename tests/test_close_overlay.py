"""MainWindow close path shows feedback while prepare_to_leave runs."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget

from tahmeed.models.user import User
from tahmeed.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_close_after_prepare_shows_overlay(qapp, monkeypatch):
    user = User(
        username="c1",
        password_hash="x",
        role="cashier",
        full_name="Cashier",
    )
    seen: list[bool] = []

    class _Dash(QWidget):
        async def prepare_to_leave(self) -> bool:
            overlay = win._close_overlay
            seen.append(overlay is not None and overlay.isVisible())
            return False  # cancel close

    def _build(self):
        self.setCentralWidget(_Dash())

    monkeypatch.setattr(MainWindow, "_build_ui", _build)
    win = MainWindow(user)
    win.resize(640, 480)
    win.show()
    qapp.processEvents()

    win._close_pending = True
    asyncio.run(win._close_after_prepare())
    qapp.processEvents()

    assert seen == [True]
    assert win._close_pending is False
    assert win._close_overlay is not None
    assert not win._close_overlay.isVisible()
    assert win._force_close is False

    win._force_close = True
    win.close()
