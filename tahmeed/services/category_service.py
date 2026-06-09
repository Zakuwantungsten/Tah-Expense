from typing import List
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.category import Category


async def get_all_categories(include_inactive: bool = False) -> List[Category]:
    db = get_db()
    query = {} if include_inactive else {"active": True}
    cursor = db.categories.find(query).sort("name", 1)
    docs = await cursor.to_list(length=None)
    return [Category.from_doc(d) for d in docs]


async def create_category(
    name: str, color: str, requires_receipt: bool, requires_truck: bool
) -> Category:
    db = get_db()
    cat = Category(
        name=name,
        color=color,
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
