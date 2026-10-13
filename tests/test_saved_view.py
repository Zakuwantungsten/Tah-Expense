"""Saved inbox: day summaries, mapping counts, money buckets, search."""

from __future__ import annotations

from datetime import date, datetime

from tahmeed.models.transaction import Transaction
from tahmeed.ui.cashier.saved_view import (
    _COL_ACTIONS,
    _COL_DATE,
    _COL_DEFAULTS,
    _COL_SNO,
    _SAVED_ROW,
    _SAVED_ROW_ALT,
    filter_day_summaries,
    filter_saved_rows,
    flatten_draft_day_rows,
    format_tx_amount,
    summarize_draft_day,
    summarize_draft_day_batches,
    tx_is_mapped,
    tx_is_refund_to_float,
    tx_money_buckets,
    tx_register_day,
)


def _tx(**kwargs) -> Transaction:
    defaults = {
        "date": datetime(2026, 3, 10),
        "description": "Fuel",
        "item": "Diesel",
        "truck_number": "T123",
        "amount": 50000.0,
        "currency": "TZS",
        "payee": "SHELL",
    }
    defaults.update(kwargs)
    return Transaction(**defaults)


def test_flatten_preserves_newest_day_batches() -> None:
    d1, d2 = date(2026, 3, 11), date(2026, 3, 10)
    a, b, c = _tx(description="A"), _tx(description="B"), _tx(description="C")
    rows = flatten_draft_day_rows([(d1, [a, b]), (d2, [c])])
    assert [r[0] for r in rows] == [d1, d1, d2]
    assert [r[1].description for r in rows] == ["A", "B", "C"]


def test_filter_matches_description_item_truck_date() -> None:
    day = date(2026, 3, 10)
    rows = [
        (day, _tx(description="Parking Congo", item="Parking", truck_number="T1")),
        (day, _tx(description="Fuel stop", item="Diesel", truck_number="T99")),
    ]
    assert len(filter_saved_rows(rows, "congo")) == 1
    assert len(filter_saved_rows(rows, "diesel")) == 1
    assert len(filter_saved_rows(rows, "t99")) == 1
    assert len(filter_saved_rows(rows, "10 mar")) == 2
    assert filter_saved_rows(rows, "zzz") == []
    assert filter_saved_rows(rows, "") == rows


def test_tx_register_day_prefers_import_primary() -> None:
    tx = _tx(
        date=datetime(2026, 3, 10),
        import_primary_date=datetime(2026, 3, 1),
    )
    assert tx_register_day(tx) == date(2026, 3, 1)
    assert tx_register_day(_tx()) == date(2026, 3, 10)


def test_format_tx_amount_tzs() -> None:
    assert format_tx_amount(_tx(amount=1200.0)) == "TZS 1,200"


def test_tx_is_mapped_requires_item_and_payee() -> None:
    assert tx_is_mapped(_tx(item="Diesel", payee="SHELL")) is True
    assert tx_is_mapped(_tx(item="", payee="SHELL")) is False
    assert tx_is_mapped(_tx(item="Diesel", payee="")) is False
    assert tx_is_mapped(_tx(item="", category_name="Parking", payee="CITY")) is True


def test_tx_money_buckets_split_currencies() -> None:
    assert tx_money_buckets(_tx(amount=1000, currency="TZS")) == (1000.0, 0.0, 0.0)
    assert tx_money_buckets(_tx(amount=25, currency="USD")) == (0.0, 25.0, 0.0)
    assert tx_money_buckets(_tx(amount=40, currency="ZMW")) == (0.0, 0.0, 40.0)


def test_saved_table_layout_has_sno_and_faint_blue() -> None:
    assert _COL_SNO == 0
    assert _COL_DATE == 1
    assert _COL_ACTIONS == 9
    assert len(_COL_DEFAULTS) == 10
    assert _SAVED_ROW == "#E8F4FD"
    assert _SAVED_ROW_ALT == "#EFF6FF"


def test_tx_is_refund_to_float() -> None:
    assert tx_is_refund_to_float(_tx(notes_flag=True)) is True
    assert tx_is_refund_to_float(_tx(ref_float="REFUND TO FLOAT")) is True
    assert tx_is_refund_to_float(_tx(ref_float="REFUND TO MY FLOAT")) is True
    assert tx_is_refund_to_float(_tx(ref_float="OTHER")) is False
    assert tx_is_refund_to_float(_tx(ref_float="")) is False


def test_summarize_draft_day_counts_and_totals() -> None:
    day = date(2026, 10, 13)
    rows = [
        _tx(amount=100_000, item="Parking", payee="CITY"),
        _tx(amount=50_000, item="", payee="", description="UNMAPPED"),
        _tx(amount=25, currency="USD", item="Fee", payee="BANK"),
        _tx(amount=10, currency="ZMW", item="Toll", payee="ZRA"),
        _tx(
            amount=5000,
            item="Cash",
            payee="ME",
            notes_flag=True,
            description="REFUND",
        ),
    ]
    s = summarize_draft_day(day, rows)
    assert s.day == day
    assert s.entries_count == 5
    assert s.mapped_count == 4
    assert s.unmapped_count == 1
    assert s.total_tzs == 155_000.0  # 100k + 50k + 5k refund
    assert s.total_usd == 25.0
    assert s.total_zmw == 10.0
    assert s.total_refund == 5000.0


def test_summarize_batches_and_filter_by_date() -> None:
    d1, d2 = date(2026, 10, 13), date(2026, 10, 12)
    batches = [
        (d1, [_tx(amount=1), _tx(amount=2, item="", payee="")]),
        (d2, [_tx(amount=3, currency="USD")]),
    ]
    days = summarize_draft_day_batches(batches)
    assert [s.day for s in days] == [d1, d2]
    assert days[0].entries_count == 2
    assert days[0].unmapped_count == 1
    assert len(filter_day_summaries(days, "13 oct")) == 1
    assert len(filter_day_summaries(days, "zzz")) == 0
