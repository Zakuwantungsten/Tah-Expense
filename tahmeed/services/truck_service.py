import asyncio
from typing import Dict, List

from tahmeed.services.api_client import api_client


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


async def search_trucks(prefix: str, limit: int = 10) -> List[str]:
    value = prefix.strip()
    if not value:
        return []
    documents = await _list("trucks", value, True)
    return [
        document["number"]
        for document in documents
        if document["number"].lower().startswith(value.lower())
    ][:limit]


async def search_fleet(prefix: str, limit: int = 10) -> List[str]:
    value = prefix.strip()
    if not value:
        return []
    trucks, trailers = await asyncio.gather(
        _list("trucks", value, True), _list("trailers", value, True)
    )
    seen: set[str] = set()
    result: List[str] = []
    for document in trucks + trailers:
        number = document["number"]
        if number.lower().startswith(value.lower()) and number not in seen:
            seen.add(number)
            result.append(number)
    return result[:limit]


async def get_fleet_numbers(active_only: bool = True) -> set:
    active = True if active_only else None
    trucks, trailers = await asyncio.gather(
        _list("trucks", active=active), _list("trailers", active=active)
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
    normalized = " ".join(number.upper().split())
    await api_client.request(
        "PUT",
        f"v1/{kind}/{normalized}",
        json={"number": normalized, "active": active},
    )


async def add_truck(number: str) -> None:
    await _put("trucks", number)


async def remove_truck(number: str) -> None:
    await api_client.request("DELETE", f"v1/trucks/{number.strip().upper()}")


async def set_truck_active(number: str, active: bool) -> None:
    await _put("trucks", number, active)


async def bulk_add_trucks(numbers: List[str]) -> int:
    normalized = {" ".join(number.upper().split()) for number in numbers if number.strip()}
    existing = {document["number"] for document in await _list("trucks")}
    await asyncio.gather(*(_put("trucks", number) for number in normalized))
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


async def set_trailer_active(number: str, active: bool) -> None:
    await _put("trailers", number, active)


async def bulk_add_trailers(numbers: List[str]) -> int:
    normalized = {" ".join(number.upper().split()) for number in numbers if number.strip()}
    existing = {document["number"] for document in await _list("trailers")}
    await asyncio.gather(*(_put("trailers", number) for number in normalized))
    return len(normalized - existing)
