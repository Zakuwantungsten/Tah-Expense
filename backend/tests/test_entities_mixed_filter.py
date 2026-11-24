"""Entities mixed filter: OR match + full item catalog resolution."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.accountant_analytics_api import (
    _infer_entity_family,
    _master_base_match,
    _resolve_entity_selection,
)


def test_master_base_match_or_unions_category_and_payee() -> None:
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    anded = _master_base_match(
        start,
        end,
        category_names=["Boss Ahmed"],
        payees=["Cashier A"],
        filters_op="and",
    )
    assert "$or" in anded  # category clause itself uses $or
    assert "payee" in anded

    unioned = _master_base_match(
        start,
        end,
        category_names=["Boss Ahmed"],
        payees=["Cashier A"],
        filters_op="or",
    )
    assert "$or" in unioned
    assert isinstance(unioned["$or"], list)
    assert len(unioned["$or"]) == 2
    assert "payee" not in unioned  # payee lives inside the union branch


def test_infer_entity_family_from_prefix() -> None:
    assert _infer_entity_family("item:boss_ahmed") == "items"
    assert _infer_entity_family("payee:jane") == "payees"
    assert _infer_entity_family("supplier:shell") == "suppliers"
    assert _infer_entity_family("item:x", "mixed") == "mixed"


@pytest.mark.asyncio
async def test_resolve_items_uses_full_catalog_not_sidebar() -> None:
    """Suggested non-sidebar items must resolve (fixes Entities 404)."""
    item_row = {
        "key": "item:boss_ahmed",
        "label": "Boss Ahmed",
        "kind": "item",
        "category_name": "Boss Ahmed",
    }
    db = SimpleNamespace()

    async def _items(_db):
        return [item_row]

    async def _sidebar(_db):
        return []  # would 404 if used

    async def _empty(_db):
        return []

    import app.accountant_analytics_api as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod, "_item_entity_sources", _items)
    monkey.setattr(mod, "_sidebar_item_sources", _sidebar)
    monkey.setattr(mod, "_supplier_entity_sources", _empty)
    monkey.setattr(mod, "_payee_entity_sources", _empty)
    try:
        source_key, label, cats, payees, entities, op = await _resolve_entity_selection(
            db, "mixed", ["item:boss_ahmed"]
        )
        assert source_key == "item:boss_ahmed"
        assert label == "Boss Ahmed"
        assert cats == ["Boss Ahmed"]
        assert payees is None
        assert op == "and"
        assert entities[0]["key"] == "item:boss_ahmed"
    finally:
        monkey.undo()


@pytest.mark.asyncio
async def test_resolve_mixed_item_and_payee_uses_or() -> None:
    item_row = {
        "key": "item:fuel",
        "label": "Fuel",
        "kind": "item",
        "category_name": "Fuel",
    }
    payee_row = {
        "key": "payee:acme",
        "label": "Acme",
        "kind": "payee",
        "payee": "Acme",
    }
    db = SimpleNamespace()

    async def _items(_db):
        return [item_row]

    async def _payees(_db):
        return [payee_row]

    async def _empty(_db):
        return []

    import app.accountant_analytics_api as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod, "_item_entity_sources", _items)
    monkey.setattr(mod, "_supplier_entity_sources", _empty)
    monkey.setattr(mod, "_payee_entity_sources", _payees)
    try:
        _sk, label, cats, payees, _ents, op = await _resolve_entity_selection(
            db, "mixed", ["item:fuel", "payee:acme"]
        )
        assert cats == ["Fuel"]
        assert payees == ["Acme"]
        assert op == "or"
        assert "Fuel" in label and "Acme" in label
    finally:
        monkey.undo()


@pytest.mark.asyncio
async def test_resolve_unknown_item_still_404() -> None:
    db = SimpleNamespace()

    async def _empty(_db):
        return []

    import app.accountant_analytics_api as mod

    monkey = pytest.MonkeyPatch()
    monkey.setattr(mod, "_item_entity_sources", _empty)
    monkey.setattr(mod, "_supplier_entity_sources", _empty)
    monkey.setattr(mod, "_payee_entity_sources", _empty)
    try:
        with pytest.raises(HTTPException) as exc:
            await _resolve_entity_selection(db, "mixed", ["item:missing"])
        assert exc.value.status_code == 404
        assert "Unknown item" in str(exc.value.detail)
    finally:
        monkey.undo()
