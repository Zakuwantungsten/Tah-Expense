"""Desktop chase / inbox / link helpers for receipt follow-up."""

from __future__ import annotations

from typing import Any

from tahmeed.services.api_client import api_client


async def list_chase() -> list[dict[str, Any]]:
    payload = await api_client.request("GET", "v1/receipts/chase")
    return list(payload.get("items") or [])


async def list_requests(
    *,
    preset: str = "open",
    ownership: str = "",
    overdue_hours: float = 48.0,
    reminder_threshold: int = 3,
) -> list[dict[str, Any]]:
    payload = await api_client.request(
        "GET",
        "v1/receipts/requests",
        params={
            "preset": preset,
            "ownership": ownership,
            "overdue_hours": overdue_hours,
            "reminder_threshold": reminder_threshold,
        },
    )
    return list(payload.get("items") or [])


async def get_compliance(
    *, overdue_hours: float = 48.0, reminder_threshold: int = 3
) -> dict[str, Any]:
    return await api_client.request(
        "GET",
        "v1/receipts/compliance",
        params={
            "overdue_hours": overdue_hours,
            "reminder_threshold": reminder_threshold,
        },
    )


async def list_inbox(
    *, status: str = "parked", unmatched_only: bool = False
) -> list[dict[str, Any]]:
    if status == "quarantine":
        payload = await api_client.request("GET", "v1/receipts/quarantine")
        return list(payload.get("items") or [])
    payload = await api_client.request(
        "GET",
        "v1/receipts/inbox",
        params={
            "status": status,
            "unmatched_only": "true" if unmatched_only else "false",
        },
    )
    return list(payload.get("items") or [])


async def list_receipt_library(
    *,
    date_from: str = "",
    date_to: str = "",
    item: str = "",
    payee: str = "",
    ownership: str = "",
    status: str = "all",
    limit: int = 200,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "status": status or "all",
        "limit": limit,
    }
    if date_from.strip():
        params["date_from"] = date_from.strip()
    if date_to.strip():
        params["date_to"] = date_to.strip()
    if item.strip():
        params["item"] = item.strip()
    if payee.strip():
        params["payee"] = payee.strip()
    if ownership.strip():
        params["ownership"] = ownership.strip()
    payload = await api_client.request("GET", "v1/receipts/library", params=params)
    return list(payload.get("items") or [])


async def fetch_receipt_file_meta(file_id: str) -> dict[str, Any]:
    """Viewer chrome for a receipt file (filename, mime, status, links)."""
    fid = (file_id or "").strip()
    if not fid:
        raise ValueError("File id is required")
    return await api_client.request("GET", f"v1/receipts/files/{fid}/meta")


async def fetch_receipt_file_bytes(file_id: str) -> tuple[bytes, dict[str, str]]:
    """Return raw bytes + useful headers for the shared desk viewer."""
    import httpx

    fid = (file_id or "").strip()
    if not fid:
        raise ValueError("File id is required")
    response = await api_client.request_response(
        "GET",
        f"v1/receipts/files/{fid}/open",
        timeout=httpx.Timeout(120.0, connect=5.0),
    )
    filename = ""
    disposition = response.headers.get("content-disposition") or ""
    if "filename=" in disposition:
        filename = disposition.split("filename=", 1)[-1].strip().strip('"')
    meta = {
        "content_type": response.headers.get("content-type") or "application/octet-stream",
        "filename": filename,
        "content_length": response.headers.get("content-length") or "",
    }
    return response.content, meta


async def approve_files(file_ids: list[str]) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/files/approve",
        json={"file_ids": file_ids},
    )


async def reject_files(file_ids: list[str], reason: str) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/files/reject",
        json={"file_ids": file_ids, "reason": reason},
    )


async def search_link_candidates(
    *,
    ownership: str = "",
    amount: float | None = None,
    needed_only: bool = False,
    today: bool = False,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if ownership.strip():
        params["ownership"] = ownership.strip()
    if amount is not None:
        params["amount"] = amount
    if needed_only:
        params["needed_only"] = "true"
    if today:
        params["today"] = "true"
    payload = await api_client.request(
        "GET", "v1/receipts/link-candidates", params=params or None
    )
    return list(payload.get("items") or [])


async def link_files(
    file_ids: list[str],
    transaction_ids: list[str],
    *,
    partial: bool = False,
    partial_note: str = "",
) -> dict[str, Any]:
    return await api_client.request(
        "POST",
        "v1/receipts/link",
        json={
            "file_ids": file_ids,
            "transaction_ids": transaction_ids,
            "partial": partial,
            "partial_note": partial_note,
        },
    )


async def upload_parked_files(paths: list[str]) -> dict[str, Any]:
    """Multipart early drop into parked inbox (no request/tx)."""
    import mimetypes
    from pathlib import Path

    files = []
    handles = []
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
        return await api_client.request(
            "POST",
            "v1/receipts/files/upload",
            files=files,
        )
    finally:
        for handle in handles:
            handle.close()


async def mark_reminded(
    *, ownership: str = "", request_ids: list[str] | None = None
) -> int:
    payload = await api_client.request(
        "POST",
        "v1/receipts/mark-reminded",
        json={"ownership": ownership, "request_ids": list(request_ids or [])},
    )
    return int(payload.get("updated") or 0)


def _pack_item_list(item: str = "", items: list[str] | None = None) -> list[str]:
    item_list = [p.strip() for p in (items or []) if p and p.strip()]
    if item.strip() and item.strip() not in item_list:
        for part in item.split(","):
            part = part.strip()
            if part and part not in item_list:
                item_list.append(part)
    return item_list


def _pack_response_meta(response: Any) -> dict[str, str]:
    meta = {
        "sha256": response.headers.get("x-pack-sha256") or "",
        "export_id": response.headers.get("x-export-id") or "",
        "rows": response.headers.get("x-pack-rows") or "",
        "with_file": response.headers.get("x-pack-with-file") or "",
        "without_file": response.headers.get("x-pack-without-file") or "",
        "files": response.headers.get("x-pack-files") or "",
        "missing": response.headers.get("x-pack-missing") or "",
        "filename": "",
    }
    disposition = response.headers.get("content-disposition") or ""
    if "filename=" in disposition:
        meta["filename"] = disposition.split("filename=", 1)[-1].strip().strip('"')
    return meta


async def download_evidence_pack(
    *,
    date_from: str,
    date_to: str,
    item: str = "",
    items: list[str] | None = None,
    payee: str = "",
    ownership: str = "",
    approved_only: bool = False,
) -> tuple[bytes, dict[str, str]]:
    """Return ZIP bytes and response header metadata (sha256, export id, counts)."""
    import httpx

    item_list = _pack_item_list(item, items)
    response = await api_client.request_response(
        "POST",
        "v1/receipts/evidence-pack",
        json={
            "date_from": date_from,
            "date_to": date_to,
            "item": item_list[0] if len(item_list) == 1 else "",
            "items": item_list,
            "payee": payee,
            "ownership": ownership,
            "approved_only": approved_only,
        },
        timeout=httpx.Timeout(180.0, connect=5.0),
    )
    return response.content, _pack_response_meta(response)


async def rematerialize_files(
    *, file_ids: list[str] | None = None, transaction_id: str = ""
) -> dict[str, Any]:
    """Refresh approved/ browse copies for file ids and/or one transaction."""
    return await api_client.request(
        "POST",
        "v1/receipts/files/rematerialize",
        json={
            "file_ids": list(file_ids or []),
            "transaction_id": (transaction_id or "").strip(),
        },
    )


async def download_consolidate_pack(
    *,
    date_from: str,
    date_to: str,
    item: str = "",
    items: list[str] | None = None,
    payee: str = "",
    ownership: str = "",
    approved_only: bool = True,
) -> tuple[bytes, dict[str, str]]:
    """Files-only consolidate ZIP for Library / inquiry downloads."""
    import httpx

    item_list = _pack_item_list(item, items)
    response = await api_client.request_response(
        "POST",
        "v1/receipts/files/consolidate",
        json={
            "date_from": date_from,
            "date_to": date_to,
            "item": item_list[0] if len(item_list) == 1 else "",
            "items": item_list,
            "payee": payee,
            "ownership": ownership,
            "approved_only": approved_only,
        },
        timeout=httpx.Timeout(180.0, connect=5.0),
    )
    return response.content, _pack_response_meta(response)


async def list_evidence_exports() -> list[dict[str, Any]]:
    payload = await api_client.request("GET", "v1/receipts/evidence-exports")
    return list(payload.get("items") or [])
