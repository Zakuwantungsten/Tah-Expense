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
    assert result["payees"]["top_n"] == 2
    # Pool keeps more than display top_n for client re-rank; first two are top.
    assert payees[0]["label"] == "INFINITY"
    assert payees[0]["daily"]["2026-01-10"]["tzs"] == 100_000.0
    assert payees[0]["daily"]["2026-02-05"]["tzs"] == 50_000.0
    assert payees[1]["label"] == "BOSS SALIM"

    daily = result["daily"]
    assert daily["2026-01-10"]["tzs"] == 100_000.0
    assert daily["2026-01-10"]["count"] == 1.0
    assert daily["2026-02-05"]["count"] == 1.0


def test_compute_overview_charts_excludes_unassigned_payees() -> None:
    docs = [
        {
            "date": datetime(2026, 4, 2),
            "amount": 500_000,
            "currency": "TZS",
            "item": "DIESEL",
            "payee": "",
        },
        {
            "date": datetime(2026, 4, 3),
            "amount": 40_000,
            "currency": "TZS",
            "item": "TOLLS",
            "payee": "INFINITY",
        },
    ]
    result = compute_overview_charts(docs, year=2026, top_payees=5)
    labels = [row["label"] for row in result["payees"]["series"]]
    assert labels == ["INFINITY"]
    assert "Unassigned" not in labels
    # Daily totals still include unassigned spend/entries.
    assert result["daily"]["2026-04-02"]["tzs"] == 500_000.0
    assert result["daily"]["2026-04-02"]["count"] == 1.0


def test_compute_overview_charts_blank_currency_counts_as_tzs() -> None:
    docs = [
        {
            "date": datetime(2026, 6, 1),
            "amount": 12_000,
            "currency": "",
            "item": "PARKING",
            "payee": "GATE",
        }
    ]
    result = compute_overview_charts(docs, year=2026)
    assert result["daily"]["2026-06-01"]["tzs"] == 12_000.0
    assert result["payees"]["series"][0]["label"] == "GATE"


def test_compute_overview_charts_empty() -> None:
    result = compute_overview_charts([], year=2026)
    assert result["items"] == []
    assert result["payees"]["series"] == []
    assert result["daily"] == {}
