"""Master Expenses toolbar / column filter query kwargs."""

from __future__ import annotations

from tahmeed.services.accountant_service import _master_params


def test_master_params_omits_receipt_all() -> None:
    params = _master_params(year=2026, receipt="all", search="diesel")
    assert "receipt" not in params
    assert params["search"] == "diesel"
    assert params["year"] == 2026


def test_master_params_includes_receipt_when_set() -> None:
    params = _master_params(year=2026, receipt="pending")
    assert params["receipt"] == "pending"


def test_master_params_serializes_column_filters() -> None:
    params = _master_params(
        year=2026,
        column_filters={"item": ["Fuel", "Parking"]},
    )
    assert "column_filters" in params
    assert "Fuel" in params["column_filters"]
