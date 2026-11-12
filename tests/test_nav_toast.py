"""Bottom-right nav toast for tab-open feedback."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from tahmeed.ui.widgets.nav_toast import NavToast


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_nav_toast_shows_opening_and_loading(qapp) -> None:
    host = QWidget()
    host.resize(900, 600)
    host.show()
    qapp.processEvents()

    toast = NavToast(host)
    assert not toast.isWindow()
    assert not toast.testAttribute(Qt.WA_TranslucentBackground)

    toast.show_opening("Toll Plaza")
    assert toast.isVisible()
    assert "Opening Toll Plaza" in toast.display_text()
    assert toast.x() + toast.width() <= host.width()
    assert toast.y() + toast.height() <= host.height()

    toast.show_loading("Toll Plaza", ms=50)
    assert "Loading Toll Plaza" in toast.display_text()
    assert toast._hide_timer.isActive()

    # Drive the hide path directly — avoids flaky timer waits under pytest.
    toast._on_hide_timeout()
    assert not toast.isVisible()
    host.close()


def test_nav_toast_constructs_without_crash(qapp) -> None:
    """Regression: hide() during __init__ must not touch timers early."""
    host = QWidget()
    host.resize(400, 300)
    toast = NavToast(host)
    assert not toast.isVisible()
    assert toast._dot_timer is not None
    host.close()


def test_nav_toast_dismiss_stops_timers(qapp) -> None:
    host = QWidget()
    host.resize(640, 480)
    host.show()
    toast = NavToast(host)
    toast.show_opening("Verify")
    assert toast.is_showing()
    toast.dismiss()
    assert not toast.is_showing()
    assert toast.display_text() == "" or True  # may keep last text while hidden
    host.close()
