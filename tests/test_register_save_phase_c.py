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

    async def boom_bulk(_txs, *, on_progress=None, should_cancel=None):
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
    assert messages and messages[0].startswith("Saved locally")
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

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
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

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
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
    # Phase 2: drain emits Syncing… then Draft saved: (status only, no overlay).
    assert any(m.startswith("Draft saved:") for m in messages)


@pytest.mark.asyncio
async def test_do_save_schedules_drain_after_release(register, monkeypatch):
    """Drain is scheduled only after ``_save_in_flight`` clears."""
    scheduled: list[int] = []

    async def body(meta=None, *, parked_only=False):
        register._kick_outbox_after_save = True
        if meta is not None:
            meta["queued"] = 1
            meta["async_outbox"] = True
            meta["parked_only"] = parked_only
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


@pytest.mark.asyncio
async def test_parked_only_saves_parked_skips_off_date_and_non_parked(
    register, monkeypatch
):
    """parked_only: persist parked rows as parked; leave other My rows alone."""
    from tahmeed.models.transaction import Transaction
    from tahmeed.ui.cashier.register_delegates import (
        COL_DATE,
        COL_DESC,
        COL_TRUCK,
        COL_TZS,
    )

    register._table.blockSignals(True)
    try:
        # Parked incorrect-truck row (off-date) — saved as register_status=parked.
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "BAD TRUCK")
        register._put(0, COL_DATE, "01/10/2026")
        register._pending_row_meta[0] = {"parked": True, "client_row_id": "p0"}
        # Good-truck off-date row — must remain unsaved on My.
        register._put(1, COL_DESC, "DIESEL")
        register._put(1, COL_TZS, "2,000.00")
        register._put(1, COL_TRUCK, "YARD")
        register._put(1, COL_DATE, "02/10/2026")
    finally:
        register._table.blockSignals(False)

    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_rows_missing_required_truck", lambda: [])

    async def fake_next_day_order(_d):
        return 1

    async def boom_dup(*_a, **_k):
        raise AssertionError("parked_only must not run duplicate review")

    sent: list = []

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        for tx in txs:
            tx._id = ObjectId()
            sent.append(tx)
        return [("created", tx, None) for tx in txs]

    def boom_mbox(*_a, **_k):
        raise AssertionError("parked_only must not show Off-date / save dialogs")

    monkeypatch.setattr(
        register,
        "_enqueue_inserts_for_outbox",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("parked_only must sync-create, not async-queue")
        ),
    )

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.next_day_order",
        fake_next_day_order,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk",
        boom_dup,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.warning",
        boom_mbox,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        boom_mbox,
    )

    meta: dict = {}
    ok = await register._do_save_body(meta, parked_only=True)
    assert ok is True
    assert meta.get("parked_only") is True
    assert len(sent) == 1
    assert sent[0].register_status == "parked"
    assert isinstance(sent[0], Transaction)
    assert not meta.get("queued")
    assert not meta.get("async_outbox")
    # Non-parked row stays ordinary unsaved (no sync_status).
    assert not (register._pending_row_meta.get(1) or {}).get("sync_status")
    assert register._row_has_data(1)
