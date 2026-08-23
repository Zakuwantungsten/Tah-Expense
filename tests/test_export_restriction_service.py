"""Tests for per-item export restriction helpers."""

from dataclasses import dataclass

import pytest

from tahmeed.models.category import Category
from tahmeed.services.export_restriction_service import (
    DEFAULT_EXPORT_RESTRICT_SURFACES,
    EXPORT_SURFACES,
    filter_overview_rows,
    filter_register_rows,
    filter_transactions,
    is_item_restricted,
    normalize_export_surfaces,
    restricted_names_from_categories,
    transaction_item_name,
)


@dataclass
class _Tx:
    item: str = ""
    category_name: str = ""


def test_normalize_export_surfaces_defaults_on_invalid():
    assert normalize_export_surfaces(None) == DEFAULT_EXPORT_RESTRICT_SURFACES
    assert normalize_export_surfaces(["not_real"]) == []


def test_normalize_export_surfaces_keeps_known_only():
    picked = normalize_export_surfaces(["cashier_register", "bogus", "master_expenses"])
    assert picked == ["cashier_register", "master_expenses"]


def test_restricted_names_from_categories_respects_format():
    cats = [
        Category(name="Alpha", restrict_in_pdf=True),
        Category(name="Beta", restrict_in_excel=True),
        Category(name="Gamma"),
    ]
    assert restricted_names_from_categories(cats, "pdf") == {"alpha"}
    assert restricted_names_from_categories(cats, "excel") == {"beta"}


def test_transaction_item_name_prefers_item():
    assert transaction_item_name(_Tx(item="Fuel", category_name="Other")) == "Fuel"
    assert transaction_item_name(_Tx(category_name="Parking")) == "Parking"


def test_filter_transactions_case_insensitive():
    txs = [_Tx(item="ADD BACKS"), _Tx(item="diesel cash")]
    restricted = {"add backs"}
    out = filter_transactions(txs, restricted)
    assert len(out) == 1
    assert out[0].item == "diesel cash"


def test_filter_register_rows_by_item_column():
    rows = [
        ["01/01/2026", "ADD BACKS", "desc"],
        ["01/01/2026", "Diesel Cash", "desc"],
    ]
    out = filter_register_rows(rows, item_col_index=1, restricted={"add backs"})
    assert len(out) == 1
    assert out[0][1] == "Diesel Cash"


def test_filter_overview_rows_keeps_rows_without_item_name():
    rows = [
        {"item_name": "ADD BACKS", "description": "x"},
        {"item_name": "", "description": "toll plaza"},
        {"item_name": "Diesel Cash", "description": "y"},
    ]
    out = filter_overview_rows(rows, {"add backs"})
    assert len(out) == 2
    assert out[0]["description"] == "toll plaza"
    assert out[1]["item_name"] == "Diesel Cash"


def test_is_item_restricted_empty_name_not_restricted():
    assert not is_item_restricted("", {"alpha"})
    assert not is_item_restricted("  ", {"alpha"})


@pytest.mark.asyncio
async def test_should_apply_export_restriction(monkeypatch):
    from tahmeed.services import export_restriction_service as svc

    async def _enabled():
        return {"master_expenses"}

    monkeypatch.setattr(svc, "get_enabled_export_surfaces", _enabled)
    assert await svc.should_apply_export_restriction("master_expenses", "excel")
    assert not await svc.should_apply_export_restriction("cashier_register", "excel")
    assert not await svc.should_apply_export_restriction("item_quick_report", "excel")
    assert "item_quick_report" not in EXPORT_SURFACES
