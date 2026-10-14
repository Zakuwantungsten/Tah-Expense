"""Phase 5 — chaos-oriented register save / restore regressions (no hang)."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
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
    from tahmeed.ui.cashier.register_delegates import EDIT_BG
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
async def test_double_save_ignored_while_in_flight(register, monkeypatch):
    """Second Save while first is flying must no-op (no duplicate posts)."""
    calls = {"n": 0}

    async def slow_body(meta=None):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(register, "_do_save_body", slow_body)

    t1 = asyncio.create_task(register._do_save())
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(register._do_save())
    ok1, ok2 = await asyncio.gather(t1, t2)
    assert ok1 is True
    assert ok2 is False
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_outbox_drain_skipped_in_merged_mode(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._merged_mode = True
    register._put(0, COL_DESC, "DIESEL")
    register._put(0, COL_TZS, "1,000.00")
    register._set_row_sync_status(0, SYNC_PENDING)

    called = {"n": 0}

    async def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("bulk_save must not run in Merged")

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        boom,
    )
    await register._drain_register_outbox()
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_network_drop_mid_drain_marks_failed(register, monkeypatch):
    from tahmeed.services.register_draft_service import (
        SYNC_FAILED,
        SYNC_PENDING,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    register._put(0, COL_DESC, "PARKING")
    register._put(0, COL_TZS, "5,000.00")
    register._set_row_sync_status(0, SYNC_PENDING)
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    async def boom(_txs):
        raise ConnectionError("ACK lost")

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.bulk_save_transactions",
        boom,
    )
    backoff = {"n": 0}
    monkeypatch.setattr(
        register,
        "_schedule_outbox_backoff",
        lambda: backoff.__setitem__("n", backoff["n"] + 1),
    )

    await register._drain_register_outbox()

    assert register._row_sync_status(0) == SYNC_FAILED
    assert backoff["n"] == 1
    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    assert len(draft["new_rows"]) == 1


def test_mid_ack_prune_keeps_unsaved_sibling(register):
    """Kill after first-row ACK: pruned row gone; sibling stays in draft."""
    from tahmeed.services.register_draft_service import load_register_draft
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS
    from PySide6.QtWidgets import QTableWidgetItem

    register._table.setItem(0, COL_DESC, QTableWidgetItem("ACKED"))
    register._table.setItem(0, COL_TZS, QTableWidgetItem("10.00"))
    register._table.setItem(1, COL_DESC, QTableWidgetItem("STILL LOCAL"))
    register._table.setItem(1, COL_TZS, QTableWidgetItem("20.00"))
    register._flush_local_draft()
    assert len(
        load_register_draft(
            register._user._id, register._current_date, merged=False
        )["new_rows"]
    ) == 2

    register._mark_new_row_saved(0)

    assert not register._row_has_data(0)
    assert register._table.item(1, COL_DESC).text() == "STILL LOCAL"
    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert draft is not None
    assert len(draft["new_rows"]) == 1
    from tahmeed.services.register_draft_service import cells_from_json

    cells = cells_from_json(draft["new_rows"][0]["cells"])
    assert cells.get(COL_DESC) == "STILL LOCAL"
