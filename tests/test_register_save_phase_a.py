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


def test_action_bar_flash_transient_status(qapp):
    from tahmeed.ui.cashier.dashboard import _ActionBar

    bar = _ActionBar()
    bar.show()
    bar.flash_transient_status("Draft saved: 3 added", ms=1000)
    assert not bar._status.isHidden()
    assert "Draft saved" in bar._status.text()
    bar._clear_transient_status()
    assert bar._status.isHidden()
