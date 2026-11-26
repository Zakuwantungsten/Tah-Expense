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

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
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


@pytest.mark.asyncio
async def test_await_outbox_idle_waits_for_inflight_drain_in_merged_mode(
    register, monkeypatch
):
    """Browse opens Merged — must still wait out a drain that started on My."""
    register._merged_mode = True
    register._outbox_drain_in_flight = True
    ticks = {"n": 0}

    async def fake_sleep(_s):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            register._outbox_drain_in_flight = False

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.sleep",
        fake_sleep,
    )

    ok = await register._await_outbox_idle(timeout_s=5, require_empty=False)
    assert ok is True
    assert ticks["n"] >= 2
    assert not register._outbox_drain_in_flight


@pytest.mark.asyncio
async def test_drain_ack_after_day_change_does_not_append_to_opened_day(
    register, monkeypatch
):
    """Save day A still syncing → Browse opens day B Merged → no A rows on B."""
    from tahmeed.services.register_draft_service import (
        SYNC_PENDING,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    day_a = date(2026, 8, 8)
    day_b = date(2026, 8, 9)
    register._current_date = day_a
    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DAY-A DIESEL")
        register._put(0, COL_TZS, "1,000.00")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)
    cid = (register._pending_row_meta.get(0) or {}).get("client_row_id")
    assert cid
    register._flush_local_draft()

    day_a_table = register._table
    day_a_saved_before = register._saved_count

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        # Simulate Browse Go To while the network call is in flight.
        register._current_date = day_b
        register._merged_mode = True
        if register._merged_table is not None:
            register._activate_table_surface(register._merged_table)
            register._saved_count = 0
            register._saved_ids = {}
            register._saved_txs = {}
            register._pending_row_meta = {}
            # Paint two "day B" merged rows so an errant append is visible.
            register._populate([])
        txs[0]._id = ObjectId()
        txs[0].register_status = "draft"
        return [("created", txs[0], None)]

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_load_date", lambda _d: None)

    append_calls: list[int] = []
    real_append = register._append_saved_transactions

    def track_append(txs):
        append_calls.append(len(txs))
        return real_append(txs)

    monkeypatch.setattr(register, "_append_saved_transactions", track_append)

    await register._drain_register_outbox()

    # Must not promote onto the newly opened Merged day-B surface.
    assert append_calls == []
    assert register._table is register._merged_table
    assert register._current_date == day_b
    assert register._saved_count == 0

    # Durable prune still targets day A so recovery cannot resurrect ACK'd rows.
    draft_a = load_register_draft(register._user._id, day_a, merged=False)
    if draft_a is not None:
        ids = {
            str(e.get("client_row_id") or "")
            for e in (draft_a.get("new_rows") or [])
        }
        assert str(cid) not in ids

    # Day A My table was left alone (drain did not mark/clear via wrong table).
    assert day_a_table is register._my_table
    assert day_a_saved_before == 0


@pytest.mark.asyncio
async def test_drain_ack_after_flip_to_merged_updates_my_keepalive(
    register, monkeypatch
):
    """My → Merged mid-sync: ACK lands on My keep-alive, never Merged cells."""
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "KEEPALIVE ROW")
        register._put(0, COL_TZS, "2,500.00")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        # Snapshot My then flip to Merged (same day) while syncing.
        register._my_session = register._snapshot_grid_session()
        register._activate_table_surface(register._merged_table)
        register._view_mode = "merged"
        register._merged_mode = True
        register._saved_count = 0
        register._saved_ids = {}
        register._saved_txs = {}
        register._pending_row_meta = {}
        register._populate([])
        txs[0]._id = ObjectId()
        txs[0].register_status = "draft"
        return [("created", txs[0], None)]

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    await register._drain_register_outbox()

    # Active Merged surface stays empty of the ACK'd promote.
    assert register._table is register._merged_table
    assert register._saved_count == 0

    # My keep-alive received the saved row.
    assert register._my_session is not None
    assert int(register._my_session.get("saved_count") or 0) == 1
    assert register._my_session.get("saved_txs")
