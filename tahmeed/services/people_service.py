"""People registry — names used for Ownership / APR BY autocomplete."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional
from urllib.parse import quote

from tahmeed.services.api_client import api_client

_people_list: Optional[List[str]] = None
_people_set: Optional[set[str]] = None
_cache_lock = asyncio.Lock()


def invalidate_people_cache() -> None:
    global _people_list, _people_set
    _people_list = None
    _people_set = None


def _normalize_name(name: str) -> str:
    return " ".join((name or "").upper().split())


def _path_name(name: str) -> str:
    return quote(_normalize_name(name), safe="")


async def _page(
    search: str = "",
    active: bool | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    params: dict = {"search": search}
    if active is not None:
        params["active"] = str(active).lower()
    params.update(limit=max(1, min(limit, 500)), offset=max(0, offset))
    return await api_client.request("GET", "v1/people", params=params)


async def _list(search: str = "", active: bool | None = None) -> List[Dict]:
    documents: list[dict] = []
    offset = 0
    while True:
        page = await _page(search, active, limit=500, offset=offset)
        batch = page["items"]
        documents.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page["total"]):
            break
    return [
        {"name": document["name"], "active": document.get("active", True)}
        for document in documents
        if document.get("name")
    ]


async def _window(
    search: str,
    active: bool | None,
    *,
    limit: int,
    offset: int,
) -> List[Dict]:
    remaining = max(1, limit)
    current_offset = max(0, offset)
    documents: list[dict] = []
    while remaining:
        page = await _page(
            search,
            active,
            limit=min(remaining, 500),
            offset=current_offset,
        )
        batch = page["items"]
        documents.extend(batch)
        current_offset += len(batch)
        remaining -= len(batch)
        if not batch or current_offset >= int(page["total"]):
            break
    return [
        {"name": document["name"], "active": document.get("active", True)}
        for document in documents
        if document.get("name")
    ]


async def _ensure_people_cache() -> List[str]:
    global _people_list, _people_set
    if _people_list is not None:
        return _people_list
    async with _cache_lock:
        if _people_list is not None:
            return _people_list
        docs = await _list(active=True)
        names = sorted({_normalize_name(d["name"]) for d in docs if d.get("name")})
        _people_set = set(names)
        _people_list = names
        return _people_list


def search_people_sync(prefix: str, limit: int = 10) -> Optional[List[str]]:
    """Prefix filter against the warm cache (no await). Returns None if cold."""
    if _people_list is None:
        return None
    value = prefix.strip().upper()
    if not value:
        return []
    return [name for name in _people_list if name.startswith(value)][:limit]


async def get_people_names(active_only: bool = True) -> List[str]:
    if active_only:
        return list(await _ensure_people_cache())
    docs = await _list()
    return sorted({_normalize_name(d["name"]) for d in docs if d.get("name")})


def _active(active_filter: str) -> bool | None:
    if active_filter == "active":
        return True
    if active_filter == "inactive":
        return False
    return None


async def list_people(
    *,
    search: str = "",
    active_filter: str = "all",
    limit: int = 100,
    skip: int = 0,
) -> List[Dict]:
    return await _window(
        search,
        _active(active_filter),
        limit=limit,
        offset=skip,
    )


async def count_people(*, search: str = "", active_filter: str = "all") -> int:
    page = await _page(search, _active(active_filter), limit=1)
    return int(page["total"])


async def add_person(name: str) -> None:
    normalized = _normalize_name(name)
    await api_client.request(
        "PUT",
        f"v1/people/{_path_name(normalized)}",
        json={"name": normalized, "active": True},
    )
    invalidate_people_cache()


async def remove_person(name: str) -> None:
    await api_client.request("DELETE", f"v1/people/{_path_name(name)}")
    invalidate_people_cache()


async def set_person_active(name: str, active: bool) -> None:
    normalized = _normalize_name(name)
    await api_client.request(
        "PUT",
        f"v1/people/{_path_name(normalized)}",
        json={"name": normalized, "active": active},
    )
    invalidate_people_cache()
