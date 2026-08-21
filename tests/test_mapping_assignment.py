"""Create-item resolution for Map Description to Item."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from tahmeed.models.category import Category
from tahmeed.services.api_client import ApiError
from tahmeed.services.mapping_assignment_service import (
    MappingAssignment,
    materialize_mapping_assignment,
    remember_category,
)


@pytest.mark.asyncio
async def test_materialize_reuses_existing_item(monkeypatch) -> None:
    created = AsyncMock()
    monkeypatch.setattr(
        "tahmeed.services.mapping_assignment_service.create_category", created
    )
    cat = Category(_id=ObjectId(), name="Parking")
    result = await materialize_mapping_assignment(
        MappingAssignment(
            description="PARKING KURASINI",
            category=cat,
        ),
        [cat],
    )
    assert result._id == cat._id
    created.assert_not_called()


@pytest.mark.asyncio
async def test_materialize_creates_new_item(monkeypatch) -> None:
    new_cat = Category(_id=ObjectId(), name="Parking")

    async def create_item(name: str, fields=None) -> Category:
        assert name == "Parking"
        assert fields["color"] == "#112233"
        assert fields["requires_receipt"] is True
        return new_cat

    monkeypatch.setattr(
        "tahmeed.services.mapping_assignment_service.create_item_for_mapping",
        create_item,
    )
    catalog: list[Category] = []
    result = await materialize_mapping_assignment(
        MappingAssignment(
            description="PARKING KURASINI",
            create_new=True,
            new_item_name="Parking",
            new_item_fields={
                "name": "Parking",
                "color": "#112233",
                "requires_receipt": True,
                "requires_truck": False,
            },
        ),
        catalog,
    )
    assert result._id == new_cat._id
    assert catalog[0]._id == new_cat._id


@pytest.mark.asyncio
async def test_create_item_falls_back_to_cashier_endpoint(monkeypatch) -> None:
    from tahmeed.services import mapping_assignment_service as mas

    new_cat = Category(_id=ObjectId(), name="Parking")

    async def manager_create(*_a, **_k):
        raise ApiError("forbidden", status_code=403, code="forbidden")

    async def cashier_create(name, *_a, **_k):
        assert name == "Parking"
        return new_cat

    monkeypatch.setattr(mas, "create_category", manager_create)
    monkeypatch.setattr(mas, "create_cashier_category", cashier_create)
    result = await mas.create_item_for_mapping("Parking")
    assert result._id == new_cat._id


def test_remember_category_replaces_placeholder() -> None:
    placeholder = Category(name="Parking")
    created = Category(_id=ObjectId(), name="Parking")
    cats = [placeholder]
    remember_category(cats, created)
    assert len(cats) == 1
    assert cats[0]._id == created._id


@pytest.mark.asyncio
async def test_apply_assignment_reuses_item_and_saves_each_description(monkeypatch) -> None:
    from tahmeed.services import mapping_assignment_service as mas

    cat = Category(_id=ObjectId(), name="Parking")
    saved: list[tuple] = []

    async def fake_save(description, category_id, category_name):
        saved.append((description, category_id, category_name))

    monkeypatch.setattr(mas, "create_item_for_mapping", AsyncMock())
    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.save_mapping",
        fake_save,
    )
    chosen, failed = await mas.apply_assignment_to_descriptions(
        ["TRIANGLE", "PARKING KURASINI", " triangle "],
        MappingAssignment(category=cat),
        [cat],
    )
    assert chosen._id == cat._id
    assert failed == 0
    assert saved == [
        ("TRIANGLE", cat._id, "Parking"),
        ("PARKING KURASINI", cat._id, "Parking"),
    ]
    mas.create_item_for_mapping.assert_not_called()


@pytest.mark.asyncio
async def test_apply_assignment_creates_item_once_then_maps_all(monkeypatch) -> None:
    from tahmeed.services import mapping_assignment_service as mas

    new_cat = Category(_id=ObjectId(), name="Council Fees")
    created = AsyncMock(return_value=new_cat)
    saved: list[str] = []

    async def fake_save(description, category_id, category_name):
        saved.append(description)
        assert category_id == new_cat._id
        assert category_name == "Council Fees"

    monkeypatch.setattr(mas, "create_item_for_mapping", created)
    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.save_mapping",
        fake_save,
    )
    catalog: list[Category] = []
    chosen, failed = await mas.apply_assignment_to_descriptions(
        ["LATRA", "COUNCIL"],
        MappingAssignment(
            create_new=True,
            new_item_name="Council Fees",
            new_item_fields={"color": "#112233"},
        ),
        catalog,
    )
    assert chosen._id == new_cat._id
    assert failed == 0
    assert saved == ["LATRA", "COUNCIL"]
    created.assert_awaited_once()
    assert catalog[0]._id == new_cat._id
