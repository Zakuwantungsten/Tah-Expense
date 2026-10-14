"""Warm Description Maps cache — silent O(1) resolve after ensure."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tahmeed.services import description_mapping_service as dms


@pytest.fixture(autouse=True)
def _reset_maps_cache():
    dms.invalidate_description_mapping_cache()
    yield
    dms.invalidate_description_mapping_cache()


def _mapping(*, key: str, item: str, payee: str = ""):
    return SimpleNamespace(
        description_key=key,
        description=key,
        category_name=item,
        payee=payee,
        category_id=None,
    )


@pytest.mark.asyncio
async def test_ensure_cache_then_resolve_is_local(monkeypatch) -> None:
    pages = {"n": 0}

    async def fake_pages(path, *, params=None):
        pages["n"] += 1
        assert path == "v1/description-mappings"
        return [
            {
                "description_key": "PARKING CONGO",
                "description": "PARKING CONGO",
                "category_id": "507f1f77bcf86cd799439011",
                "category_name": "Parking",
                "payee": "CITY",
            }
        ]

    async def boom(*_a, **_k):
        raise AssertionError("resolve must not hit keyed API when cache is warm")

    monkeypatch.setattr(dms, "get_all_pages", fake_pages)
    monkeypatch.setattr(dms, "get_mapping", boom)

    await dms.ensure_description_mapping_cache()
    assert dms.description_mapping_cache_ready()
    assert pages["n"] == 1

    hit = await dms.resolve_mapping_for_description("parking congo")
    assert hit is not None
    assert hit.category_name == "Parking"
    assert hit.payee == "CITY"

    miss = await dms.resolve_mapping_for_description("UNKNOWN DESC")
    assert miss is None

    # Second ensure does not reload.
    await dms.ensure_description_mapping_cache()
    assert pages["n"] == 1


@pytest.mark.asyncio
async def test_get_mappings_for_descriptions_uses_warm_cache(monkeypatch) -> None:
    async def fake_pages(path, *, params=None):
        return [
            {
                "description_key": "LATRA FEE",
                "description": "LATRA FEE",
                "category_id": "507f1f77bcf86cd799439011",
                "category_name": "Parking",
                "payee": "COUNCIL",
            },
            {
                "description_key": "DIESEL",
                "description": "DIESEL",
                "category_id": "507f1f77bcf86cd799439012",
                "category_name": "Fuel",
                "payee": "SHELL",
            },
        ]

    async def boom(*_a, **_k):
        raise AssertionError("warm cache must not call get_all_pages for keyed lookup")

    monkeypatch.setattr(dms, "get_all_pages", fake_pages)
    await dms.ensure_description_mapping_cache()
    monkeypatch.setattr(dms, "get_all_pages", boom)

    out = await dms.get_mappings_for_descriptions(["latra fee", "missing", "DIESEL"])
    assert set(out) == {"LATRA FEE", "DIESEL"}
    assert out["LATRA FEE"].category_name == "Parking"
    assert out["DIESEL"].payee == "SHELL"


@pytest.mark.asyncio
async def test_save_mapping_updates_warm_cache(monkeypatch) -> None:
    dms._maps_by_key = {}

    async def fake_request(method, path, **kwargs):
        return {
            "description_key": "NEW FEE",
            "description": "NEW FEE",
            "category_id": "507f1f77bcf86cd799439011",
            "category_name": "Parking",
            "payee": "CITY",
        }

    monkeypatch.setattr(dms.api_client, "request", fake_request)
    from bson import ObjectId

    saved = await dms.save_mapping(
        "new fee", ObjectId("507f1f77bcf86cd799439011"), "Parking", payee="CITY"
    )
    assert saved.category_name == "Parking"
    assert dms.lookup_mapping_sync("NEW FEE") is saved


@pytest.mark.asyncio
async def test_delete_mapping_invalidates_cache(monkeypatch) -> None:
    dms._maps_by_key = {"X": _mapping(key="X", item="Y")}

    async def fake_request(*_a, **_k):
        return None

    monkeypatch.setattr(dms.api_client, "request", fake_request)
    await dms.delete_mapping("507f1f77bcf86cd799439011")
    assert not dms.description_mapping_cache_ready()
