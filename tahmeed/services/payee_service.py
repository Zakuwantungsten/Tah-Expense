"""Payee registry — QuickBooks PAYEE names for mapping and autocomplete."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from tahmeed.services.api_client import api_client
from tahmeed.services.payee_iif import parse_payee_iif

_payee_list: Optional[List[str]] = None
_payee_set: Optional[set[str]] = None
_cache_lock = asyncio.Lock()


def invalidate_payee_cache() -> None:
    global _payee_list, _payee_set
    _payee_list = None
    _payee_set = None


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
    return await api_client.request("GET", "v1/payees", params=params)


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
        {
            "name": document["name"],
            "active": document.get("active", True),
            "ref_num": document.get("ref_num", ""),
            "currency": document.get("currency", ""),
        }
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
        {
            "name": document["name"],
            "active": document.get("active", True),
            "ref_num": document.get("ref_num", ""),
            "currency": document.get("currency", ""),
        }
        for document in documents
        if document.get("name")
    ]


async def _ensure_payee_cache() -> List[str]:
    global _payee_list, _payee_set
    if _payee_list is not None:
        return _payee_list
    async with _cache_lock:
        if _payee_list is not None:
            return _payee_list
        from tahmeed.services.catalog_service import fetch_catalog_snapshot

        snapshot = await fetch_catalog_snapshot()
        names = sorted(
            {
                _normalize_name(str(document.get("name") or ""))
                for document in snapshot.get("payees") or []
                if isinstance(document, dict) and document.get("name")
            }
        )
        _payee_set = set(names)
        _payee_list = names
        return _payee_list


def search_payees_sync(prefix: str, limit: int = 12) -> Optional[List[str]]:
    """Contains filter against the warm cache (no await). Returns None if cold."""
    if _payee_list is None:
        return None
    value = prefix.strip().upper()
    if not value:
        return []
    starts = [name for name in _payee_list if name.startswith(value)]
    contains = [
        name for name in _payee_list
        if value in name and name not in starts
    ]
    return (starts + contains)[:limit]


async def get_payee_names(active_only: bool = True) -> List[str]:
    if active_only:
        return list(await _ensure_payee_cache())
    docs = await _list()
    return sorted({_normalize_name(d["name"]) for d in docs if d.get("name")})


def _active(active_filter: str) -> bool | None:
    if active_filter == "active":
        return True
    if active_filter == "inactive":
        return False
    return None


async def list_payees(
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


async def count_payees(*, search: str = "", active_filter: str = "all") -> int:
    page = await _page(search, _active(active_filter), limit=1)
    return int(page["total"])


async def add_payee(name: str, *, currency: str = "TZS", ref_num: str = "") -> None:
    normalized = _normalize_name(name)
    await api_client.request(
        "PUT",
        f"v1/payees/{_path_name(normalized)}",
        json={
            "name": normalized,
            "active": True,
            "currency": _normalize_name(currency) or "TZS",
            "ref_num": (ref_num or "").strip(),
        },
    )
    invalidate_payee_cache()
    from tahmeed.services.catalog_service import mark_catalog_stale

    mark_catalog_stale()


async def remove_payee(name: str) -> None:
    await api_client.request("DELETE", f"v1/payees/{_path_name(name)}")
    invalidate_payee_cache()
    from tahmeed.services.catalog_service import mark_catalog_stale

    mark_catalog_stale()


async def set_payee_active(
    name: str,
    active: bool,
    *,
    currency: str | None = None,
) -> None:
    normalized = _normalize_name(name)
    payload: dict = {"name": normalized, "active": active}
    if currency is not None:
        payload["currency"] = _normalize_name(currency) or "TZS"
    await api_client.request(
        "PUT",
        f"v1/payees/{_path_name(normalized)}",
        json=payload,
    )
    invalidate_payee_cache()
    from tahmeed.services.catalog_service import mark_catalog_stale

    mark_catalog_stale()


async def import_payee_list(
    path: str | Path,
    *,
    replace_existing: bool = True,
) -> dict:
    """Parse an IIF Payee List and POST rows to the API."""
    parsed = parse_payee_iif(path)
    if not parsed:
        raise ValueError("No payees found in the Payee List file.")

    payload = await api_client.request(
        "POST",
        "v1/imports/payees",
        json={
            "rows": parsed,
            "replace_existing": bool(replace_existing),
            "source_filename": Path(path).name,
        },
    )
    invalidate_payee_cache()
    from tahmeed.services.catalog_service import mark_catalog_stale

    mark_catalog_stale()
    result = dict(payload or {})
    result.setdefault("imported", 0)
    result.setdefault("removed", 0)
    result.setdefault("updated", 0)
    result["source"] = str(path)
    return result
