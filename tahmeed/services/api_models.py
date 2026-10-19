"""Conversion helpers between JSON API documents and desktop dataclasses."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from bson import ObjectId


_OBJECT_ID_FIELDS = (
    "_id",
    "category_id",
    "user_id",
    "cashier_id",
    "verified_by",
    "last_edited_by",
    "deletion_requested_by",
    "trashed_by",
    "original_transaction_id",
    "duplicate_of_id",
    "imported_by",
)

_DATETIME_FIELDS = (
    "created_at",
    "updated_at",
    "last_login",
    "last_used_at",
    "expires_at",
    "uploaded_at",
    "date",
    "reported_date",
    "verified_at",
    "last_edited_at",
    "deletion_requested_at",
    "trashed_at",
    "import_primary_date",
    "primary_date",
    "min_date",
    "max_date",
    "t1_date",
    "import_date",
    "transaction_date",
    "expense_date",
    "skipped_at",
    "payment_date",
    "sales_date",
    "min_toll_date",
    "max_toll_date",
)


def desktop_document(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    for field in _OBJECT_ID_FIELDS:
        value = result.get(field)
        if isinstance(value, str) and ObjectId.is_valid(value):
            result[field] = ObjectId(value)
    for field in _DATETIME_FIELDS:
        value = result.get(field)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                result[field] = parsed.replace(tzinfo=None)
            except ValueError:
                pass
    return result


def jsonable_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable_value(item) for item in value]
    return value


def jsonable_document(document: dict[str, Any]) -> dict[str, Any]:
    return {str(key): jsonable_value(value) for key, value in document.items()}


async def get_all_pages(path: str, *, params: dict[str, Any] | None = None) -> list[dict]:
    from tahmeed.services.api_client import api_client

    offset = 0
    items: list[dict] = []
    while True:
        query = dict(params or {})
        query.update(limit=500, offset=offset)
        page = await api_client.request("GET", path, params=query)
        batch = page["items"]
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= page["total"]:
            return items
