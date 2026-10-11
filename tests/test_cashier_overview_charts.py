"""Client-side bucketing for cashier Overview payee trend."""

from datetime import date

from tahmeed.services.cashier_service import bucket_payee_spend


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
