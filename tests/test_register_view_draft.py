"""My ↔ Parked draft policy + same-day view cache (no Draft restored thrash)."""

from __future__ import annotations

import sys
from datetime import date, datetime
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


def _tx(register, *, parked: bool = False, desc: str = "DIESEL"):
    from tahmeed.models.transaction import Transaction

    return Transaction(
        _id=ObjectId(),
        date=datetime(2026, 8, 8),
        description=desc,
        truck_number="YARD",
        amount=1000.0,
        register_status="parked" if parked else "draft",
        cashier_id=register._user._id,
    )


def test_parked_view_never_restores_my_draft(register, monkeypatch):
    """My→Parked must not inject unsaved My rows or show Draft restored."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)

    register._flush_local_draft()
    assert register.has_unsaved_work()

    notices: list[tuple] = []
    monkeypatch.setattr(
        register,
        "_show_draft_restored_notice",
        lambda *a: notices.append(a),
    )
    load_calls: list = []

    async def boom_load(_d):
        load_calls.append(_d)
        raise AssertionError("cache hit must not network-load")

    monkeypatch.setattr(register, "_load_date", boom_load)

    parked = _tx(register, parked=True, desc="PARKED ROW")
    my_saved = _tx(register, parked=False, desc="SAVED MY")
    register._day_tx_cache = [parked, my_saved]
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )

    register.set_view_mode("parked")

    assert register._parked_mode is True
    assert notices == []
    assert load_calls == []
    # Only server parked row — not the unsaved My draft DIESEL row as a saved tx.
    assert register._saved_count == 1
    assert register._saved_txs[0].description == "PARKED ROW"


def test_return_to_my_restores_silently(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)
    register._flush_local_draft()

    notices: list = []
    monkeypatch.setattr(
        register,
        "_show_draft_restored_notice",
        lambda *a: notices.append(a),
    )

    parked = _tx(register, parked=True)
    my_saved = _tx(register, parked=False, desc="SAVED")
    register._day_tx_cache = [parked, my_saved]
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )

    register.set_view_mode("parked")
    assert notices == []

    register.set_view_mode("my")
    assert notices == []
    # Silent restore should re-apply the unsaved DIESEL draft onto My.
    assert register.has_unsaved_work()


@pytest.mark.asyncio
async def test_cold_my_load_still_notifies(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
    finally:
        register._table.blockSignals(False)
    register._flush_local_draft()
    register._populate([])

    notices: list = []
    monkeypatch.setattr(
        register,
        "_show_draft_restored_notice",
        lambda *a: notices.append(a),
    )

    async def fake_get(_d, **_k):
        return []

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.get_transactions_by_date",
        fake_get,
    )
    register._draft_restore_policy = "notify"
    register._view_mode = "my"
    register._parked_mode = False
    register._merged_mode = False

    await register._load_date_body(register._current_date)

    assert len(notices) == 1
    assert notices[0][1] >= 1  # new_count


@pytest.mark.asyncio
async def test_stale_load_does_not_leave_loading_overlay(register, monkeypatch):
    """Two loads of one day with a slow description fetch: Loading must clear."""
    import asyncio

    overlay = {"shown": False}
    monkeypatch.setattr(
        register,
        "_show_register_loading",
        lambda *_a, **_k: overlay.__setitem__("shown", True),
    )
    monkeypatch.setattr(
        register,
        "_hide_register_loading",
        lambda: overlay.__setitem__("shown", False),
    )
    scheduled: list = []
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future",
        lambda coro, *a, **k: scheduled.append(coro),
    )
    release_descriptions = asyncio.Event()

    async def slow_descriptions():
        await release_descriptions.wait()
        return []

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.ensure_description_cache",
        slow_descriptions,
    )

    async def fake_get(_d, **_k):
        return []

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.get_transactions_by_date",
        fake_get,
    )
    from tahmeed.services import cashier_service

    cashier_service.invalidate_description_cache()
    register._draft_restore_policy = "off"
    day = register._current_date

    loop = asyncio.get_running_loop()
    first = loop.create_task(register._load_date_body(day))
    for _ in range(3):
        await asyncio.sleep(0)
    await register._load_date_body(day)
    assert overlay["shown"] is False

    release_descriptions.set()
    await first
    assert overlay["shown"] is False
    for coro in scheduled:
        if hasattr(coro, "close"):
            coro.close()


@pytest.mark.asyncio
async def test_load_waiting_on_sync_says_finishing_sync(register, monkeypatch):
    messages: list = []
    monkeypatch.setattr(
        register,
        "_show_register_loading",
        lambda message="Loading…": messages.append(message),
    )
    monkeypatch.setattr(register, "_hide_register_loading", lambda: None)

    async def fake_idle(*, timeout_s=180, require_empty=True):
        register._outbox_drain_in_flight = False
        return True

    async def fake_get(_d, **_k):
        return []

    monkeypatch.setattr(register, "_await_outbox_idle", fake_idle)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.get_transactions_by_date",
        fake_get,
    )
    register._draft_restore_policy = "off"
    register._outbox_drain_in_flight = True

    await register._load_date_body(register._current_date)

    assert messages[0] == "Finishing sync…"
    assert messages[-1].startswith("Loading ")


@pytest.mark.asyncio
async def test_parked_persist_opens_parked_without_draft_notice(register, monkeypatch):
    notices: list = []
    monkeypatch.setattr(
        register,
        "_show_draft_restored_notice",
        lambda *a: notices.append(a),
    )

    async def fake_save(*, parked_only=False):
        assert parked_only is True
        return True

    async def fake_idle(*, timeout_s=180, require_empty=True):
        return True

    modes: list[str] = []

    def fake_view(mode):
        modes.append(mode)
        register._view_mode = mode
        register._parked_mode = mode == "parked"
        register._draft_restore_policy = "off"

    monkeypatch.setattr(register, "_do_save", fake_save)
    monkeypatch.setattr(register, "_await_outbox_idle", fake_idle)
    monkeypatch.setattr(register, "set_view_mode", fake_view)
    register._pending_row_meta[0] = {"parked": True}
    register._view_mode = "my"

    await register._persist_parked_after_dialog()

    assert modes == ["parked"]
    assert notices == []
    assert register._draft_restore_policy == "off"


def _stage_import_like_rows(register):
    """Two unsaved rows: a correct truck (YARD) and a wrong one marked parked."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "DIESEL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
        register._put(1, COL_DESC, "OIL")
        register._put(1, COL_TZS, "2,000.00")
        register._put(1, COL_TRUCK, "BAD TRUCK")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[1] = {
        "parked": True,
        "daily_import_source": "matumizi.xlsx",
    }


def _boom_save(*_a, **_k):
    raise AssertionError("Park all must not save anything to the server")


def test_park_all_keeps_correct_rows_and_parks_locally_without_saving(
    register, monkeypatch
):
    from tahmeed.services.register_draft_service import (
        cells_from_json,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_do_save", _boom_save)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    counts: list[int] = []
    register.parked_count_changed.connect(counts.append)

    register._after_truck_dialog_parked()

    # Correct row stays on My, unsaved and ready for Save.
    descs = [
        (register._table.item(r, COL_DESC).text() if register._table.item(r, COL_DESC) else "")
        for r in range(register._table.rowCount())
    ]
    assert "DIESEL" in descs
    assert "OIL" not in descs
    assert register.has_unsaved_work()
    assert not any(
        (m or {}).get("parked") for m in register._pending_row_meta.values()
    )

    # Wrong-truck row lives in the local Parked bucket (disk + memory) only.
    assert len(register._local_parked) == 1
    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is not None
    assert len(draft["parked_rows"]) == 1
    assert cells_from_json(draft["parked_rows"][0]["cells"])[COL_DESC] == "OIL"
    assert [cells_from_json(e["cells"])[COL_DESC] for e in draft["new_rows"]] == [
        "DIESEL"
    ]
    assert counts and counts[-1] == 1
    assert register._parked_day_count == 1


def test_parked_view_shows_local_parked_rows_and_my_restores_silently(
    register, monkeypatch
):
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_do_save", _boom_save)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    notices: list = []
    monkeypatch.setattr(
        register, "_show_draft_restored_notice", lambda *a: notices.append(a)
    )
    register._after_truck_dialog_parked()

    my_saved = _tx(register, parked=False, desc="SAVED MY")
    register._day_tx_cache = [my_saved]
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )

    flush_calls: list[int] = []
    restore_calls: list[int] = []
    monkeypatch.setattr(
        register, "_flush_local_draft", lambda *a, **k: flush_calls.append(1)
    )
    monkeypatch.setattr(
        register, "_restore_local_draft", lambda *a, **k: restore_calls.append(1)
    )

    register.set_view_mode("parked")
    assert register._parked_mode is True
    assert register._saved_count == 0  # no server-parked rows
    assert register._table is register._parked_table
    assert register._table.rowCount() == 1
    assert register._table.item(0, COL_DESC).text() == "OIL"
    assert register._table.item(0, COL_TRUCK).text() == "BAD TRUCK"
    assert register._row_marked_parked(0)
    # My leftovers still count as unsaved work while Parked is showing.
    assert register.has_unsaved_work()
    assert register._parked_day_count == 1
    assert flush_calls == []
    assert restore_calls == []

    register.set_view_mode("my")
    assert notices == []
    assert register._table is register._my_table
    descs = [
        (register._table.item(r, COL_DESC).text() if register._table.item(r, COL_DESC) else "")
        for r in range(register._table.rowCount())
    ]
    assert "DIESEL" in descs and "OIL" not in descs
    assert register.has_unsaved_work()
    assert register._parked_day_count == 1
    assert flush_calls == []
    assert restore_calls == []


@pytest.mark.asyncio
async def test_parked_save_persists_fixed_as_draft_and_still_parked_as_parked(
    register, monkeypatch
):
    from tahmeed.services.register_draft_service import load_register_draft
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    _stage_import_like_rows(register)
    # Second wrong-truck row so one can be fixed and one left parked.
    register._table.blockSignals(True)
    try:
        register._put(2, COL_DESC, "TYRES")
        register._put(2, COL_TZS, "3,000.00")
        register._put(2, COL_TRUCK, "ALSO BAD")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[2] = {"parked": True}
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    assert len(register._local_parked) == 2

    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    assert register._table.rowCount() == 2

    # Cashier fixes the truck on the OIL row only.
    oil_row = next(
        r for r in range(register._table.rowCount())
        if register._table.item(r, COL_DESC).text() == "OIL"
    )
    register._table.blockSignals(True)
    try:
        register._table.item(oil_row, COL_TRUCK).setText("YARD")
    finally:
        register._table.blockSignals(False)
    assert register.has_unsaved_work()

    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    async def fake_next_day_order(_d):
        return 1

    async def fake_dup_bulk(candidates, *_a, **_k):
        return [[] for _ in candidates]

    sent: list = []

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        for tx in txs:
            tx._id = ObjectId()
            sent.append(tx)
        return [("created", tx, None) for tx in txs]

    load_calls: list = []

    async def fake_load(d):
        load_calls.append(d)

    def boom_mbox(*_a, **_k):
        raise AssertionError("Parked Save must not pop dialogs here")

    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.next_day_order", fake_next_day_order)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk", fake_dup_bulk
    )
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.bulk_save_transactions", fake_bulk)
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.QMessageBox.warning", boom_mbox)
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.QMessageBox.information", boom_mbox)
    monkeypatch.setattr(register, "_load_date", fake_load)

    meta: dict = {}
    ok = await register._do_save_body(meta)
    assert ok is True
    by_desc = {tx.description: tx for tx in sent}
    assert set(by_desc) == {"OIL", "TYRES"}
    assert by_desc["OIL"].register_status == "draft"
    assert by_desc["OIL"].truck_number == "YARD"
    assert by_desc["TYRES"].register_status == "parked"
    assert load_calls == [register._current_date]
    assert register._local_parked == []
    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is None or draft.get("parked_rows") in (None, [])


@pytest.mark.asyncio
async def test_parked_save_persists_unfixed_rows_as_parked(register, monkeypatch):
    from tahmeed.services.register_draft_service import load_register_draft

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")

    monkeypatch.setattr(register, "_api_is_online", lambda: True)

    async def fake_next_day_order(_d):
        return 1

    sent: list = []

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        for tx in txs:
            tx._id = ObjectId()
            sent.append(tx)
        return [("created", tx, None) for tx in txs]

    async def fake_load(_d):
        return None

    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.next_day_order", fake_next_day_order)
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.bulk_save_transactions", fake_bulk)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.warning",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no warning expected")),
    )
    monkeypatch.setattr(register, "_load_date", fake_load)

    meta: dict = {}
    ok = await register._do_save_body(meta)
    assert ok is True
    assert len(sent) == 1
    assert sent[0].description == "OIL"
    assert sent[0].register_status == "parked"
    assert register._local_parked == []
    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is None or draft.get("parked_rows") in (None, [])


@pytest.mark.asyncio
async def test_my_save_after_park_persists_draft_and_parked(register, monkeypatch):
    """Park then Save from My: good rows → draft, parked bucket → parked."""
    from tahmeed.services.register_draft_service import load_register_draft
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    assert "DIESEL" in [
        (register._table.item(r, COL_DESC).text() if register._table.item(r, COL_DESC) else "")
        for r in range(register._table.rowCount())
    ]
    assert len(register._local_parked) == 1
    assert register.has_unsaved_work()

    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    # Force sync create for drafts too so the test sees both statuses without drain.
    monkeypatch.setattr(register, "_submit_in_flight", True)

    async def fake_next_day_order(_d):
        return 1

    async def fake_dup_bulk(candidates, *_a, **_k):
        return [[] for _ in candidates]

    sent: list = []

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        for tx in txs:
            tx._id = ObjectId()
            sent.append(tx)
        return [("created", tx, None) for tx in txs]

    async def fake_load(_d):
        return None

    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.next_day_order", fake_next_day_order)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk", fake_dup_bulk
    )
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.bulk_save_transactions", fake_bulk)
    monkeypatch.setattr(register, "_load_date", fake_load)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.warning",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)),
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        lambda *a, **k: None,
    )

    meta: dict = {}
    ok = await register._do_save_body(meta)
    assert ok is True
    by_desc = {tx.description: tx for tx in sent}
    assert set(by_desc) == {"DIESEL", "OIL"}
    assert by_desc["DIESEL"].register_status == "draft"
    assert by_desc["OIL"].register_status == "parked"
    assert register._local_parked == []
    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is None or (
        not draft.get("parked_rows") and not draft.get("new_rows")
    )


def test_reimport_same_file_replaces_its_parked_rows_only(register, monkeypatch):
    from tahmeed.services.register_draft_service import (
        cells_from_json,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    _stage_import_like_rows(register)
    # A parked row from a different file must survive a re-import.
    register._table.blockSignals(True)
    try:
        register._put(2, COL_DESC, "FROM OTHER FILE")
        register._put(2, COL_TZS, "5.00")
        register._put(2, COL_TRUCK, "XX")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[2] = {"parked": True, "daily_import_source": "other.xlsx"}
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    assert len(register._local_parked) == 2

    register._clear_day_draft_keep_parked(
        register._current_date, source="MATUMIZI.xlsx"
    )

    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is not None
    assert draft["new_rows"] == []
    assert [cells_from_json(e["cells"])[COL_DESC] for e in draft["parked_rows"]] == [
        "FROM OTHER FILE"
    ]
    assert len(register._local_parked) == 1
    assert register._parked_day_count == 1


def test_clear_local_draft_keeps_parked_bucket(register, monkeypatch):
    from tahmeed.services.register_draft_service import load_register_draft

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()

    register._clear_local_draft(both_modes=True)

    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is not None
    assert draft["new_rows"] == []
    assert len(draft["parked_rows"]) == 1


def test_cache_hit_skips_network_on_view_switch(register, monkeypatch):
    calls: list = []

    async def boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("must not fetch")

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.get_transactions_by_date",
        boom,
    )
    monkeypatch.setattr(
        register,
        "_show_draft_restored_notice",
        lambda *a: (_ for _ in ()).throw(AssertionError("no notice")),
    )

    register._day_tx_cache = [
        _tx(register, parked=True),
        _tx(register, parked=False),
    ]
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    register.set_view_mode("my")
    assert calls == []


@pytest.mark.asyncio
async def test_clear_parked_bucket_moves_local_rows_to_trash(register, monkeypatch):
    """Clear parked saves local rows as parked then soft-trashes them."""
    from tahmeed.models.transaction import Transaction
    from tahmeed.services.register_draft_service import load_register_draft
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    assert len(register._local_parked) == 1

    monkeypatch.setattr(
        "tahmeed.ui.dialogs.confirm_dialog.ask_confirm",
        lambda *a, **k: True,
    )
    created: list = []
    trashed: list = []

    async def fake_bulk_save(txs, **_k):
        out = []
        for tx in txs:
            saved = Transaction(
                _id=ObjectId(),
                date=tx.date,
                description=tx.description,
                truck_number=tx.truck_number,
                amount=tx.amount,
                register_status="parked",
                cashier_id=register._user._id,
                client_row_id=tx.client_row_id,
            )
            created.append(saved)
            out.append(("created", saved, None))
        return out

    async def fake_trash(tx_id, cashier_id=None):
        trashed.append(tx_id)
        return "trashed"

    monkeypatch.setattr(
        "tahmeed.services.cashier_service.bulk_save_transactions", fake_bulk_save
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.request_or_delete_transaction", fake_trash
    )

    register.clear_parked_bucket()
    await register._clear_parked_to_trash([])

    assert created and created[0].register_status == "parked"
    assert trashed and trashed[0] == created[0]._id
    assert register._local_parked == []
    assert register._parked_day_count == 0
    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is not None
    assert draft["parked_rows"] == []
    # My leftovers survive clear-parked.
    register.set_view_mode("my")
    descs = [
        (register._table.item(r, COL_DESC).text() if register._table.item(r, COL_DESC) else "")
        for r in range(register._table.rowCount())
    ]
    assert "DIESEL" in descs


@pytest.mark.asyncio
async def test_clear_parked_trashes_saved_server_rows(register, monkeypatch):
    """Legacy server-parked leftovers go to Trash (not back to My as drafts)."""
    parked = _tx(register, parked=True, desc="OLD PARKED")
    register._day_tx_cache = [parked]
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    assert register._saved_count == 1
    assert register._local_parked == []

    monkeypatch.setattr(
        "tahmeed.ui.dialogs.confirm_dialog.ask_confirm",
        lambda *a, **k: True,
    )
    trashed: list = []

    async def fake_trash(tx_id, cashier_id=None):
        trashed.append(tx_id)
        return "trashed"

    async def fake_bulk_save(txs, **_k):
        return []

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.request_or_delete_transaction", fake_trash
    )
    monkeypatch.setattr(
        "tahmeed.services.cashier_service.bulk_save_transactions", fake_bulk_save
    )

    register.clear_parked_bucket()
    await register._clear_parked_to_trash([parked._id])

    assert trashed == [parked._id]
    assert register._local_parked == []
    assert register._parked_day_count == 0
    # Still on Parked surface (empty) — not flipped to My via unpark.
    assert register._view_mode == "parked"


def test_open_parked_truck_correction_uses_no_park_dialog(register, monkeypatch):
    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")

    seen: list = []

    class _FakeDlg:
        def __init__(self, issues, fleet, **kwargs):
            seen.append((issues, kwargs))
            self.issues = []
            self.pending_registry_adds = []
            self.new_labels = []

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Rejected

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.TruckCorrectionDialog", _FakeDlg
    )
    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(register, "_load_fleet_numbers", _noop)
    register.open_parked_truck_correction()

    assert len(seen) == 1
    issues, kwargs = seen[0]
    assert len(issues) == 1
    assert issues[0].original == "BAD TRUCK"
    assert kwargs.get("allow_park") is False
    assert "My entries" in (kwargs.get("intro") or "")


def test_show_truck_correction_on_parked_hides_park_and_promotes(
    register, monkeypatch
):
    """Cell-edit truck dialog while on Parked must not offer Park / re-append."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    before = len(register._local_parked)
    assert before == 1

    seen: list = []

    class _FakeDlg:
        def __init__(self, issues, fleet, **kwargs):
            seen.append(kwargs)
            self.issues = list(issues)
            self.pending_registry_adds = []
            self.new_labels = []
            self._on_resolved = kwargs.get("on_resolved")

        def exec(self):
            from PySide6.QtWidgets import QDialog

            for issue in list(self.issues):
                issue.corrected = issue.original
                issue.allow_anyway = True
                issue.parked = False
                if self._on_resolved:
                    self._on_resolved(issue)
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.TruckCorrectionDialog", _FakeDlg
    )

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(register, "_load_fleet_numbers", _noop)

    issue = TruckIssue(row=0, original="BAD TRUCK", kind="not_in_registry")
    register._show_truck_correction([issue])

    assert seen and seen[0].get("allow_park") is False
    assert register._view_mode == "my"
    assert register._local_parked == []
    descs = [
        (
            register._table.item(r, COL_DESC).text()
            if register._table.item(r, COL_DESC)
            else ""
        )
        for r in range(register._table.rowCount())
    ]
    assert "OIL" in descs
    assert "DIESEL" in descs


def test_allow_live_clears_parked_meta_so_park_sweep_skips_row(register, monkeypatch):
    """Allow on My must clear parked marks — otherwise Park remaining sweeps them."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC
    from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    # Pretend row 1 was marked parked, then Allow resolves it.
    issue = TruckIssue(
        row=1, original="BAD TRUCK", kind="not_in_registry", corrected="BAD TRUCK"
    )
    issue.allow_anyway = True
    issue.parked = False
    register._on_truck_issue_resolved_live(issue)
    assert not register._row_marked_parked(1)

    register._after_truck_dialog_parked()
    assert register._local_parked == []
    descs = [
        (
            register._table.item(r, COL_DESC).text()
            if register._table.item(r, COL_DESC)
            else ""
        )
        for r in range(register._table.rowCount())
    ]
    assert "OIL" in descs
    assert "DIESEL" in descs


def test_park_rows_locally_does_not_duplicate_client_row_id(register, monkeypatch):
    from tahmeed.services.register_draft_service import entry_client_row_id

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    assert len(register._local_parked) == 1
    rid = entry_client_row_id(register._local_parked[0])
    assert rid

    # Re-stage the same logical row (same client_row_id) on My and park again.
    register.set_view_mode("my")
    _stage_import_like_rows(register)
    register._pending_row_meta[1] = {
        "parked": True,
        "client_row_id": rid,
        "daily_import_source": "matumizi.xlsx",
    }
    register._after_truck_dialog_parked()
    assert len(register._local_parked) == 1
    assert entry_client_row_id(register._local_parked[0]) == rid


@pytest.mark.asyncio
async def test_promote_saved_parked_correct_persists_truck(register, monkeypatch):
    """Fix trucks → Correct on server-saved parked must PATCH truck + draft."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK

    parked = _tx(register, parked=True, desc="OIL")
    parked.truck_number = "BAD TRUCK"
    register._day_tx_cache = [parked]
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    assert register._saved_count == 1
    assert register._table.item(0, COL_DESC).text() == "OIL"

    # Simulate Fix trucks → Correct writing a valid fleet/place truck.
    register._fleet_numbers.add("T688 EAF")
    register._table.blockSignals(True)
    try:
        register._table.item(0, COL_TRUCK).setText("T688 EAF")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[0] = dict(register._pending_row_meta.get(0) or {})
    register._pending_row_meta[0].pop("parked", None)
    register._dirty_rows.add(0)
    assert register._row_truck_is_valid(0)

    patches: list = []

    async def capture_unpark(items, **_k):
        patches.extend(list(items))

    pending: list = []

    def capture_future(coro, *_a, **_k):
        pending.append(coro)
        return None

    monkeypatch.setattr(register, "_unpark_saved_rows", capture_unpark)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future", capture_future
    )

    n = register._promote_accepted_parked_rows_to_my()
    assert n == 1
    assert pending
    await pending[0]
    assert len(patches) == 1
    tx_id, updates = patches[0]
    assert tx_id == parked._id
    assert updates.get("register_status") == "draft"
    assert updates.get("truck_number") == "T688 EAF"
    assert "edited_after_verification" not in updates


def test_parked_allow_all_promotes_rows_to_my(register, monkeypatch):
    """Allow all from Fix parked trucks must leave Parked and land on My."""
    from tahmeed.services.register_draft_service import (
        cells_from_json,
        load_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    assert register._parked_mode is True
    assert register._local_parked  # OIL parked

    class _FakeDlg:
        def __init__(self, issues, fleet, **kwargs):
            self.issues = list(issues)
            self.pending_registry_adds = []
            self.new_labels = []
            self._on_resolved = kwargs.get("on_resolved")

        def exec(self):
            from PySide6.QtWidgets import QDialog

            for issue in list(self.issues):
                issue.corrected = issue.original
                issue.allow_anyway = True
                if self._on_resolved:
                    self._on_resolved(issue)
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.TruckCorrectionDialog", _FakeDlg
    )

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(register, "_load_fleet_numbers", _noop)
    statuses: list[str] = []
    register.transient_status.connect(statuses.append)

    register.open_parked_truck_correction()

    assert register._view_mode == "my"
    assert register._parked_mode is False
    assert register._local_parked == []
    assert register._parked_day_count == 0

    descs = [
        (
            register._table.item(r, COL_DESC).text()
            if register._table.item(r, COL_DESC)
            else ""
        )
        for r in range(register._table.rowCount())
    ]
    assert "DIESEL" in descs
    assert "OIL" in descs

    # Allowed truck must still resolve as ok on My.
    oil_row = next(
        r
        for r in range(register._table.rowCount())
        if register._table.item(r, COL_DESC)
        and register._table.item(r, COL_DESC).text() == "OIL"
    )
    assert register._row_truck_is_valid(oil_row)
    assert not register._row_marked_parked(oil_row)

    draft = load_register_draft(register._user._id, register._current_date, merged=False)
    assert draft is not None
    assert draft.get("parked_rows") in ([], None)
    assert "OIL" in [
        cells_from_json(e["cells"])[COL_DESC] for e in draft.get("new_rows") or []
    ]
    assert statuses and "My entries" in statuses[-1]


def test_open_parked_truck_correction_includes_empty_truck(register, monkeypatch):
    """A parked row whose truck was cleared still opens in Fix trucks."""
    from tahmeed.ui.cashier.register_delegates import COL_TRUCK

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    register._table.blockSignals(True)
    try:
        register._table.item(0, COL_TRUCK).setText("")
    finally:
        register._table.blockSignals(False)

    seen: list = []

    class _FakeDlg:
        def __init__(self, issues, fleet, **kwargs):
            seen.append((issues, kwargs))
            self.issues = []
            self.pending_registry_adds = []
            self.new_labels = []

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Rejected

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.TruckCorrectionDialog", _FakeDlg
    )

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(register, "_load_fleet_numbers", _noop)
    register.open_parked_truck_correction()

    assert len(seen) == 1
    issues, kwargs = seen[0]
    assert len(issues) == 1
    assert issues[0].original == ""
    assert kwargs.get("allow_park") is False


def test_no_truck_promotes_empty_parked_row_to_my(register, monkeypatch):
    """No truck on a blank parked row moves it to My with an empty truck cell."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK

    _stage_import_like_rows(register)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register.set_view_mode("parked")
    register._table.blockSignals(True)
    try:
        register._table.item(0, COL_TRUCK).setText("")
    finally:
        register._table.blockSignals(False)

    class _FakeDlg:
        def __init__(self, issues, fleet, **kwargs):
            self.issues = list(issues)
            self.pending_registry_adds = []
            self.new_labels = []
            self._on_resolved = kwargs.get("on_resolved")

        def exec(self):
            from PySide6.QtWidgets import QDialog

            for issue in list(self.issues):
                issue.corrected = ""
                issue.skip = False
                issue.accept_empty = True
                issue.parked = False
                if self._on_resolved:
                    self._on_resolved(issue)
            return QDialog.Accepted

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.TruckCorrectionDialog", _FakeDlg
    )

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(register, "_load_fleet_numbers", _noop)
    register.open_parked_truck_correction()

    assert register._view_mode == "my"
    assert register._local_parked == []
    oil_row = next(
        r
        for r in range(register._table.rowCount())
        if register._table.item(r, COL_DESC)
        and register._table.item(r, COL_DESC).text() == "OIL"
    )
    truck = register._table.item(oil_row, COL_TRUCK)
    assert (truck.text() if truck else "") == ""
    assert register._row_accepts_empty_truck(oil_row)
    assert not register._row_marked_parked(oil_row)


def test_accept_empty_truck_waives_required_truck_only_for_that_row(register):
    """No truck waives the save gate for that row; other empty rows still block."""
    from tahmeed.models.category import Category
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_ITEM, COL_TZS

    fuel = Category(name="Fuel", requires_truck=True)
    register._categories = [fuel]
    register._cat_by_name = {fuel.name.lower(): fuel}
    register._table.blockSignals(True)
    try:
        register._put(0, COL_ITEM, "Fuel")
        register._put(0, COL_DESC, "OIL")
        register._put(0, COL_TZS, "1,000.00")
        register._put(1, COL_ITEM, "Fuel")
        register._put(1, COL_DESC, "DIESEL")
        register._put(1, COL_TZS, "2,000.00")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[0] = {"accept_empty_truck": True}

    assert register._rows_missing_required_truck() == [1]
    waived = register._build_transaction_from_row(0)
    assert waived is not None
    assert waived.truck_number == ""
    assert waived.register_status == "draft"
    with pytest.raises(ValueError, match="Truck number is required"):
        register._build_transaction_from_row(1)


def test_toolbar_parked_tools_live_on_parked_tab(qapp):
    from tahmeed.ui.widgets.qb_txn_toolbar import QbTxnToolbar

    bar = QbTxnToolbar(register_actions=True)
    assert bar._btn_fix_parked is not None
    assert bar._btn_clear_parked is not None
    assert not bar._btn_fix_parked.isEnabled()
    assert not bar._btn_clear_parked.isEnabled()
    # qtawesome must resolve a real glyph (mdi.truck-alert-outline is missing).
    assert not bar._btn_fix_parked.icon().pixmap(20, 20).isNull()

    bar.set_parked_count(2)
    assert not bar._btn_fix_parked.isEnabled()
    assert not bar._btn_clear_parked.isEnabled()

    bar.set_parked_mode("parked")
    assert bar._btn_fix_parked.isEnabled()
    assert bar._btn_clear_parked.isEnabled()
    assert not bar._btn_fix_parked.icon().pixmap(20, 20).isNull()

    bar.set_parked_mode("my")
    assert not bar._btn_fix_parked.isEnabled()
    assert not bar._btn_clear_parked.isEnabled()


def test_my_parked_flip_keeps_my_rows_without_draft_restore(register, monkeypatch):
    """Large leftover My batches must survive My↔Parked without disk restore."""
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.setRowCount(50)
    register._init_editable_rows(0, 50)
    register._table.blockSignals(True)
    try:
        for i in range(40):
            register._put(i, COL_DESC, f"ROW-{i}")
            register._put(i, COL_TZS, "1,000.00")
            register._put(i, COL_TRUCK, "YARD")
        register._put(40, COL_DESC, "PARK ME")
        register._put(40, COL_TZS, "2,000.00")
        register._put(40, COL_TRUCK, "BAD TRUCK")
    finally:
        register._table.blockSignals(False)
    register._pending_row_meta[40] = {"parked": True}
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()

    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )

    restore_calls: list[int] = []
    flush_calls: list[int] = []
    load_calls: list = []

    def boom_restore(*_a, **_k):
        restore_calls.append(1)
        return None

    def counting_flush(*_a, **_k):
        flush_calls.append(1)

    async def boom_load(_d):
        load_calls.append(_d)
        raise AssertionError("keep-alive flip must not network-load")

    monkeypatch.setattr(register, "_restore_local_draft", boom_restore)
    monkeypatch.setattr(register, "_flush_local_draft", counting_flush)
    monkeypatch.setattr(register, "_load_date", boom_load)

    my_table = register._my_table
    register.set_view_mode("parked")
    assert register._table is register._parked_table
    assert register._table.item(0, COL_DESC).text() == "PARK ME"
    register.set_view_mode("my")
    assert register._table is my_table
    descs = {
        (register._table.item(r, COL_DESC).text() if register._table.item(r, COL_DESC) else "")
        for r in range(register._table.rowCount())
    }
    assert "ROW-0" in descs and "ROW-39" in descs
    assert "PARK ME" not in descs
    assert restore_calls == []
    assert flush_calls == []
    assert load_calls == []


def test_merged_round_trip_keeps_my_grid_and_skips_reload(register, monkeypatch):
    """My → Merged → My shows the mounted My grid. Merged is every cashier."""
    from tahmeed.ui.cashier.register_delegates import COL_CASHIER, COL_DESC

    loads: list = []

    async def _load(d):
        loads.append(d)

    monkeypatch.setattr(register, "_load_date", _load)
    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "MINE")
    finally:
        register._table.blockSignals(False)

    mine = _tx(register, desc="MINE-SAVED")
    theirs = _tx(register, desc="THEIRS")
    theirs.cashier_id = ObjectId()
    register._merged_tx_cache = [mine, theirs]
    register._merged_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=True
    )

    register.set_view_mode("merged")
    assert loads == []
    assert register._table is register._merged_table
    assert register._merged_table.isColumnHidden(COL_CASHIER) is False
    painted = {
        register._merged_table.item(row, COL_DESC).text()
        for row in range(register._merged_table.rowCount())
        if register._merged_table.item(row, COL_DESC) is not None
        and register._merged_table.item(row, COL_DESC).text()
    }
    assert painted == {"MINE-SAVED", "THEIRS"}

    register.set_view_mode("my")
    assert loads == []
    assert register._table is register._my_table
    assert register._my_table.item(0, COL_DESC).text() == "MINE"
    assert register._my_table.isColumnHidden(COL_CASHIER) is True

    register.set_view_mode("merged")
    assert loads == []
    assert register._table is register._merged_table
    assert "THEIRS" in {
        register._merged_table.item(row, COL_DESC).text()
        for row in range(register._merged_table.rowCount())
        if register._merged_table.item(row, COL_DESC) is not None
        and register._merged_table.item(row, COL_DESC).text()
    }
