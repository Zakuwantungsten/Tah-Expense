"""Phase 4 — cancel mid-drain + Parked/outbox chaos."""

from __future__ import annotations

import sys
from datetime import date
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QKeyEvent
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
    reg._table.setRowCount(12)
    reg._saved_count = 0
    reg._saved_ids = {}
    reg._saved_txs = {}
    reg._init_editable_rows(0, 12)

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    reg._put = _put
    return reg


@pytest.mark.asyncio
async def test_cancel_mid_drain_keeps_acked_requeues_rest(register, monkeypatch):
    from tahmeed.services.cashier_service import BULK_CREATE_CHUNK
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    n = BULK_CREATE_CHUNK + 3
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

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        # First chunk ACK'd; remaining marked cancelled (as real bulk does on Esc).
        from tahmeed.services.cashier_service import BULK_CREATE_CHUNK as CH

        end = min(CH, len(txs))
        out = []
        for i, tx in enumerate(txs):
            if i < end:
                tx._id = ObjectId()
                tx.register_status = "draft"
                out.append(("created", tx, None))
            else:
                out.append(("error", None, "cancelled"))
        if on_progress:
            on_progress(0, len(txs))
            on_progress(end, len(txs))
        return out

    status: list[str] = []
    register.transient_status.connect(status.append)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        fake_bulk,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    # Drain sets cancel flag false at start — our fake sets it mid-flight.
    await register._drain_register_outbox()

    assert register._saved_count == BULK_CREATE_CHUNK
    pending = register._outbox_pending_rows()
    assert len(pending) == 3
    assert all(
        register._row_sync_status(r) == SYNC_PENDING for r in pending
    )
    assert any("Sync cancelled" in s for s in status)


def test_escape_requests_cancel_while_draining(register):
    register._outbox_drain_in_flight = True
    ok = register.cancel_outbox_sync()
    assert ok is True
    assert register._outbox_cancel_requested is True

    # Esc via table key path (Daily Register grid)
    register._outbox_cancel_requested = False
    register._outbox_drain_in_flight = True
    ev = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    register._table_key_press(ev)
    assert register._outbox_cancel_requested is True


def test_escape_noop_when_not_syncing(register):
    register._outbox_drain_in_flight = False
    assert register.cancel_outbox_sync() is False
    assert register._outbox_cancel_requested is False


@pytest.mark.asyncio
async def test_parked_persist_awaits_idle_before_mode_switch(register, monkeypatch):
    """Chaos: Parked path must not race drain (regression from Phase 1)."""
    order: list[str] = []

    async def fake_save(*, parked_only=False):
        order.append(f"save:{parked_only}")
        return True

    async def fake_idle(*, timeout_s=180, require_empty=True):
        order.append(f"idle:{require_empty}")
        return True

    def fake_view(mode):
        order.append(f"view:{mode}")

    monkeypatch.setattr(register, "_do_save", fake_save)
    monkeypatch.setattr(register, "_await_outbox_idle", fake_idle)
    monkeypatch.setattr(register, "set_view_mode", fake_view)
    register._view_mode = "my"
    register._pending_row_meta[0] = {"parked": True}

    await register._persist_parked_after_dialog()
    assert order == ["save:True", "idle:True", "view:parked"]


@pytest.mark.asyncio
async def test_bulk_save_honours_should_cancel(monkeypatch):
    from datetime import datetime

    from tahmeed.models.transaction import Transaction
    from tahmeed.services import cashier_service as svc

    txs = []
    for i in range(5):
        tx = Transaction(
            date=datetime(2026, 8, 8),
            description=f"R{i}",
            truck_number="YARD",
            amount=1.0,
            item="X",
        )
        tx.client_row_id = f"c{i}"
        txs.append(tx)

    monkeypatch.setattr(svc, "BULK_CREATE_CHUNK", 2)
    posts = {"n": 0}

    async def fake_request(method, path, **kwargs):
        posts["n"] += 1
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

    # Cancel before any chunk.
    outcomes = await svc.bulk_save_transactions(
        txs, should_cancel=lambda: True
    )
    assert posts["n"] == 0
    assert all(err == "cancelled" for _s, _t, err in outcomes)

    # Cancel after first chunk.
    flag = {"cancel": False}

    def should():
        return flag["cancel"]

    async def fake_request2(method, path, **kwargs):
        posts["n"] += 1
        flag["cancel"] = True
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

    posts["n"] = 0
    monkeypatch.setattr(svc.api_client, "request", fake_request2)
    outcomes = await svc.bulk_save_transactions(txs, should_cancel=should)
    assert posts["n"] == 1
    assert sum(1 for s, _t, _e in outcomes if s == "created") == 2
    assert sum(1 for _s, _t, e in outcomes if e == "cancelled") == 3
