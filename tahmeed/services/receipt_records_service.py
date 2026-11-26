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


async def delete_custom_folder(
    folder_id: str, *, cascade: bool = False
) -> dict[str, Any]:
    return await api_client.request(
        "DELETE",
        f"v1/receipts/records/folders/{folder_id}",
        params={"cascade": "true" if cascade else "false"},
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


async def discard_record_upload_batch(file_ids: list[str]) -> dict[str, Any]:
    """Roll back Records file ids after a failed / cancelled multi-upload."""
    ids = [str(x).strip() for x in file_ids if str(x or "").strip()]
    if not ids:
        return {"discarded": 0, "requested": 0}
    return await api_client.request(
        "POST",
        "v1/receipts/records/discard-batch",
        json={"file_ids": ids},
    )


async def move_record_files(
    file_ids: list[str],
    *,
    kind: str,
    ref_key: str = "",
    folder_id: str = "",
) -> dict[str, Any]:
    """Cut → Paste: move Records files into another destination."""
    ids = [str(x).strip() for x in file_ids if str(x or "").strip()]
    if not ids:
        return {"moved": 0, "skipped": 0, "requested": 0}
    return await api_client.request(
        "POST",
        "v1/receipts/records/move",
        json={
            "file_ids": ids,
            "kind": kind,
            "ref_key": ref_key,
            "folder_id": folder_id,
        },
    )


async def trash_record_files(file_ids: list[str]) -> dict[str, Any]:
    """Move Records / receipt files to Trash (soft delete)."""
    from tahmeed.services.receipt_storage_service import trash_receipt_files

    return await trash_receipt_files(file_ids)


async def upload_record_file(
    path: str,
    *,
    kind: str,
    ref_key: str = "",
    folder_id: str = "",
    note: str = "",
    file_date: str = "",
) -> dict[str, Any]:
    """Upload a single file into a Records destination."""
    import mimetypes
    from pathlib import Path

    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"File not found: {path}")
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    with file_path.open("rb") as handle:
        return await api_client.request(
            "POST",
            "v1/receipts/records/upload",
            data={
                "kind": kind,
                "ref_key": ref_key or "",
                "folder_id": folder_id or "",
                "note": note or "",
                "file_date": (file_date or "").strip(),
            },
            files=[("files", (file_path.name, handle, mime))],
        )


async def upload_record_files(
    paths: list[str],
    *,
    kind: str,
    ref_key: str = "",
    folder_id: str = "",
    note: str = "",
    file_date: str = "",
    on_progress: Any | None = None,
    should_cancel: Any | None = None,
) -> dict[str, Any]:
    """Sequential multi-file Records upload with progress + all-or-nothing rollback.

    ``on_progress(done, total, filename)`` is called before each file (done is
    0-based index of the file about to upload) and once at the end with
    ``done == total``. ``should_cancel()`` returning True aborts and discards.
    """
    from pathlib import Path

    readable = [str(p) for p in paths if Path(p).is_file()]
    if not readable:
        raise ValueError("No readable files selected")

    total = len(readable)
    saved_files: list[dict[str, Any]] = []
    saved_ids: list[str] = []
    destination: dict[str, Any] = {}
    filed = (file_date or "").strip()
    note_clean = note or ""
    note_file: dict[str, Any] | None = None

    committed = False
    try:
        for index, raw in enumerate(readable):
            if should_cancel is not None and should_cancel():
                raise InterruptedError("Upload cancelled")
            name = Path(raw).name
            if on_progress is not None:
                on_progress(index, total, name)
            # Attach optional note only on the last file so a mid-batch failure
            # never leaves a note without its companions.
            attach_note = note_clean if index == total - 1 else ""
            result = await upload_record_file(
                raw,
                kind=kind,
                ref_key=ref_key,
                folder_id=folder_id,
                note=attach_note,
                file_date=filed,
            )
            destination = dict(result.get("destination") or destination)
            filed = str(result.get("file_date") or filed)
            batch = list(result.get("files") or [])
            if result.get("note_file"):
                note_file = dict(result["note_file"])
            for meta in batch:
                fid = str(meta.get("id") or "").strip()
                if fid:
                    saved_ids.append(fid)
                saved_files.append(meta)
        if on_progress is not None:
            on_progress(total, total, "")
        committed = True
        return {
            "files": saved_files,
            "total": len(saved_files),
            "destination": destination,
            "file_date": filed,
            "note_file": note_file,
            "atomic": True,
        }
    finally:
        if not committed and saved_ids:
            try:
                await discard_record_upload_batch(saved_ids)
            except Exception:
                pass


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
