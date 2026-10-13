"""Client-side bucketing for cashier Overview charts."""

from datetime import date

from tahmeed.services.cashier_service import (
    bucket_daily_overview,
    bucket_payee_spend,
)


def test_bucket_payee_spend_year() -> None:
    series = [
        {
            "key": "INFINITY",
            "label": "INFINITY",
            "daily": {
                "2026-01-15": {"tzs": 1_000_000, "usd": 0, "zmw": 0},
                "2026-03-10": {"tzs": 500_000, "usd": 0, "zmw": 0},
            },
        }
    ]
    chart = bucket_payee_spend(
        series,
        "year",
        2026,
        "TZS",
        today=date(2026, 4, 10),
    )
    assert chart["labels"][:4] == ["Jan", "Feb", "Mar", "Apr"]
    assert chart["series"][0]["values"][0] == 1_000_000
    assert chart["series"][0]["values"][1] == 0
    assert chart["series"][0]["values"][2] == 500_000
    assert "Spend by month" in chart["subtitle"]


def test_bucket_payee_spend_reranks_for_month() -> None:
    """Year-large payee with no month spend loses to a smaller current-month payee."""
    series = [
        {
            "key": "OLD GIANT",
            "label": "OLD GIANT",
            "daily": {
                "2026-01-05": {"tzs": 5_000_000, "usd": 0, "zmw": 0},
            },
        },
        {
            "key": "THIS MONTH",
            "label": "THIS MONTH",
            "daily": {
                "2026-04-08": {"tzs": 80_000, "usd": 0, "zmw": 0},
            },
        },
        {
            "key": "Unassigned",
            "label": "Unassigned",
            "daily": {
                "2026-04-08": {"tzs": 999_999, "usd": 0, "zmw": 0},
            },
        },
    ]
    chart = bucket_payee_spend(
        series,
        "month",
        2026,
        "TZS",
        today=date(2026, 4, 10),
        top_n=5,
    )
    labels = [row["label"] for row in chart["series"]]
    assert labels == ["THIS MONTH"]
    assert chart["series"][0]["values"][7] == 80_000  # day 8 (1-indexed → index 7)
    assert "Unassigned" not in labels


def test_bucket_daily_overview_money_and_counts() -> None:
    daily = {
        "2026-04-01": {"tzs": 10_000, "usd": 0, "zmw": 0, "count": 2},
        "2026-04-03": {"tzs": 25_000, "usd": 0, "zmw": 0, "count": 5},
        "2026-03-20": {"tzs": 99_000, "usd": 0, "zmw": 0, "count": 9},
    }
    chart = bucket_daily_overview(
        daily,
        "month",
        2026,
        "TZS",
        today=date(2026, 4, 10),
    )
    assert chart["labels"][0] == "1"
    assert chart["money"][0] == 10_000
    assert chart["money"][2] == 25_000
    assert chart["counts"][0] == 2
    assert chart["counts"][2] == 5
    assert sum(chart["money"]) == 35_000
    assert "Activity by day" in chart["subtitle"]
