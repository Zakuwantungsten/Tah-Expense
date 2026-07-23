import asyncio
from typing import Dict, List, Optional

from tahmeed.services.api_client import api_client
from tahmeed.services.truck_format import normalize_truck_number


# In-memory fleet cache for instant autocomplete (trucks + trailers).
_fleet_list: Optional[List[str]] = None
_fleet_set: Optional[set[str]] = None
_trucks_list: Optional[List[str]] = None
_cache_lock = asyncio.Lock()


def invalidate_fleet_cache() -> None:
    global _fleet_list, _fleet_set, _trucks_list
    _fleet_list = None
    _fleet_set = None
    _trucks_list = None


async def _page(
    kind: str,
    search: str = "",
    active: bool | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    params = {"search": search}
    if active is not None:
        params["active"] = str(active).lower()
    params.update(limit=max(1, min(limit, 500)), offset=max(0, offset))
    return await api_client.request("GET", f"v1/{kind}", params=params)


async def _list(kind: str, search: str = "", active: bool | None = None) -> List[Dict]:
    documents: list[dict] = []
    offset = 0
    while True:
        page = await _page(kind, search, active, limit=500, offset=offset)
        batch = page["items"]
        documents.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page["total"]):
            break
    return [
        {"number": document["number"], "active": document.get("active", True)}
        for document in documents
    ]


async def _window(
    kind: str,
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
            kind,
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
        {"number": document["number"], "active": document.get("active", True)}
        for document in documents
    ]


def _normalize_for_write(number: str) -> str:
    result = normalize_truck_number(number)
    if result.status in ("ok", "normalized"):
        return result.value
    return " ".join(number.upper().split())


async def _ensure_fleet_cache() -> List[str]:
    global _fleet_list, _fleet_set, _trucks_list
    if _fleet_list is not None:
        return _fleet_list
    async with _cache_lock:
        if _fleet_list is not None:
            return _fleet_list
        trucks, trailers = await asyncio.gather(
            _list("trucks", active=True),
            _list("trailers", active=True),
        )
        truck_numbers = [
            document["number"].upper()
            for document in trucks
            if document.get("number")
        ]
        trailer_numbers = [
            document["number"].upper()
            for document in trailers
            if document.get("number")
        ]
        seen: set[str] = set()
        combined: list[str] = []
        for number in truck_numbers + trailer_numbers:
            if number not in seen:
                seen.add(number)
                combined.append(number)
        combined.sort()
        _trucks_list = sorted(set(truck_numbers))
        _fleet_set = seen
        _fleet_list = combined
        return _fleet_list


def _prefix_filter(numbers: List[str], prefix: str, limit: int) -> List[str]:
    value = prefix.strip().upper()
    if not value:
        return []
    return [number for number in numbers if number.startswith(value)][:limit]


def search_fleet_sync(prefix: str, limit: int = 10) -> Optional[List[str]]:
    """Prefix filter against the warm in-memory cache (no await).

    Returns ``None`` when the cache has not been loaded yet. Safe to call from
    Qt widgets during modal dialogs nested inside an async task (Python 3.14
    rejects nested ``ensure_future`` in that case).
    """
    if _fleet_list is None:
        return None
    return _prefix_filter(_fleet_list, prefix, limit)


async def search_trucks(prefix: str, limit: int = 10) -> List[str]:
    """Prefix search over active trucks only (local cache)."""
    await _ensure_fleet_cache()
    assert _trucks_list is not None
    return _prefix_filter(_trucks_list, prefix, limit)


async def search_fleet(prefix: str, limit: int = 10) -> List[str]:
    """Prefix search over active trucks + trailers (local cache — instant)."""
    numbers = await _ensure_fleet_cache()
    return _prefix_filter(numbers, prefix, limit)


async def get_fleet_numbers(active_only: bool = True) -> set:
    if active_only:
        await _ensure_fleet_cache()
        assert _fleet_set is not None
        return set(_fleet_set)
    trucks, trailers = await asyncio.gather(
        _list("trucks"), _list("trailers")
    )
    return {
        document["number"].upper()
        for document in trucks + trailers
        if document.get("number")
    }


def _active(active_filter: str) -> bool | None:
    if active_filter == "active":
        return True
    if active_filter == "inactive":
        return False
    return None


async def get_all_trucks(search: str = "", active_only: bool = False) -> List[Dict]:
    return await _list("trucks", search, True if active_only else None)


async def list_trucks(
    *,
    search: str = "",
    active_filter: str = "all",
    limit: int = 100,
    skip: int = 0,
) -> List[Dict]:
    return await _window(
        "trucks",
        search,
        _active(active_filter),
        limit=limit,
        offset=skip,
    )


async def count_trucks(*, search: str = "", active_filter: str = "all") -> int:
    page = await _page("trucks", search, _active(active_filter), limit=1)
    return int(page["total"])


async def _put(kind: str, number: str, active: bool = True) -> None:
    normalized = _normalize_for_write(number)
    await api_client.request(
        "PUT",
        f"v1/{kind}/{normalized}",
        json={"number": normalized, "active": active},
    )
    invalidate_fleet_cache()


async def add_truck(number: str) -> None:
    await _put("trucks", number)


async def remove_truck(number: str) -> None:
    await api_client.request("DELETE", f"v1/trucks/{number.strip().upper()}")
    invalidate_fleet_cache()


async def set_truck_active(number: str, active: bool) -> None:
    await _put("trucks", number, active)


async def bulk_add_trucks(numbers: List[str]) -> int:
    normalized = {_normalize_for_write(number) for number in numbers if number.strip()}
    existing = {document["number"] for document in await _list("trucks")}
    await asyncio.gather(*(_put("trucks", number) for number in normalized))
    invalidate_fleet_cache()
    return len(normalized - existing)


async def get_all_trailers(search: str = "", active_only: bool = False) -> List[Dict]:
    return await _list("trailers", search, True if active_only else None)


async def list_trailers(
    *,
    search: str = "",
    active_filter: str = "all",
    limit: int = 100,
    skip: int = 0,
) -> List[Dict]:
    return await _window(
        "trailers",
        search,
        _active(active_filter),
        limit=limit,
        offset=skip,
    )


async def count_trailers(*, search: str = "", active_filter: str = "all") -> int:
    page = await _page("trailers", search, _active(active_filter), limit=1)
    return int(page["total"])


async def add_trailer(number: str) -> None:
    await _put("trailers", number)


async def remove_trailer(number: str) -> None:
    await api_client.request("DELETE", f"v1/trailers/{number.strip().upper()}")
    invalidate_fleet_cache()


async def set_trailer_active(number: str, active: bool) -> None:
    await _put("trailers", number, active)


async def bulk_add_trailers(numbers: List[str]) -> int:
    normalized = {_normalize_for_write(number) for number in numbers if number.strip()}
    existing = {document["number"] for document in await _list("trailers")}
    await asyncio.gather(*(_put("trailers", number) for number in normalized))
    invalidate_fleet_cache()
    return len(normalized - existing)
