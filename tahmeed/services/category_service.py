import re
from typing import List
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.category import Category


def item_key(name: str) -> str:
    """Slugify an item name into a stable sidebar / sub-table parent key.

    Used everywhere a key is needed for an item (sidebar nav, sub-tables, stack
    routing) so the accountant sidebar, Manage Items, and the dashboards all
    agree on the same key for a given item name.
    """
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


async def get_all_categories(include_inactive: bool = False) -> List[Category]:
    db = get_db()
    query = {} if include_inactive else {"active": True}
    cursor = db.categories.find(query).sort("name", 1)
    docs = await cursor.to_list(length=None)
    return [Category.from_doc(d) for d in docs]


async def get_sidebar_categories() -> List[Category]:
    """Active items flagged to appear as their own sidebar tab, ordered."""
    db = get_db()
    cursor = db.categories.find(
        {"active": True, "show_in_sidebar": True}
    ).sort([("sort_order", 1), ("name", 1)])
    docs = await cursor.to_list(length=None)
    return [Category.from_doc(d) for d in docs]


async def create_category(
    name: str, color: str, requires_receipt: bool, requires_truck: bool,
    description: str = "",
    icon: str = "mdi.tag-outline",
    show_in_sidebar: bool = False,
    sort_order: int = 0,
) -> Category:
    db = get_db()
    cat = Category(
        name=name,
        description=description,
        color=color,
        icon=icon,
        show_in_sidebar=show_in_sidebar,
        sort_order=sort_order,
        requires_receipt=requires_receipt,
        requires_truck=requires_truck,
    )
    result = await db.categories.insert_one(cat.to_doc())
    cat._id = result.inserted_id
    return cat


async def update_category(cat_id: ObjectId, **fields) -> None:
    db = get_db()
    await db.categories.update_one({"_id": cat_id}, {"$set": fields})


async def toggle_category(cat_id: ObjectId, active: bool) -> None:
    await update_category(cat_id, active=active)


async def delete_category(cat_id: ObjectId) -> None:
    db = get_db()
    await db.categories.delete_one({"_id": cat_id})
