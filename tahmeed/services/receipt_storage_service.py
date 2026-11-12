"""Receipt disk status and path configuration via the API."""

from __future__ import annotations

from typing import Any

from tahmeed.services.api_client import api_client


async def get_storage_status() -> dict[str, Any]:
    return await api_client.request("GET", "v1/receipts/storage")


async def probe_storage(path: str, *, create: bool = False) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/storage/probe",
        json={"path": path, "create": create},
    )


async def set_storage_path(path: str, *, create: bool = False) -> dict[str, Any]:
    return await api_client.request(
        "PUT",
        "v1/receipts/storage",
        json={"path": path, "create": create},
    )


async def clear_storage_override() -> dict[str, Any]:
    return await api_client.request(
        "PUT",
        "v1/receipts/storage",
        json={"path": "/", "clear_override": True},
    )


async def list_storage_tree(prefix: str = "") -> dict[str, Any]:
    return await api_client.request(
        "GET",
        "v1/receipts/storage/tree",
        params={"prefix": prefix or ""},
    )


async def list_storage_objects(
    *, prefix: str = "", q: str = "", limit: int = 200
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if prefix:
        params["prefix"] = prefix
    if q.strip():
        params["q"] = q.strip()
    return await api_client.request(
        "GET",
        "v1/receipts/storage/objects",
        params=params,
    )
