"""Unit tests for Analytics period helpers + feed catalog."""

from datetime import date

from app.accountant_analytics_ops import (
    SEPARATE_EXPENSE_FEEDS,
    averages_from_totals,
    collapse_top_n,
    normalize_period,
    pareto_for_currency,
    period_day_count,
    period_label,
    period_window,
)


def test_separate_expense_feeds_cover_sidebar_tabs() -> None:
    keys = {spec.key for spec in SEPARATE_EXPENSE_FEEDS}
    assert keys == {
        "toll_plaza",
        "parking_congo",
        "congo_exp",
        "ahmed_kimvi",
        "zambia_parking",
        "afritrack",
        "third_party",
        "comesa",
        "sm_burhani",
        "rahntech",
    }
    currencies = {spec.currency for spec in SEPARATE_EXPENSE_FEEDS}
    assert currencies == {"USD", "TZS", "ZMW"}


def test_normalize_period_year_and_month() -> None:
    assert normalize_period(2026, "year", None) == ("year", None)
    assert normalize_period(2026, "month", 3) == ("month", 3)


def test_period_window_month() -> None:
    start, end = period_window(2026, "month", 2)
    assert start.year == 2026 and start.month == 2 and start.day == 1
    assert end.day == 28


def test_period_day_count_uses_elapsed_days_for_open_year() -> None:
    days = period_day_count(2026, "year", today=date(2026, 1, 10))
    assert days == 10


def test_averages_from_totals() -> None:
    avgs = averages_from_totals({"tzs": 100.0, "usd": 50.0, "zmw": 25.0}, 10)
    assert avgs == {"tzs": 10.0, "usd": 5.0, "zmw": 2.5}


def test_period_label() -> None:
    assert period_label(2026, "year") == "2026"
    assert period_label(2026, "month", 11) == "Nov 2026"


def test_collapse_top_n_folds_rest() -> None:
    rows = [
        {"name": "A", "tzs": 100, "usd": 0, "zmw": 0, "count": 1},
        {"name": "B", "tzs": 50, "usd": 0, "zmw": 0, "count": 2},
        {"name": "C", "tzs": 10, "usd": 0, "zmw": 0, "count": 3},
    ]
    out = collapse_top_n(rows, top_n=2)
    assert [r["name"] for r in out] == ["A", "B", "Other"]
    assert out[-1]["tzs"] == 10
    assert out[-1]["count"] == 3


def test_pareto_for_currency_splits_80_20() -> None:
    rows = [
        {"tzs": 80, "count": 10},
        {"tzs": 15, "count": 5},
        {"tzs": 5, "count": 2},
    ]
    result = pareto_for_currency(rows, "tzs")
    assert result["total"] == 100
    assert result["top80"]["amount"] == 80
    assert result["bottom20"]["amount"] == 20
    assert result["top80"]["count"] == 10
    assert result["bottom20"]["count"] == 7
