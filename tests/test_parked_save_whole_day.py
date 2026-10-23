"""Save on the Parked tab saves the whole day: Parked grid and My entries."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_register_view_draft import _boom_save, qapp, register  # noqa: F401


def _stage_my_and_parked(register, *, with_parked: bool) -> None:
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK, COL_TZS

    register._table.blockSignals(True)
    try:
        register._put(0, COL_DESC, "MY ROW")
        register._put(0, COL_TZS, "1,000.00")
        register._put(0, COL_TRUCK, "YARD")
        if with_parked:
            register._put(1, COL_DESC, "PARK ME")
            register._put(1, COL_TZS, "2,000.00")
            register._put(1, COL_TRUCK, "BAD TRUCK")
    finally:
        register._table.blockSignals(False)
    if with_parked:
        register._pending_row_meta[1] = {"parked": True}
    register._day_tx_cache = []
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )


def _open_parked(register, monkeypatch, *, with_parked: bool) -> list:
    _stage_my_and_parked(register, with_parked=with_parked)
    if with_parked:
        monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
        register._after_truck_dialog_parked()

    async def _no_load(_d):
        return None

    monkeypatch.setattr(register, "_load_date", _no_load)
    register.set_view_mode("parked")
    assert register._parked_mode

    saves: list = []

    async def _fake_save(*, parked_only: bool = False) -> bool:
        saves.append(register._view_mode)
        return True

    monkeypatch.setattr(register, "_do_save", _fake_save)
    return saves


@pytest.mark.asyncio
async def test_parked_save_also_saves_my_entries_then_returns(register, monkeypatch):
    saves = _open_parked(register, monkeypatch, with_parked=True)
    assert register.has_unsaved_work(include_stashed_my=False)
    assert register._my_entries_unsaved()

    assert await register._save_whole_day() is True
    assert saves == ["parked", "my"]
    assert register._view_mode == "parked"


@pytest.mark.asyncio
async def test_parked_save_with_only_my_work_skips_empty_parked_save(
    register, monkeypatch
):
    saves = _open_parked(register, monkeypatch, with_parked=False)
    assert not register.has_unsaved_work(include_stashed_my=False)

    assert await register._save_whole_day() is True
    assert saves == ["my"]
    assert register._view_mode == "parked"


@pytest.mark.asyncio
async def test_parked_save_without_my_work_is_parked_only(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    saves = _open_parked(register, monkeypatch, with_parked=True)
    my = register._my_table
    for row in range(my.rowCount()):
        it = my.item(row, COL_DESC)
        if it is not None and it.text() == "MY ROW":
            my.setRowCount(0)
            break
    assert not register._my_entries_unsaved()

    assert await register._save_whole_day() is True
    assert saves == ["parked"]


@pytest.mark.asyncio
async def test_new_dialog_save_from_parked_saves_whole_day(register, monkeypatch):
    _open_parked(register, monkeypatch, with_parked=True)
    calls: list = []

    async def _whole_day(*, return_to_parked: bool = True) -> bool:
        calls.append(return_to_parked)
        return True

    monkeypatch.setattr(register, "_save_whole_day", _whole_day)
    monkeypatch.setattr(register, "_prompt_unsaved_work", lambda _m: "save")
    monkeypatch.setattr(register, "_open_blank_register", lambda: calls.append("blank"))

    await register._toolbar_new_table()
    assert calls == [False, "blank"]


@pytest.mark.asyncio
async def test_parked_save_real_flow_keeps_and_saves_typed_my_rows(
    register, monkeypatch
):
    """Type My rows, park one, open Parked, Save: both reach the server."""
    from asyncio import tasks

    from bson import ObjectId

    from tahmeed.ui.cashier.register_delegates import COL_DESC

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.asyncio.ensure_future", tasks.ensure_future
    )
    _stage_my_and_parked(register, with_parked=True)
    monkeypatch.setattr(register, "_persist_parked_after_dialog", _boom_save)
    register._after_truck_dialog_parked()

    server: list = []

    async def fake_load(_d):
        txs = list(server)
        register._day_tx_cache = list(txs)
        register._day_tx_cache_key = register._make_day_cache_key(
            register._current_date, merged=False, upload_id=""
        )
        register._apply_txs_to_view(
            txs, upload_id="", reload_local_parked=(register._my_session is None)
        )
        register._maybe_restore_draft_after_populate()
        register._day_tx_stale = False

    async def fake_bulk(txs, *, on_progress=None, should_cancel=None):
        for tx in txs:
            tx._id = ObjectId()
            server.append(tx)
        return [("created", tx, None) for tx in txs]

    async def fake_next_day_order(_d):
        return 1

    async def fake_dup_bulk(candidates, *_a, **_k):
        return [[] for _ in candidates]

    infos: list = []
    monkeypatch.setattr(register, "_load_date", fake_load)
    monkeypatch.setattr(register, "_api_is_online", lambda: True)
    monkeypatch.setattr(register, "_submit_in_flight", True)
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.bulk_save_transactions", fake_bulk)
    monkeypatch.setattr("tahmeed.ui.cashier.excel_grid.next_day_order", fake_next_day_order)
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.check_for_duplicates_bulk", fake_dup_bulk
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.information",
        lambda _p, title, *a, **k: infos.append(title),
    )
    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.QMessageBox.warning",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)),
    )

    register.set_view_mode("parked")
    assert register._table.item(0, COL_DESC).text() == "PARK ME"

    assert await register._save_whole_day() is True
    assert {tx.description: tx.register_status for tx in server} == {
        "PARK ME": "parked",
        "MY ROW": "draft",
    }
    assert "Nothing to save" not in infos
    assert register._view_mode == "parked"


def test_my_disk_rows_survive_parked_draft_rewrite(register):
    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        load_register_draft,
        save_register_draft,
    )

    def _write(new_rows, parked_rows):
        save_register_draft(
            build_draft_payload(
                user_id=register._user._id,
                username="aisha",
                register_date=register._current_date,
                merged=False,
                edit_mode=False,
                dirty_saved=[],
                new_rows=new_rows,
                parked_rows=parked_rows,
            )
        )

    my_row = {"client_row_id": "my-1", "cells": {"description": "MY ROW"}}
    parked_row = {"client_row_id": "pk-1", "cells": {"description": "PARK ME"}}
    _write([my_row], [parked_row])
    stash = register._my_draft_rows_on_disk()
    assert stash and [r["client_row_id"] for r in stash["new_rows"]] == ["my-1"]

    # What a Parked Save's clear does: day draft keeps only parked rows.
    _write([], [parked_row])
    register._restore_my_draft_rows_on_disk(stash)

    draft = load_register_draft(
        register._user._id, register._current_date, merged=False
    )
    assert [r["client_row_id"] for r in draft["new_rows"]] == ["my-1"]
    assert [r["client_row_id"] for r in draft["parked_rows"]] == ["pk-1"]
