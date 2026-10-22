"""Register catalog: one snapshot, reused until a catalog write or a refresh."""

from __future__ import annotations

import asyncio
from typing import Any

from tahmeed.models.category import Category
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document

_body: dict[str, Any] | None = None
_etag: str | None = None
_stale = False
_lock = asyncio.Lock()


def mark_catalog_stale() -> None:
    global _stale
    _stale = True


def catalog_is_stale() -> bool:
    return _stale


def cached_catalog() -> dict[str, Any] | None:
    return _body


async def fetch_catalog_snapshot(*, refresh: bool = False) -> dict[str, Any]:
    """Return the catalog body. ``refresh`` sends one request (200 or 304)."""
    global _body, _etag, _stale
    if _body is not None and not refresh and not _stale:
        return _body
    async with _lock:
        if _body is not None and not refresh and not _stale:
            return _body
        headers: dict[str, str] = {}
        if _etag and not _stale:
            headers["If-None-Match"] = _etag
        response = await api_client.request_response(
            "GET", "v1/catalog/snapshot", headers=headers
        )
        tag = response.headers.get("etag")
        if tag:
            _etag = tag
        if response.status_code == 304:
            _stale = False
            if _body is not None:
                return _body
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict):
            payload = {}
        _body = payload
        _stale = False
        return _body


def categories_from_snapshot(snapshot: dict[str, Any]) -> list[Category]:
    categories: list[Category] = []
    for document in snapshot.get("categories") or []:
        if isinstance(document, dict) and document.get("name"):
            categories.append(Category.from_doc(desktop_document(document)))
    return categories


def setting_from_snapshot(snapshot: dict[str, Any], key: str, default: Any = None) -> Any:
    settings = snapshot.get("settings")
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    if key == "allowed_truck_labels" and "allowed_truck_labels" in snapshot:
        return snapshot["allowed_truck_labels"]
    return default
