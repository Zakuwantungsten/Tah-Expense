"""Unit tests for cashier Overview chart aggregations."""

from datetime import datetime

from app.cashier_overview_ops import compute_overview_charts


def test_compute_overview_charts_items_and_top_payees() -> None:
    docs = [
        {
            "date": datetime(2026, 1, 10),
            "amount": 100_000,
            "currency": "TZS",
            "item": "DIESEL",
            "payee": "INFINITY",
        },
        {
            "date": datetime(2026, 2, 5),
            "amount": 50_000,
            "currency": "TZS",
            "category_name": "MILEAGE",
            "payee": "INFINITY",
        },
        {
            "date": datetime(2026, 3, 1),
            "amount": 20_000,
            "currency": "TZS",
            "item": "TOLLS",
            "payee": "BOSS SALIM",
        },
        {
            "date": datetime(2026, 4, 1),
            "amount": 10_000,
            "currency": "TZS",
            "item": "PARKING",
            "payee": "OTHER PAYEE",
        },
        {
            "date": datetime(2025, 12, 31),
            "amount": 999_999,
            "currency": "TZS",
            "item": "OLD",
            "payee": "OLD PAYEE",
        },
        {
            "date": datetime(2026, 5, 1),
            "amount": 5_000,
            "currency": "TZS",
            "item": "TRASHED",
            "payee": "X",
            "trashed": True,
        },
    ]

    result = compute_overview_charts(docs, year=2026, top_items=2, top_payees=2)

    assert result["year"] == 2026
    item_names = [row["name"] for row in result["items"]]
    assert item_names[0] == "DIESEL"
    assert item_names[1] == "MILEAGE"
    assert "Other" in item_names
    other = next(row for row in result["items"] if row["name"] == "Other")
    assert other["tzs"] == 30_000.0

    payees = result["payees"]["series"]
    assert len(payees) == 2
    assert payees[0]["label"] == "INFINITY"
    assert payees[0]["daily"]["2026-01-10"]["tzs"] == 100_000.0
    assert payees[0]["daily"]["2026-02-05"]["tzs"] == 50_000.0
    assert payees[1]["label"] == "BOSS SALIM"


def test_compute_overview_charts_empty() -> None:
    result = compute_overview_charts([], year=2026)
    assert result["items"] == []
    assert result["payees"]["series"] == []
