"""Unit tests for duplicate-draft finder helpers (no live Mongo)."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId

from app.cli.find_duplicate_drafts import content_fingerprint


def test_content_fingerprint_normalizes_money_and_case() -> None:
    cashier = ObjectId()
    a = {
        "description": "  diesel ",
        "truck_number": "t1",
        "item": "Fuel",
        "amount": 1000,
        "currency": "TZS",
        "date": datetime(2026, 8, 8, 10, 0, 0),
        "cashier_id": cashier,
    }
    b = {
        "description": "DIESEL",
        "truck_number": "T1",
        "item": "FUEL",
        "amount": 1000.0,
        "currency": "TZS",
        "date": datetime(2026, 8, 8, 18, 0, 0),
        "cashier_id": cashier,
    }
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_differs_on_amount() -> None:
    base = {
        "description": "DIESEL",
        "truck_number": "",
        "item": "",
        "amount": 1000,
        "currency": "TZS",
        "date": datetime(2026, 8, 8),
    }
    other = {**base, "amount": 1001}
    assert content_fingerprint(base) != content_fingerprint(other)
