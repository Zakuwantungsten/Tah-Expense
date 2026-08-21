"""Resolve Map-Description-to-Item choices: create item if asked."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from tahmeed.models.category import Category
from tahmeed.services.api_client import ApiError
from tahmeed.services.category_service import (
    create_cashier_category,
    create_category,
    get_all_categories,
)

DEFAULT_ITEM_COLOR = "#4A90D9"


@dataclass
class MappingAssignment:
    """Intent captured by DescriptionMappingDialog before any API writes."""

    action: str = "assign"
    description: str = ""
    category: Optional[Category] = None
    create_new: bool = False
    new_item_name: str = ""
    new_item_fields: Optional[dict] = None

    @property
    def item_name(self) -> str:
        if self.create_new:
            return (self.new_item_name or "").strip()
        if self.category is not None:
            return (self.category.name or "").strip()
        return ""

    def placeholder_category(self) -> Optional[Category]:
        """Category suitable for later dialogs in the same batch (id may be None)."""
        if self.category is not None and not self.create_new:
            return self.category
        name = self.item_name
        if not name:
            return None
        return Category(name=name)


def remember_category(categories: List[Category], category: Optional[Category]) -> None:
    """Insert or replace a category in-place so the next mapping prompt can pick it."""
    if category is None or not (category.name or "").strip():
        return
    key = category.name.strip().lower()
    for index, existing in enumerate(categories):
        if (existing.name or "").strip().lower() == key:
            if category._id is not None or existing._id is None:
                categories[index] = category
            return
    categories.append(category)


def _find_by_name(categories: List[Category], name: str) -> Optional[Category]:
    key = (name or "").strip().lower()
    if not key:
        return None
    # Prefer a row that already has an id (created earlier in this batch).
    named = [
        cat
        for cat in categories
        if (cat.name or "").strip().lower() == key
    ]
    if not named:
        return None
    return next((cat for cat in named if cat._id is not None), named[0])


def _is_duplicate(exc: ApiError) -> bool:
    return exc.code == "duplicate" or exc.status_code == 409


async def create_item_for_mapping(name: str, fields: Optional[dict] = None) -> Category:
    """Create an item from the Add Item dialog fields; cashiers use the narrow endpoint."""
    trimmed = " ".join((name or "").strip().split())
    if not trimmed:
        raise ValueError("Item name is required.")
    data = dict(fields or {})
    color = (data.get("color") or DEFAULT_ITEM_COLOR)
    requires_receipt = bool(data.get("requires_receipt", False))
    requires_truck = bool(data.get("requires_truck", True))
    extra = {
        "description": data.get("description", "") or "",
        "icon": data.get("icon") or "mdi.tag-outline",
        "sidebar_name": data.get("sidebar_name", "") or "",
        "show_in_sidebar": bool(data.get("show_in_sidebar", False)),
        "show_in_cashier_sidebar": bool(data.get("show_in_cashier_sidebar", False)),
        "lock_description": bool(data.get("lock_description", False)),
    }
    try:
        return await create_category(
            trimmed, color, requires_receipt, requires_truck, **extra
        )
    except ApiError as exc:
        if exc.status_code == 403:
            return await create_cashier_category(
                trimmed, color, requires_receipt, requires_truck, **extra
            )
        if _is_duplicate(exc):
            match = _find_by_name(await get_all_categories(include_inactive=True), trimmed)
            if match is not None:
                return match
        raise


async def materialize_mapping_assignment(
    assignment: MappingAssignment,
    categories: Optional[List[Category]] = None,
) -> Category:
    """Create the item if needed; return a Category with id."""
    catalog = categories if categories is not None else []
    name = assignment.item_name
    if not name:
        raise ValueError("Please choose or create an item for this description.")

    category = assignment.category if not assignment.create_new else None
    if category is None or category._id is None:
        existing = _find_by_name(catalog, name)
        if existing is not None and existing._id is not None:
            category = existing
        else:
            category = await create_item_for_mapping(name, assignment.new_item_fields)
            remember_category(catalog, category)
    else:
        remember_category(catalog, category)

    return category


async def apply_assignment_to_descriptions(
    descriptions: List[str],
    assignment: MappingAssignment,
    categories: Optional[List[Category]] = None,
) -> tuple[Category, int]:
    """Create the item if needed, then point every description map at it.

    Returns ``(category, failed_count)``. Failed rows are skipped so a later
    mapping can still be updated.
    """
    from tahmeed.services.description_mapping_service import save_mapping

    category = await materialize_mapping_assignment(assignment, categories)
    if category is None or category._id is None:
        raise ValueError("Please choose or create an item for these descriptions.")
    failed = 0
    seen: set[str] = set()
    for raw in descriptions:
        text = " ".join((raw or "").strip().split())
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        try:
            await save_mapping(text, category._id, category.name)
        except Exception:
            failed += 1
    return category, failed
