"""Phase 3 — chunk progress + promote ACK rows without full reload on partial."""

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
    reg._table.setRowCount(8)
    reg._saved_count = 0
    reg._saved_ids = {}
    reg._saved_txs = {}
    reg._init_editable_rows(0, 8)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    reg._put = _put
    return reg


@pytest.mark.asyncio
async def test_bulk_save_emits_chunk_progress(monkeypatch):
    from tahmeed.models.transaction import Transaction
    from tahmeed.services import cashier_service as svc
    from datetime import datetime

    txs = []
    for i in range(3):
        tx = Transaction(
            date=datetime(2026, 8, 8),
            description=f"R{i}",
            truck_number="YARD",
            amount=float(i + 1),
            item="PARKING",
        )
        tx.client_row_id = f"cid-{i}"
        txs.append(tx)

    monkeypatch.setattr(svc, "BULK_CREATE_CHUNK", 2)

    async def fake_request(method, path, **kwargs):
        items = kwargs["json"]["items"]
        return {
            "results": [
                {
                    "index": i,
                    "status": "created",
                    "transaction": {
                        "_id": str(ObjectId()),
                        "description": it["description"],
                        "amount": it["amount"],
                        "date": "2026-08-08T00:00:00",
                        "client_row_id": it.get("client_row_id"),
                        "register_status": "draft",
                    },
                }
                for i, it in enumerate(items)
            ]
        }

    monkeypatch.setattr(svc.api_client, "request", fake_request)

    progress: list[tuple[int, int]] = []

    def on_progress(done, total):
        progress.append((done, total))

    outcomes = await svc.bulk_save_transactions(txs, on_progress=on_progress)
    assert len(outcomes) == 3
    assert all(s == "created" for s, _t, _e in outcomes)
    # Start + after each chunk (2 chunks with size 2).
    assert progress[0] == (0, 3)
    assert progress[-1] == (3, 3)
    assert (2, 3) in progress


@pytest.mark.asyncio
async def test_drain_partial_promotes_without_reload(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_FAILED, SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "OK")
        register._put(0, COL_TZS, "100")
        register._put(1, COL_DESC, "BAD")
        register._put(1, COL_TZS, "200")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)
    register._set_row_sync_status(1, SYNC_PENDING)

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        if on_progress:
            on_progress(0, len(txs))
            on_progress(len(txs), len(txs))
        out = []
        for i, tx in enumerate(txs):
            if i == 0:
                tx._id = ObjectId()
                tx.register_status = "draft"
                out.append(("created", tx, None))
            else:
                out.append(("error", None, "server rejected"))
        return out

    load_calls: list = []

    async def boom_load(d):
        load_calls.append(d)

    status: list[str] = []
    register.transient_status.connect(status.append)

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_load_date", boom_load)
    monkeypatch.setattr(register, "_schedule_outbox_backoff", lambda: None)

    await register._drain_register_outbox()

    assert load_calls == []
    assert register._saved_count == 1
    # Leftover failed row still queued for retry.
    pending = register._outbox_pending_rows()
    assert pending
    assert register._row_sync_status(pending[0]) == SYNC_FAILED
    assert any("left to retry" in s for s in status)


@pytest.mark.asyncio
async def test_drain_large_batch_shows_range_progress(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.excel_grid import BULK_CREATE_CHUNK
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    n = BULK_CREATE_CHUNK + 5
    register._table.setRowCount(n + 2)
    register._init_editable_rows(0, n + 2)
    register._table.blockSignals(True)
    try:
        for i in range(n):
            register._put(i, COL_DESC, f"R{i}")
            register._put(i, COL_TZS, "1")
            register._set_row_sync_status(i, SYNC_PENDING)
    finally:
        register._table.blockSignals(False)

    progress_msgs: list[str] = []

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        if on_progress:
            on_progress(0, len(txs))
            # Simulate first chunk complete.
            on_progress(min(BULK_CREATE_CHUNK, len(txs)), len(txs))
            on_progress(len(txs), len(txs))
        out = []
        for tx in txs:
            tx._id = ObjectId()
            tx.register_status = "draft"
            out.append(("created", tx, None))
        return out

    register.transient_status.connect(progress_msgs.append)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    await register._drain_register_outbox()

    range_msgs = [m for m in progress_msgs if " of " in m and "–" in m]
    assert range_msgs, progress_msgs
    assert any(f"of {n}" in m for m in range_msgs)
    assert register._saved_count == n
