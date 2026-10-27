"""Discard from Drafts must not leave ghost rows on the open Daily Register."""

from __future__ import annotations

import sys
from datetime import date, datetime
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

    day = date(2026, 8, 8)
    reg._current_date = day
    reg._merged_mode = False
    tx0 = ObjectId()
    reg._table.setRowCount(6)
    reg._saved_count = 1
    reg._saved_ids = {0: tx0}
    reg._saved_txs = {
        0: SimpleNamespace(
            _id=tx0,
            cashier_id=user._id,
            register_status="draft",
            verified=False,
            description="DIESEL",
            truck_number="",
            item="FUEL",
            date=datetime(2026, 8, 8),
            money_parts=lambda: (1000.0, 0.0),
        ),
    }

    def _put(row, col, text):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
        it.setBackground(QBrush(EDIT_BG))
        reg._table.setItem(row, col, it)

    _put(0, COL_DESC, "DIESEL")
    _put(0, COL_TZS, "1,000.00")
    reg._init_editable_rows(1, 6)
    reg._put = _put
    return reg


def test_invalidate_reloads_open_day_and_clears_local_draft(register, monkeypatch):
    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        load_register_draft,
        save_register_draft,
    )

    day = register._current_date
    save_register_draft(
        build_draft_payload(
            user_id=register._user._id,
            username="aisha",
            register_date=day,
            merged=False,
            edit_mode=False,
            dirty_saved=[],
            new_rows=[{"cells": {"3": "GHOST", "7": "1.00"}}],
        )
    )
    assert load_register_draft(register._user._id, day, merged=False) is not None
    assert register._saved_count == 1

    scheduled = []

    def capture(coro, *a, **k):
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        capture,
    )

    register.invalidate_after_server_draft_change(day)

    assert load_register_draft(register._user._id, day, merged=False) is None
    assert len(scheduled) == 1


def test_invalidate_other_day_clears_draft_without_reload(register, monkeypatch):
    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        load_register_draft,
        save_register_draft,
    )

    other = date(2026, 9, 1)
    save_register_draft(
        build_draft_payload(
            user_id=register._user._id,
            username="aisha",
            register_date=other,
            merged=False,
            edit_mode=False,
            dirty_saved=[],
            new_rows=[{"cells": {"3": "OTHER", "7": "1.00"}}],
        )
    )
    scheduled = []

    def capture(coro, *a, **k):
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        capture,
    )

    register.invalidate_after_server_draft_change(other)

    assert load_register_draft(register._user._id, other, merged=False) is None
    assert scheduled == []
    assert register._saved_count == 1


def test_invalidate_preserves_unsaved_via_flush(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS
    from tahmeed.services.register_draft_service import load_register_draft

    register._put(register._saved_count, COL_DESC, "KEEP ME")
    register._put(register._saved_count, COL_TZS, "50.00")
    assert register.has_unsaved_work()

    scheduled = []

    def capture(coro, *a, **k):
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        capture,
    )

    day = register._current_date
    register.invalidate_after_server_draft_change(day)

    draft = load_register_draft(register._user._id, day, merged=False)
    assert draft is not None
    assert len(draft["new_rows"]) == 1
    assert len(scheduled) == 1


def test_invalidate_marks_day_stale_and_skips_draft_restore(register, monkeypatch):
    scheduled = []

    def capture(coro, *a, **k):
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        capture,
    )

    register._day_tx_cache = [object()]
    register._day_tx_cache_key = ("cashier", "2026-08-08", "x")
    register._day_tx_stale = False

    register.invalidate_after_server_draft_change(register._current_date)

    assert register._day_tx_stale is True
    assert register._day_tx_cache is None
    assert register._draft_restore_policy == "off"
    assert len(scheduled) == 1


def test_ensure_fresh_day_view_reloads_when_stale(register, monkeypatch):
    scheduled = []

    def capture(coro, *a, **k):
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        capture,
    )

    register._day_tx_stale = True
    register._day_tx_cache = None
    register._load_in_flight = False
    register.ensure_fresh_day_view()
    assert len(scheduled) == 1

    scheduled.clear()
    register._day_tx_stale = False
    register._day_tx_cache = [object()]
    register.ensure_fresh_day_view()
    assert scheduled == []


def test_ensure_fresh_day_view_skips_while_boot_load_pending(register, monkeypatch):
    """Opening Table: reload_settings must not start a second load of the same day."""
    scheduled = []

    def capture(coro, *a, **k):
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        capture,
    )

    boot = MagicMock()
    boot.done.return_value = False
    register._boot_load_task = boot
    register._day_tx_stale = True
    register._day_tx_cache = None
    register._load_in_flight = False
    register.ensure_fresh_day_view()
    assert scheduled == []

    boot.done.return_value = True
    register.ensure_fresh_day_view()
    assert len(scheduled) == 1


def test_keep_alive_flips_refuse_when_day_stale(register):
    register._day_tx_stale = True
    register._my_session = {"saved_count": 0}
    assert register._flip_to_my_keep_alive() is False
    assert register._flip_to_parked_keep_my() is False


@pytest.mark.asyncio
async def test_reload_after_server_row_change_invalidates_and_loads(
    register, monkeypatch
):
    loads = []

    async def fake_load(d):
        loads.append(d)
        register._day_tx_stale = False
        register._day_tx_cache = []
        register._day_tx_cache_key = ("cashier", d.isoformat(), "x")

    monkeypatch.setattr(register, "_load_date", fake_load)
    register._day_tx_cache = [object()]
    register._day_tx_stale = False

    await register._reload_after_server_row_change()

    assert register._day_tx_cache == [] or loads == [register._current_date]
    assert loads == [register._current_date]
    # fake_load cleared stale; invalidate ran before load.
    assert register._draft_restore_policy == "off"
