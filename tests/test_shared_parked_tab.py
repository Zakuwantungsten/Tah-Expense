"""Parked tab is shared: every cashier's parked rows for the open day."""

from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId

pytest.importorskip("PySide6")

from tests.test_register_view_draft import qapp, register  # noqa: F401

OTHER_CASHIER = ObjectId()


def _tx(register, *, desc: str, status: str, cashier_id=None):
    from tahmeed.models.transaction import Transaction

    return Transaction(
        _id=ObjectId(),
        date=datetime(2026, 8, 8),
        description=desc,
        truck_number="YARD",
        amount=1000.0,
        register_status=status,
        cashier_id=cashier_id if cashier_id is not None else register._user._id,
    )


def _seed_day(register) -> list:
    txs = [
        _tx(register, desc="MY DRAFT", status="draft"),
        _tx(register, desc="MY PARKED", status="parked"),
        _tx(register, desc="THEIR PARKED", status="parked", cashier_id=OTHER_CASHIER),
    ]
    register._day_tx_cache = list(txs)
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    return txs


def _saved_descs(register) -> list[str]:
    return [register._saved_txs[r].description for r in range(register._saved_count)]


def _no_network_load(register, monkeypatch) -> None:
    async def boom(_d):
        raise AssertionError("cache hit must not network-load")

    monkeypatch.setattr(register, "_load_date", boom)


def test_parked_tab_shows_every_cashiers_parked_rows(register, monkeypatch):
    from tahmeed.ui.cashier.register_delegates import COL_CASHIER

    txs = _seed_day(register)
    counts: list[int] = []
    register.parked_count_changed.connect(counts.append)
    _no_network_load(register, monkeypatch)

    register._apply_txs_to_view(list(txs), upload_id="")
    assert _saved_descs(register) == ["MY DRAFT"]
    assert counts[-1] == 2

    register.set_view_mode("parked")
    assert register._parked_mode is True
    assert sorted(_saved_descs(register)) == ["MY PARKED", "THEIR PARKED"]
    assert not register._table.isColumnHidden(COL_CASHIER)


def test_my_tab_never_shows_other_cashiers_rows(register):
    txs = _seed_day(register)
    # Another cashier's row that was unparked back to draft stays theirs.
    txs.append(
        _tx(register, desc="THEIR DRAFT", status="draft", cashier_id=OTHER_CASHIER)
    )

    register._apply_txs_to_view(txs, upload_id="")

    assert _saved_descs(register) == ["MY DRAFT"]


def test_parked_totals_include_other_cashiers(register):
    _seed_day(register)

    parked = register._server_parked_txs_for_totals()

    assert sorted(tx.description for tx in parked) == ["MY PARKED", "THEIR PARKED"]


def test_unparking_other_cashiers_row_does_not_join_my(register, monkeypatch):
    txs = _seed_day(register)
    _no_network_load(register, monkeypatch)
    register._apply_txs_to_view(list(txs), upload_id="")
    register.set_view_mode("parked")

    ids = {register._saved_txs[r].description: register._saved_ids[r]
           for r in range(register._saved_count)}
    register._release_saved_parked_rows(
        [(ids["MY PARKED"], {}), (ids["THEIR PARKED"], {})]
    )

    assert register._view_mode == "my"
    assert sorted(_saved_descs(register)) == ["MY DRAFT", "MY PARKED"]


@pytest.mark.asyncio
async def test_day_load_asks_server_for_shared_parked_rows(register, monkeypatch):
    calls: list[dict] = []

    async def fake_get(_d, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(
        "tahmeed.ui.cashier.excel_grid.get_transactions_by_date", fake_get
    )
    register._view_mode = "my"
    register._parked_mode = False
    register._merged_mode = False

    await register._load_date_body(register._current_date)

    assert calls and calls[0].get("include_parked") is True
    assert str(calls[0].get("cashier_id")) == str(register._user._id)
