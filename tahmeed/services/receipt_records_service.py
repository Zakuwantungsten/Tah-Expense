"""Desktop API client for Paperwork → Records (Phase 2+)."""

from __future__ import annotations

from typing import Any

from tahmeed.services.api_client import api_client


async def list_destinations() -> dict[str, Any]:
    return await api_client.request("GET", "v1/receipts/records/destinations")


async def list_custom_folders() -> list[dict[str, Any]]:
    payload = await api_client.request("GET", "v1/receipts/records/folders")
    return list((payload or {}).get("items") or [])


async def create_custom_folder(
    label: str, *, parent_id: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"label": label}
    if parent_id:
        body["parent_id"] = parent_id
    return await api_client.request(
        "POST",
        "v1/receipts/records/folders",
        json=body,
    )


async def rename_custom_folder(folder_id: str, label: str) -> dict[str, Any]:
    return await api_client.request(
        "PATCH",
        f"v1/receipts/records/folders/{folder_id}",
        json={"label": label},
    )


async def delete_custom_folder(folder_id: str) -> dict[str, Any]:
    return await api_client.request(
        "DELETE",
        f"v1/receipts/records/folders/{folder_id}",
    )


async def list_record_files(
    *,
    kind: str = "",
    ref_key: str = "",
    folder_id: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if kind:
        params["kind"] = kind
    if ref_key:
        params["ref_key"] = ref_key
    if folder_id:
        params["folder_id"] = folder_id
    if q:
        params["q"] = q
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    payload = await api_client.request(
        "GET", "v1/receipts/records/files", params=params
    )
    return list((payload or {}).get("items") or [])


async def upload_record_files(
    paths: list[str],
    *,
    kind: str,
    ref_key: str = "",
    folder_id: str = "",
    note: str = "",
    file_date: str = "",
) -> dict[str, Any]:
    """Multipart bulk upload into a Records destination.

    ``file_date`` is YYYY-MM-DD for the browse YYYY/MM folder (backlog). Empty = today.
    """
    import mimetypes
    from pathlib import Path

    files: list[tuple[str, tuple[str, Any, str]]] = []
    handles: list[Any] = []
    try:
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                continue
            handle = path.open("rb")
            handles.append(handle)
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            files.append(("files", (path.name, handle, mime)))
        if not files:
            raise ValueError("No readable files selected")
        data = {
            "kind": kind,
            "ref_key": ref_key or "",
            "folder_id": folder_id or "",
            "note": note or "",
            "file_date": (file_date or "").strip(),
        }
        return await api_client.request(
            "POST",
            "v1/receipts/records/upload",
            data=data,
            files=files,
        )
    finally:
        for handle in handles:
            handle.close()


def _pack_response_meta(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", {}) or {}
    disposition = str(headers.get("content-disposition") or "")
    filename = ""
    if "filename=" in disposition:
        filename = disposition.split("filename=", 1)[1].strip().strip('"')
    return {
        "filename": filename or "tahmeed-records.zip",
        "sha256": str(headers.get("x-pack-sha256") or ""),
        "file_count": str(headers.get("x-pack-files") or ""),
        "missing": str(headers.get("x-pack-missing") or ""),
    }


async def download_records_pack(
    *,
    kind: str = "",
    ref_key: str = "",
    folder_id: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
) -> tuple[bytes, dict[str, str]]:
    """ZIP pack for the active Records filters."""
    import httpx

    body: dict[str, Any] = {
        "kind": kind or "",
        "ref_key": ref_key or "",
        "folder_id": folder_id or "",
        "q": q or "",
    }
    if date_from:
        body["date_from"] = date_from
    if date_to:
        body["date_to"] = date_to
    response = await api_client.request_response(
        "POST",
        "v1/receipts/records/pack",
        json=body,
        timeout=httpx.Timeout(180.0, connect=5.0),
    )
    return response.content, _pack_response_meta(response)
