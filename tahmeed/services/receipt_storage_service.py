"""Receipt disk status and path configuration via the API."""

from __future__ import annotations

from typing import Any

from tahmeed.services.api_client import api_client


async def get_storage_status() -> dict[str, Any]:
    return await api_client.request("GET", "v1/receipts/storage")


async def get_storage_health() -> dict[str, Any]:
    """Light health probe for sidebar badges and degraded-mode banners."""
    return await api_client.request("GET", "v1/receipts/storage/health")


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


async def create_storage_folder(*, prefix: str, name: str) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/storage/folders",
        json={"prefix": prefix or "", "name": name},
    )


async def delete_storage_folder(*, prefix: str, cascade: bool = False) -> dict[str, Any]:
    return await api_client.request(
        "DELETE",
        "v1/receipts/storage/folders",
        params={"prefix": prefix, "cascade": "true" if cascade else "false"},
    )


async def rename_storage_folder(*, prefix: str, name: str) -> dict[str, Any]:
    return await api_client.request(
        "PATCH",
        "v1/receipts/storage/folders",
        json={"prefix": prefix, "name": name},
    )


async def list_receipt_trash(*, limit: int = 200, q: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if q.strip():
        params["q"] = q.strip()
    return await api_client.request(
        "GET", "v1/receipts/trash", params=params
    )


async def restore_all_receipt_trash() -> dict[str, Any]:
    return await api_client.request("POST", "v1/receipts/trash/restore-all")


async def empty_receipt_trash() -> dict[str, Any]:
    return await api_client.request("POST", "v1/receipts/trash/empty")


async def trash_receipt_files(file_ids: list[str]) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/trash",
        json={"file_ids": list(file_ids)},
    )


async def restore_receipt_trash(file_ids: list[str]) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/trash/restore",
        json={"file_ids": list(file_ids)},
    )


async def purge_receipt_trash(file_ids: list[str]) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/trash/purge",
        json={"file_ids": list(file_ids)},
    )
