"""Phase C — async online Save (outbox enqueue) + promote without full reload."""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QApplication, QTableWidgetItem


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
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, EDIT_BG

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
async def test_online_save_enqueues_outbox_without_reload(register, monkeypatch):
    from tahmeed.services.register_draft_service import (
        SYNC_PENDING,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, COL_TRUCK

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)

    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_rows_missing_required_truck", lambda: [])

    async def fake_next_day_order(_d):
        return 1

    async def fake_dup_bulk(candidates, *_a, **_k):
        return [[] for _ in candidates]

    async def boom_bulk(_txs):
        raise AssertionError("online Save must not bulk-create inline")

    load_calls: list = []

    async def fake_load(d):
        load_calls.append(d)

    messages: list[str] = []
    register.transient_status.connect(messages.append)

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.next_day_order",
        fake_next_day_order,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk",
        fake_dup_bulk,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        boom_bulk,
    )
    monkeypatch.setattr(register, "_load_date", fake_load)

    meta: dict = {}
    ok = await register._do_save_body(meta)
    assert ok is True
    assert meta.get("async_outbox") is True
    assert meta.get("queued") == 1
    assert register._kick_outbox_after_save is True
    assert register._row_sync_status(0) == SYNC_PENDING
    assert load_calls == []
    assert messages and messages[0].startswith("Syncing")
    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    assert len(draft["new_rows"]) == 1


@pytest.mark.asyncio
async def test_submit_save_still_awaits_bulk_create(register, monkeypatch):
    """Submit must not race — inserts stay on the sync create path."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, COL_TRUCK

    register._submit_in_flight = True
    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)

    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_rows_missing_required_truck", lambda: [])

    async def fake_next_day_order(_d):
        return 1

    async def fake_dup_bulk(candidates, *_a, **_k):
        return [[] for _ in candidates]

    bulk_calls = {"n": 0}

    async def fake_bulk(txs):
        bulk_calls["n"] += 1
        for tx in txs:
            tx._id = ObjectId()
        return [("created", tx, None) for tx in txs]

    load_calls: list = []

    async def fake_load(d):
        load_calls.append(d)

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.next_day_order",
        fake_next_day_order,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk",
        fake_dup_bulk,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_load_date", fake_load)

    meta: dict = {}
    ok = await register._do_save_body(meta)
    assert ok is True
    assert bulk_calls["n"] == 1
    assert not meta.get("async_outbox")
    assert meta.get("saved") == 1
    assert load_calls == [date(2026, 8, 8)]


@pytest.mark.asyncio
async def test_drain_promotes_without_full_reload(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "PARKING")
        register._put(0, COL_TZS, "5,000.00")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)

    saved_id = ObjectId()

    async def fake_bulk(txs):
        assert len(txs) == 1
        txs[0]._id = saved_id
        txs[0].register_status = "draft"
        return [("created", txs[0], None)]

    load_calls: list = []

    async def boom_load(_d):
        load_calls.append(_d)

    messages: list[str] = []
    register.transient_status.connect(messages.append)

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_load_date", boom_load)

    await register._drain_register_outbox()

    assert load_calls == []
    assert register._saved_count == 1
    assert register._saved_ids[0] == saved_id
    assert register._outbox_pending_rows() == []
    assert messages and messages[0].startswith("Draft saved:")


@pytest.mark.asyncio
async def test_do_save_schedules_drain_after_release(register, monkeypatch):
    """Drain is scheduled only after ``_save_in_flight`` clears."""
    scheduled: list[int] = []

    async def body(meta=None):
        register._kick_outbox_after_save = True
        if meta is not None:
            meta["queued"] = 1
            meta["async_outbox"] = True
        return True

    monkeypatch.setattr(register, "_do_save_body", body)
    monkeypatch.setattr(
        register,
        "_schedule_outbox_drain",
        lambda delay_ms=500: scheduled.append(delay_ms),
    )

    ok = await register._do_save()
    assert ok is True
    assert register._save_in_flight is False
    assert scheduled == [0]
