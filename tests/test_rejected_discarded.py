"""Rejected / discarded cashier flows.

Server-side list / discard / resubmit behaviour is covered in
backend/tests/test_accountant_rejected_api.py.
"""
from __future__ import annotations

from datetime import datetime

from tahmeed.models.transaction import Transaction


def test_transaction_discarded_roundtrip():
    tx = Transaction(
        date=datetime(2026, 7, 1),
        description="X",
        truck_number="T1",
        amount=1.0,
        discarded=True,
        rejected=True,
    )
    doc = tx.to_doc()
    assert doc["discarded"] is True
    restored = Transaction.from_doc(doc)
    assert restored.discarded is True
