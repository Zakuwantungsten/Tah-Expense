"""Phase 4 — offline outbox enqueue / sync_status / drain helpers."""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bson import ObjectId


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def register(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda *_a, **_k: None,
    )
    from tahmeed.services import register_draft_service as drafts
    from tahmeed.ui.cashier.excel_grid import DailyRegister
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, EDIT_BG
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush
    from PySide6.QtWidgets import QTableWidgetItem

    monkeypatch.setattr(drafts, "drafts_root", lambda: tmp_path / "register_drafts")

    user = MagicMock()
    user._id = ObjectId()
    user.username = "aisha"
    reg = DailyRegister(user, [])
    monkeypatch.setattr(reg, "_schedule_outbox_drain", lambda *a, **k: None)
    reg._outbox_drain_timer.stop()
    try:
        from tahmeed.signals import app_signals

        app_signals.connectivity_changed.disconnect(reg._on_connectivity_changed)
    except Exception:
        pass
    reg._current_date = date(2026, 8, 8)
    reg._merged_mode = False
    reg._edit_mode = False
    reg._table.setRowCount(6)
    reg._saved_count = 0
    reg._saved_ids = {}
    reg._saved_txs = {}
    reg._init_editable_rows(0, 6)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    reg._put = _put
    return reg


def test_normalize_sync_status_maps_in_flight_to_pending():
    from tahmeed.services.register_draft_service import (
        SYNC_FAILED,
        SYNC_IN_FLIGHT,
        SYNC_PENDING,
        normalize_sync_status,
    )

    assert normalize_sync_status(SYNC_IN_FLIGHT) == SYNC_PENDING
    assert normalize_sync_status(SYNC_FAILED) == SYNC_FAILED
    assert normalize_sync_status(None) == SYNC_PENDING
    assert normalize_sync_status("") == SYNC_PENDING


def test_enqueue_offline_outbox_marks_pending_and_flushes(register):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS
    from tahmeed.services.register_draft_service import (
        SYNC_PENDING,
        entry_sync_status,
        load_register_draft,
    )

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
    finally:
        register._table.blockSignals(False)

    register._enqueue_offline_outbox()

    assert register._row_sync_status(0) == SYNC_PENDING
    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    assert len(draft["new_rows"]) == 1
    assert entry_sync_status(draft["new_rows"][0]) == SYNC_PENDING
    assert draft["new_rows"][0]["client_row_id"]


@pytest.mark.asyncio
async def test_drain_outbox_marks_saved_on_success(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.models.transaction import Transaction
    from datetime import datetime
    from bson import ObjectId

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "PARKING")
        register._put(0, COL_TZS, "5,000.00")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)

    async def fake_bulk(txs):
        assert len(txs) == 1
        txs[0]._id = ObjectId()
        return [("created", txs[0], None)]

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    load_calls: list = []

    async def boom_load(_d):
        load_calls.append(_d)

    monkeypatch.setattr(register, "_load_date", boom_load)

    await register._drain_register_outbox()

    # Phase C: promote into saved prefix — no full reload on clean success.
    assert load_calls == []
    assert register._saved_count == 1
    assert register._outbox_pending_rows() == []


@pytest.mark.asyncio
async def test_api_offline_save_enqueues(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS
    from tahmeed.services.register_draft_service import SYNC_PENDING, load_register_draft

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "OFFLINE ROW")
        register._put(0, COL_TZS, "100.00")
    finally:
        register._table.blockSignals(False)

    monkeypatch.setattr(register, "_api_is_online", lambda: False)
    boxes = []

    def fake_info(*args, **kwargs):
        boxes.append(args[1] if len(args) > 1 else kwargs.get("title"))
        return MagicMock()

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        fake_info,
    )

    result = await register._do_save_body()
    assert result is True
    assert register._row_sync_status(0) == SYNC_PENDING
    assert load_register_draft(
        register._user._id, register._current_date, merged=False
    ) is not None
    assert boxes and "Saved locally" in str(boxes[0])
