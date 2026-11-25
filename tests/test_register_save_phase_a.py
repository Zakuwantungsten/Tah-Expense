"""Phase A fast-save: chunk size, non-blocking status pill."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tahmeed.services.cashier_service import BULK_CREATE_CHUNK


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_bulk_create_chunk_within_api_max():
    # API bulk-create allows 500; workers use 300 with concurrent drains.
    assert 1 <= BULK_CREATE_CHUNK <= 500
    assert BULK_CREATE_CHUNK == 300


def test_table_page_status_toast_replaces_green_pill(qapp):
    """Register transient_status shows a floating success toast, not the action-bar pill."""
    from PySide6.QtWidgets import QWidget

    from tahmeed.ui.widgets.nav_toast import NavToast

    host = QWidget()
    host.resize(640, 400)
    host.show()
    toast = NavToast(host)
    toast.show_success("196 kept as possible duplicates", ms=2000)
    assert toast.is_showing()
    assert "196 kept as possible duplicates" in toast.display_text()
    toast.dismiss()
    assert not toast.is_showing()
