"""Afritrack statement footer math and Excel parse."""

from pathlib import Path

from tahmeed.ui.accountant.separate_expenses import (
    _afritrack_compute_footer,
    _read_afritrack_file,
)


def test_footer_matches_april_2026_statement() -> None:
    fig = _afritrack_compute_footer(
        4631.666666666666, 5050.0, 30.0, 30.0, 43.0, 18.0,
    )
    assert round(fig["sub2"][0], 2) == 4661.67
    assert round(fig["sub2"][1], 2) == 5080.00
    assert round(fig["vat"][0], 2) == 839.10
    assert round(fig["vat"][1], 2) == 914.40
    assert round(fig["wht"][0], 2) == 233.08
    assert round(fig["wht"][1], 2) == 254.00
    assert round(fig["payable"][0], 2) == 5267.68
    assert round(fig["payable"][1], 2) == 5740.40
    assert round(fig["total"][0], 2) == 5310.68
    assert fig["bal"][1] is None
    assert fig["total"][1] is None


def test_wht_is_five_percent_of_pre_vat_subtotal() -> None:
    fig = _afritrack_compute_footer(100.0, 200.0, 0.0, 0.0, 0.0, 18.0)
    assert fig["wht"][0] == 5.0
    assert fig["wht"][1] == 10.0
    assert round(fig["vat"][0], 2) == 18.0
    assert round(fig["payable"][0], 2) == 113.0


def test_read_april_2026_file_footer() -> None:
    path = Path(__file__).resolve().parents[1] / (
        "AFRITRACK SCHEDULE OF DIFFERENCES APR 2026.xlsx"
    )
    if not path.exists():
        return
    parsed = _read_afritrack_file(str(path))
    assert parsed.inst_t == 30
    assert parsed.inst_i == 30
    assert parsed.bal_mar == 43
    assert parsed.vat_rate == 18
    assert "Installation Fees" in parsed.inst_label
    assert "Total Payable" in parsed.total_label
    assert round(parsed.statement_tahmeed, 2) == 4631.67
    assert round(parsed.statement_invoice, 2) == 5050.00
    assert len(parsed.rows) == 493
