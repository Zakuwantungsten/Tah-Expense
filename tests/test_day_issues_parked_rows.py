"""Day Issues sees and maps My entries and Parked rows on either tab."""

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


def _local_parked_entry(desc: str) -> dict:
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TRUCK

    return {
        "client_row_id": f"pk-{desc}",
        "cells": {str(COL_DESC): desc, str(COL_TRUCK): "BAD TRUCK"},
        "pending_meta": {"parked": True},
    }


def _seed(register, monkeypatch) -> None:
    from tahmeed.ui.cashier.register_delegates import COL_DESC, COL_TZS

    txs = [
        _tx(register, desc="MY SAVED", status="draft"),
        _tx(register, desc="MY PARKED", status="parked"),
        _tx(register, desc="THEIR PARKED", status="parked", cashier_id=OTHER_CASHIER),
    ]
    register._day_tx_cache = list(txs)
    register._day_tx_cache_key = register._make_day_cache_key(
        register._current_date, merged=False, upload_id=""
    )
    register._local_parked = [_local_parked_entry("LOCAL PARKED")]

    async def _no_load(_d):
        raise AssertionError("cache hit must not network-load")

    monkeypatch.setattr(register, "_load_date", _no_load)
    register._apply_txs_to_view(txs, upload_id="", reload_local_parked=False)
    register._table.blockSignals(True)
    try:
        row = register._saved_count
        register._put(row, COL_DESC, "MY TYPED")
        register._put(row, COL_TZS, "500.00")
    finally:
        register._table.blockSignals(False)


EXPECTED = {"MY SAVED", "MY TYPED", "MY PARKED", "THEIR PARKED", "LOCAL PARKED"}


def _descs(register) -> list[str]:
    return [tx.description for tx in register.snapshot_transactions_for_day_issues()]


def test_day_issues_from_my_tab_includes_parked_rows(register, monkeypatch):
    _seed(register, monkeypatch)
    assert register._parked_mode is False

    descs = _descs(register)

    assert sorted(descs) == sorted(EXPECTED)


def test_day_issues_from_parked_tab_includes_my_entries(register, monkeypatch):
    _seed(register, monkeypatch)
    register.set_view_mode("parked")
    assert register._parked_mode is True

    descs = _descs(register)

    assert sorted(descs) == sorted(EXPECTED)


def test_row_source_for_other_day_includes_unsaved_parked_rows(register):
    from datetime import date

    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        save_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_DESC

    other_day = date(2026, 8, 7)
    save_register_draft(
        build_draft_payload(
            user_id=register._user._id,
            username="aisha",
            register_date=other_day,
            merged=False,
            edit_mode=False,
            dirty_saved=[],
            new_rows=[{"client_row_id": "n1", "cells": {str(COL_DESC): "DRAFT NEW"}}],
            parked_rows=[_local_parked_entry("DRAFT PARKED")],
        )
    )

    replace_txs, extra_txs = register.day_issues_row_source(other_day)

    assert replace_txs is None
    assert sorted(tx.description for tx in extra_txs) == ["DRAFT NEW", "DRAFT PARKED"]


def _payees(register) -> dict:
    return {
        tx.description: tx.payee
        for tx in register.snapshot_transactions_for_day_issues()
    }


def _map(register, descriptions) -> int:
    return register.apply_description_mapping_to_open_rows(
        register._current_date,
        descriptions,
        payee="acme",
        item_name="FUEL",
        cashier_id=register._user._id,
        emit_source_changed=False,
    )


def test_mapping_from_my_tab_reaches_parked_rows(register, monkeypatch):
    _seed(register, monkeypatch)

    assert _map(register, ["THEIR PARKED", "LOCAL PARKED", "MY TYPED"]) == 3

    register.set_view_mode("parked")
    payees = _payees(register)
    assert payees["THEIR PARKED"] == "ACME"
    assert payees["LOCAL PARKED"] == "ACME"
    assert payees["MY TYPED"] == "ACME"
    assert payees["MY PARKED"] == ""


def test_mapping_from_parked_tab_reaches_my_entries(register, monkeypatch):
    _seed(register, monkeypatch)
    register.set_view_mode("parked")

    assert _map(register, ["MY SAVED", "MY TYPED", "THEIR PARKED"]) == 3

    register.set_view_mode("my")
    payees = _payees(register)
    assert payees["MY SAVED"] == "ACME"
    assert payees["MY TYPED"] == "ACME"
    assert payees["THEIR PARKED"] == "ACME"
    assert payees["LOCAL PARKED"] == ""


def test_mapping_for_other_day_stamps_draft_parked_rows(register):
    from datetime import date

    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        cells_from_json,
        load_register_draft,
        save_register_draft,
    )
    from tahmeed.ui.cashier.register_delegates import COL_PAYEE

    other_day = date(2026, 8, 7)
    save_register_draft(
        build_draft_payload(
            user_id=register._user._id,
            username="aisha",
            register_date=other_day,
            merged=False,
            edit_mode=False,
            dirty_saved=[],
            new_rows=[],
            parked_rows=[_local_parked_entry("DRAFT PARKED")],
        )
    )

    n = register.apply_description_mapping_to_open_rows(
        other_day, ["DRAFT PARKED"], payee="acme", item_name="FUEL"
    )

    assert n == 1
    draft = load_register_draft(register._user._id, other_day, merged=False)
    assert cells_from_json(draft["parked_rows"][0]["cells"])[COL_PAYEE] == "ACME"


def test_local_days_lists_open_day_and_draft_days_with_rows(register):
    from datetime import date

    from tahmeed.services.register_draft_service import (
        build_draft_payload,
        local_draft_dates,
        save_register_draft,
    )

    with_rows, dirty_only = date(2026, 8, 5), date(2026, 8, 6)
    common = dict(user_id=register._user._id, username="aisha", edit_mode=False)
    save_register_draft(
        build_draft_payload(
            register_date=with_rows,
            merged=False,
            dirty_saved=[],
            new_rows=[],
            parked_rows=[_local_parked_entry("DRAFT PARKED")],
            **common,
        )
    )
    save_register_draft(
        build_draft_payload(
            register_date=dirty_only,
            merged=True,
            dirty_saved=[{"tx_id": str(ObjectId()), "cells": {}}],
            new_rows=[],
            **common,
        )
    )

    assert local_draft_dates(register._user._id) == [with_rows, dirty_only]
    assert local_draft_dates(ObjectId()) == []
    assert register.day_issues_local_days() == sorted(
        [with_rows, register._current_date]
    )


def test_row_source_for_open_day_is_the_full_snapshot(register, monkeypatch):
    _seed(register, monkeypatch)

    replace_txs, extra_txs = register.day_issues_row_source(register._current_date)

    assert extra_txs is None
    assert sorted(tx.description for tx in replace_txs) == sorted(EXPECTED)
