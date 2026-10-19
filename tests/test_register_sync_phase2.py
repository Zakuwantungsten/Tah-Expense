"""Phase 2 — instant local Save; non-blocking background outbox drain."""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem


@pytest.fixture
def qapp():
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
    from tahmeed.ui.cashier.register_delegates import EDIT_BG

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


@pytest.mark.asyncio
async def test_drain_does_not_show_overlay_or_toolbar_busy(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "PARKING")
        register._put(0, COL_TZS, "5,000.00")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)

    overlays: list[str] = []
    busy_signals: list[bool] = []
    status: list[str] = []

    register._show_register_loading = lambda msg="Loading…": overlays.append(msg)  # type: ignore[method-assign]
    register.save_busy_changed.connect(busy_signals.append)
    register.transient_status.connect(status.append)

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        if on_progress:
            on_progress(0, len(txs))
            on_progress(len(txs), len(txs))
        for tx in txs:
            tx._id = ObjectId()
            tx.register_status = "draft"
        return [("created", tx, None) for tx in txs]

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    await register._drain_register_outbox()

    assert overlays == []
    assert busy_signals == []
    assert any(s.startswith("Syncing") for s in status)
    assert any(s.startswith("Draft saved:") for s in status)


@pytest.mark.asyncio
async def test_drain_reschedules_when_new_pending_enqueued(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "A")
        register._put(0, COL_TZS, "1")
        register._put(1, COL_DESC, "B")
        register._put(1, COL_TZS, "2")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)

    scheduled: list[int] = []

    def capture_schedule(delay_ms=500):
        scheduled.append(delay_ms)

    # Restore real drain scheduling capture (fixture stubs schedule).
    monkeypatch.setattr(register, "_schedule_outbox_drain", capture_schedule)
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        # While first row syncs, another Save queued row 1.
        register._set_row_sync_status(1, SYNC_PENDING)
        for tx in txs:
            tx._id = ObjectId()
            tx.register_status = "draft"
        return [("created", tx, None) for tx in txs]

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )

    await register._drain_register_outbox()

    # Row indices may shift after promote; leftover PENDING must still reschedule.
    assert register._outbox_pending_rows()
    assert scheduled == [100]


def test_sync_pending_restore_notice(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING

    register._pending_row_meta[0] = {
        "client_row_id": "abc",
        "sync_status": SYNC_PENDING,
    }
    register._saved_count = 0
    # Pretend row 0 has data so the notice scans it.
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "LEFT")
    finally:
        register._table.blockSignals(False)

    boxes: list[tuple[str, str]] = []

    def fake_info(parent, title, text, *a, **k):
        boxes.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        fake_info,
    )

    register._show_draft_restored_notice(0, 1)
    assert boxes and boxes[0][0] == "Sync pending"
    assert "waiting to reach the server" in boxes[0][1]


def test_ordinary_restore_notice_unchanged(register, monkeypatch):
    boxes: list[tuple[str, str]] = []

    def fake_info(parent, title, text, *a, **k):
        boxes.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        fake_info,
    )

    register._show_draft_restored_notice(1, 2)
    assert boxes and boxes[0][0] == "Draft restored"
    assert "before the app closed" in boxes[0][1]
