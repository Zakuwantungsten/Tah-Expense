"""Tests for duplicate review helpers."""

from __future__ import annotations

from datetime import datetime

from tahmeed.models.transaction import Transaction
from tahmeed.services.duplicate_review import (
    DuplicateReviewItem,
    format_amount_label,
    format_existing_date,
    rows_to_save_with_duplicate_flags,
)


def test_format_amount_label_tzs_only():
    tx = Transaction(
        date=datetime.utcnow(), description="X", truck_number="", amount=1500.0, currency="TZS",
    )
    assert format_amount_label(tx) == "TZS 1,500"


def test_format_amount_label_dual():
    tx = Transaction(
        date=datetime.utcnow(),
        description="X",
        truck_number="T688 EAF",
        amount=1000.0,
        currency="TZS",
        amount_usd=50.0,
    )
    label = format_amount_label(tx)
    assert "TZS 1,000" in label
    assert "USD 50.00" in label


def test_format_existing_date_none():
    assert format_existing_date(None) == "—"


def test_rows_to_save_with_duplicate_flags():
    item = DuplicateReviewItem(
        row=3,
        row_display=4,
        description="PARKING",
        truck_number="YARD",
        item="PARKING",
        amount=5000.0,
        amount_label="TZS 5,000",
        existing=Transaction(
            date=datetime.utcnow(), description="PARKING", truck_number="YARD", amount=5000.0,
        ),
    )
    assert rows_to_save_with_duplicate_flags([item], {3}) == {3: True}
    assert rows_to_save_with_duplicate_flags([item], set()) == {}


def test_register_day_clause_uses_import_primary_date():
    """Manual register saves stamp import_primary_date so rows stay on the open day."""
    from datetime import date as date_cls
    from tahmeed.services.cashier_service import _register_day_clause

    target = date_cls(2026, 8, 23)
    clause = _register_day_clause(target)
    assert "$or" in clause
    branches = clause["$or"]
    assert any("import_primary_date" in b for b in branches)
