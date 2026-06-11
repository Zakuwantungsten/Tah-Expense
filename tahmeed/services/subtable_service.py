"""Sub-table (category sub-route) service — async Motor CRUD.

Sub-tables let the accountant carve a parent category (e.g. Mileage) into
named sub-views (e.g. routes "Dar to Congo") without any transaction schema
change — each sub-table is just a stored description filter.
"""

from typing import List, Optional
from bson import ObjectId

from tahmeed.db.connection import get_db
from tahmeed.models.sub_table import SubTable


async def get_subtables(parent_key: str, include_inactive: bool = False) -> List[SubTable]:
    db = get_db()
    query: dict = {"parent_key": parent_key}
    if not include_inactive:
        query["active"] = True
    cursor = db.category_subtables.find(query).sort([("sort_order", 1), ("name", 1)])
    docs = await cursor.to_list(length=None)
    return [SubTable.from_doc(d) for d in docs]


async def create_subtable(
    parent_key: str,
    parent_category: str,
    name: str,
    match: Optional[str] = None,
) -> SubTable:
    db = get_db()
    sub = SubTable(
        parent_key=parent_key,
        parent_category=parent_category,
        name=name,
        match=(match or name),
    )
    result = await db.category_subtables.insert_one(sub.to_doc())
    sub._id = result.inserted_id
    return sub


async def update_subtable(sub_id: ObjectId, **fields) -> None:
    db = get_db()
    await db.category_subtables.update_one({"_id": sub_id}, {"$set": fields})


async def archive_subtable(sub_id: ObjectId, active: bool = False) -> None:
    await update_subtable(sub_id, active=active)


async def delete_subtable(sub_id: ObjectId) -> None:
    db = get_db()
    await db.category_subtables.delete_one({"_id": sub_id})
