"""Issues tab pairs flagged duplicates with their matched original expense."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from PySide6.QtWidgets import QApplication

from tahmeed.models.transaction import Transaction
from tahmeed.models.user import User
from tahmeed.ui.accountant.verify_inbox import (
    VerifyInboxWidget,
    _COL_APP,
    _COL_CHK,
    _COL_DESC,
    _EXISTING_BG,
    _MATCH_ROW_MARKER,
    _TX_IDX_ROLE,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _tx(**kwargs) -> Transaction:
    defaults = dict(
        date=datetime(2026, 8, 20),
        description="PARKING",
        truck_number="YARD",
        amount=5000.0,
        item="PARKING",
        possible_duplicate=False,
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


def test_issues_fill_appends_matched_expense_row():
    _app()
    user = User(
        username="acct",
        password_hash="x",
        role="accountant",
        full_name="Accountant",
        _id=ObjectId(),
    )
    inbox = VerifyInboxWidget(user)
    inbox._current_tab = 3

    original_id = ObjectId()
    flagged_id = ObjectId()
    original = _tx(_id=original_id, date=datetime(2026, 8, 18))
    flagged = _tx(
        _id=flagged_id,
        date=datetime(2026, 8, 20),
        possible_duplicate=True,
        duplicate_of_id=original_id,
    )
    inbox._duplicate_matches = {original_id: original}
    inbox._fill_table([flagged], {}, 0, append=False)

    assert inbox._table.rowCount() == 2
    assert inbox._table.item(0, _COL_CHK).data(_TX_IDX_ROLE) == 0
    assert inbox._table.item(1, _COL_CHK).data(_TX_IDX_ROLE) == _MATCH_ROW_MARKER
    assert "Duplicate" in (inbox._table.item(0, _COL_APP).text() or "")
    assert "vs" in (inbox._table.item(0, _COL_APP).text() or "")
    assert inbox._table.item(1, _COL_APP).text() == "Matched expense"
    assert (
        inbox._table.item(1, _COL_DESC).background().color().name().lower()
        == _EXISTING_BG.lower()
    )
    assert inbox._tx_index_for_table_row(0) == 0
    assert inbox._tx_index_for_table_row(1) is None


def test_transaction_round_trips_duplicate_of_id():
    oid = ObjectId()
    match = ObjectId()
    tx = _tx(_id=oid, possible_duplicate=True, duplicate_of_id=match)
    doc = tx.to_doc()
    assert doc["duplicate_of_id"] == match
    restored = Transaction.from_doc(doc)
    assert restored.duplicate_of_id == match
