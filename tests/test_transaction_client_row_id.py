"""Transaction.client_row_id round-trip for Phase 1 register identity."""

from __future__ import annotations

from datetime import datetime

from tahmeed.models.transaction import Transaction


def test_to_doc_omits_blank_client_row_id():
    tx = Transaction(
        date=datetime(2026, 8, 8),
        description="DIESEL",
        truck_number="T100",
        amount=1000.0,
    )
    doc = tx.to_doc()
    assert "client_row_id" not in doc


def test_to_doc_and_from_doc_preserve_client_row_id():
    rid = "11111111-2222-3333-4444-555555555555"
    tx = Transaction(
        date=datetime(2026, 8, 8),
        description="DIESEL",
        truck_number="T100",
        amount=1000.0,
        client_row_id=rid,
    )
    doc = tx.to_doc()
    assert doc["client_row_id"] == rid
    back = Transaction.from_doc(doc)
    assert back.client_row_id == rid
