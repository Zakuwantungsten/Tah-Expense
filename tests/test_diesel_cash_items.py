"""Diesel Cash item-name filter — configurable, multi-item, source-agnostic."""

from __future__ import annotations

import asyncio
import re

from tahmeed.services.accountant_service import (
    DIESEL_CASH_CATEGORIES,
    _build_diesel_cash_query,
    _diesel_cash_name_filter,
    get_diesel_cash_item_names,
    is_diesel_cash_item,
    normalize_diesel_cash_item_names,
    set_diesel_cash_item_names,
)


def test_normalize_defaults_to_diesel_cash() -> None:
    assert normalize_diesel_cash_item_names(None) == list(DIESEL_CASH_CATEGORIES)
    assert normalize_diesel_cash_item_names("Diesel Cash") == ["Diesel Cash"]


def test_normalize_keeps_many_names_and_dedupes_case() -> None:
    assert normalize_diesel_cash_item_names(
        ["Diesel", "diesel cash", "Diesel", "  DIESEL CASH  ", ""]
    ) == ["Diesel", "diesel cash"]


def test_normalize_empty_stays_empty() -> None:
    assert normalize_diesel_cash_item_names([]) == []


def test_name_filter_matches_any_configured_item_exactly() -> None:
    filt = _diesel_cash_name_filter(["Diesel", "Diesel Cash"])
    assert filt is not None
    pattern, flags = filt["$regex"], re.IGNORECASE
    assert re.fullmatch(pattern, "DIESEL", flags)
    assert re.fullmatch(pattern, "diesel cash", flags)
    assert re.fullmatch(pattern, "Diesel Cash", flags)
    assert not re.fullmatch(pattern, "Diesel Stop", flags)
    assert not re.fullmatch(pattern, "LATRA", flags)
    assert not re.fullmatch(pattern, "Diesel CSH", flags)


def test_name_filter_default_does_not_catch_diesel_alone() -> None:
    filt = _diesel_cash_name_filter()
    assert filt is not None
    assert re.fullmatch(filt["$regex"], "Diesel Cash", re.IGNORECASE)
    assert not re.fullmatch(filt["$regex"], "Diesel", re.IGNORECASE)


def test_name_filter_empty_matches_nothing() -> None:
    assert _diesel_cash_name_filter([]) is None
    assert is_diesel_cash_item("Diesel Cash", []) is False


def test_is_diesel_cash_item_uses_configured_names() -> None:
    names = ["Diesel", "Diesel Cash"]
    assert is_diesel_cash_item("Diesel", names)
    assert is_diesel_cash_item("DIESEL CASH", names)
    assert not is_diesel_cash_item("Toll", names)
    assert not is_diesel_cash_item("", names)
    assert not is_diesel_cash_item(None, names)


def test_query_matches_item_or_category_name_not_description() -> None:
    query = _build_diesel_cash_query(None, item_names=["Diesel", "Diesel Cash"])
    assert query["verified"] is True
    assert "description" not in query
    assert "$or" in query
    fields = {tuple(clause.keys())[0] for clause in query["$or"]}
    assert fields == {"category_name", "item"}
    regex = query["$or"][0]["category_name"]["$regex"]
    assert re.fullmatch(regex, "Diesel", re.IGNORECASE)
    assert re.fullmatch(regex, "Diesel Cash", re.IGNORECASE)


def test_query_empty_item_list_matches_no_rows() -> None:
    query = _build_diesel_cash_query(None, item_names=[])
    assert query["verified"] is True
    assert query["category_name"] == {"$in": []}
    assert "$or" not in query


def test_get_and_set_diesel_cash_item_names(monkeypatch) -> None:
    store: dict = {}

    async def fake_get(key: str):
        return store.get(key)

    async def fake_set(key: str, value):
        store[key] = value

    monkeypatch.setattr(
        "tahmeed.services.settings_service.get_setting", fake_get,
    )
    monkeypatch.setattr(
        "tahmeed.services.settings_service.set_setting", fake_set,
    )

    saved = asyncio.run(set_diesel_cash_item_names(["Diesel", "Diesel Cash", "diesel"]))
    assert saved == ["Diesel", "Diesel Cash"]
    assert store["diesel_cash_items"] == ["Diesel", "Diesel Cash"]

    loaded = asyncio.run(get_diesel_cash_item_names())
    assert loaded == ["Diesel", "Diesel Cash"]

    asyncio.run(set_diesel_cash_item_names([]))
    assert asyncio.run(get_diesel_cash_item_names()) == []


def test_unset_setting_defaults_to_diesel_cash(monkeypatch) -> None:
    async def fake_get(key: str):
        return None

    monkeypatch.setattr(
        "tahmeed.services.settings_service.get_setting", fake_get,
    )
    assert asyncio.run(get_diesel_cash_item_names()) == ["Diesel Cash"]
