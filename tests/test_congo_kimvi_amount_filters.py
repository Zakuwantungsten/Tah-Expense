"""Congo / Kimvi amount-tab query filters (Money In vs Called Out)."""

from tahmeed.services.accountant_service import (
    _congo_entries_query,
    _kimvi_entries_query,
    _usd_amount_clause,
)

_MONEY_IN = {"amount_usd": {"$lt": 0}}
_CALLED_OUT = {"$nor": [{"amount_usd": {"$lt": 0}}]}


def test_usd_amount_clause_modes() -> None:
    assert _usd_amount_clause() is None
    assert _usd_amount_clause(money_in_only=True) == _MONEY_IN
    assert _usd_amount_clause(called_out_only=True) == _CALLED_OUT


def test_congo_all_entries_has_no_amount_clause() -> None:
    assert _congo_entries_query() == {"expense_type": "congo_expenses"}


def test_congo_money_in_filters_negative_usd() -> None:
    q = _congo_entries_query(money_in_only=True)
    assert _MONEY_IN in q["$and"]


def test_congo_called_out_excludes_money_in() -> None:
    q = _congo_entries_query(called_out_only=True)
    assert _CALLED_OUT in q["$and"]


def test_kimvi_all_entries_has_no_amount_clause() -> None:
    assert _kimvi_entries_query() == {"expense_type": "ahmed_kimvi"}


def test_kimvi_money_in_filters_negative_usd() -> None:
    q = _kimvi_entries_query(money_in_only=True)
    assert _MONEY_IN in q["$and"]


def test_kimvi_called_out_excludes_money_in() -> None:
    q = _kimvi_entries_query(called_out_only=True)
    assert _CALLED_OUT in q["$and"]
