"""Conversion helpers between JSON API documents and desktop dataclasses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId


def desktop_document(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    for field in ("_id", "category_id", "user_id"):
        value = result.get(field)
        if isinstance(value, str) and ObjectId.is_valid(value):
            result[field] = ObjectId(value)
    for field in (
        "created_at",
        "updated_at",
        "last_login",
        "last_used_at",
        "expires_at",
        "uploaded_at",
    ):
        value = result.get(field)
        if isinstance(value, str):
            try:
                result[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
    return result


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
