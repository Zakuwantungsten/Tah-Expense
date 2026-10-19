"""Phase 1 — outbox/load mutex + Possible Duplicates Keep for later."""

from __future__ import annotations

import sys
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import QApplication, QDialog, QTableWidgetItem


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
async def test_await_outbox_idle_waits_until_drain_clears(register, monkeypatch):
    from tahmeed.services.register_draft_service import SYNC_PENDING
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, COL_TRUCK

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_PENDING)

    register._outbox_drain_in_flight = True
    ticks = {"n": 0}

    async def fake_sleep(_s):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            register._outbox_drain_in_flight = False
            register._pending_row_meta.pop(0, None)

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.sleep",
        fake_sleep,
    )
    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    ok = await register._await_outbox_idle(timeout_s=5, require_empty=True)
    assert ok is True
    assert ticks["n"] >= 2
    assert not register._outbox_drain_in_flight


@pytest.mark.asyncio
async def test_await_outbox_idle_require_empty_false_returns_when_drain_done(
    register, monkeypatch
):
    from tahmeed.services.register_draft_service import SYNC_FAILED
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, COL_TRUCK

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)
    register._set_row_sync_status(0, SYNC_FAILED)
    register._outbox_drain_in_flight = False

    ok = await register._await_outbox_idle(timeout_s=1, require_empty=False)
    assert ok is True
    # Failed leftover still present — load path must not block on it.
    assert register._outbox_pending_rows()


@pytest.mark.asyncio
async def test_parked_persist_awaits_idle_before_view_switch(register, monkeypatch):
    """Parked after Save must settle outbox before set_view_mode."""
    order: list[str] = []

    async def fake_save(*, parked_only=False):
        order.append(f"save:{parked_only}")
        return True

    async def fake_idle(*, timeout_s=180, require_empty=True):
        order.append(f"idle:{require_empty}")
        return True

    modes: list[str] = []

    def fake_set_view(mode):
        order.append(f"view:{mode}")
        modes.append(mode)

    monkeypatch.setattr(register, "_do_save", fake_save)
    monkeypatch.setattr(register, "_await_outbox_idle", fake_idle)
    monkeypatch.setattr(register, "set_view_mode", fake_set_view)
    register._view_mode = "my"
    register._pending_row_meta[0] = {"parked": True, "client_row_id": "x"}

    await register._persist_parked_after_dialog()

    assert order == ["save:True", "idle:True", "view:parked"]
    assert modes == ["parked"]


@pytest.mark.asyncio
async def test_schedule_outbox_drain_blocked_while_load_in_flight(register):
    register._load_in_flight = True
    register._api_is_online = lambda: True  # type: ignore[method-assign]
    # Restore real schedule (fixture stubs it).
    from tahmeed.ui.cashier.excel_grid import DailyRegister

    started = []

    def capture_start(ms):
        started.append(ms)

    register._outbox_drain_timer.start = capture_start  # type: ignore[method-assign]
    register._outbox_pending_rows = lambda: [0]  # type: ignore[method-assign]
    DailyRegister._schedule_outbox_drain(register, delay_ms=10)
    assert started == []


@pytest.mark.asyncio
async def test_dup_keep_for_later_returns_true_without_sync_pending(
    register, monkeypatch
):
    from tahmeed.models.transaction import Transaction
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS, COL_TRUCK
    from tahmeed.ui.dialogs.duplicate_review_dialog import DuplicateReviewDialog

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "PARKING")
        register._put(0, COL_TZS, "5,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)

    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_rows_missing_required_truck", lambda: [])

    async def fake_next_day_order(_d):
        return 1

    existing = Transaction(
        date=datetime(2026, 8, 1),
        description="PARKING",
        truck_number="YARD",
        amount=5000.0,
        item="PARKING",
    )
    existing._id = ObjectId()

    async def fake_dup_bulk(candidates, *_a, **_k):
        return [[existing] for _ in candidates]

    class _KeepDlg(DuplicateReviewDialog):
        def exec(self):  # noqa: A003
            self._accept_keep_for_later()
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.next_day_order",
        fake_next_day_order,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk",
        fake_dup_bulk,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.DuplicateReviewDialog",
        _KeepDlg,
    )

    messages: list[str] = []
    register.transient_status.connect(messages.append)

    meta: dict = {}
    ok = await register._do_save_body(meta)
    assert ok is True
    assert meta.get("deferred_only") is True
    assert meta.get("queued", 0) == 0
    assert register._row_has_data(0)
    # No sync_status — ordinary unsaved row for a later Save.
    assert not (register._pending_row_meta.get(0) or {}).get("sync_status")
    assert messages and "possible duplicate" in messages[0].lower()


def test_duplicate_dialog_keep_for_later_api(qapp):
    from tahmeed.models.transaction import Transaction
    from tahmeed.services.duplicate_review import DuplicateReviewItem
    from tahmeed.ui.dialogs.duplicate_review_dialog import DuplicateReviewDialog

    item = DuplicateReviewItem(
        row=2,
        row_display=3,
        description="PARKING",
        truck_number="YARD",
        item="PARKING",
        amount=1000.0,
        amount_label="TZS 1,000",
        existing=Transaction(
            date=datetime(2026, 8, 20),
            description="PARKING",
            truck_number="YARD",
            amount=1000.0,
            item="PARKING",
        ),
        date_label="23 Aug 2026",
    )
    dlg = DuplicateReviewDialog([item], dup_days=5)
    dlg._checks[0].setChecked(True)
    dlg._accept_keep_for_later()
    assert dlg.kept_for_later() is True
    assert dlg.save_anyway_rows() == set()
    assert dlg.deferred_duplicate_rows() == {2}
