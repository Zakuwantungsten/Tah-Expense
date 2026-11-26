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
    offset: int = 0,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "limit": max(1, min(int(limit), 2000)),
        "offset": max(0, int(offset or 0)),
    }
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


# Bounded parallel POSTs — enough to saturate typical WAN, low enough to
# avoid flooding the API / connection pool on large Covernote batches.
UPLOAD_CONCURRENCY = 6


def format_upload_mib(n_bytes: int) -> str:
    """Human size for upload progress (MiB, one decimal)."""
    mb = max(0, int(n_bytes)) / (1024 * 1024)
    if mb >= 100:
        return f"{mb:.0f} MB"
    return f"{mb:.1f} MB"


def format_records_upload_progress(
    files_done: int,
    files_total: int,
    bytes_done: int,
    bytes_total: int,
    filename: str = "",
    *,
    suffix: str = "",
) -> str:
    """Two-line progress: files + MB transferred (and remaining)."""
    done_b = max(0, int(bytes_done))
    total_b = max(0, int(bytes_total))
    left_b = max(0, total_b - done_b)
    mb_line = (
        f"{format_upload_mib(done_b)} of {format_upload_mib(total_b)}"
        f" · {format_upload_mib(left_b)} left"
    )
    if files_done >= files_total and files_total > 0:
        head = f"Finishing {files_done}/{files_total} files"
    elif filename:
        head = f"Files {files_done}/{files_total} · {filename}"
    else:
        head = f"Files {files_done}/{files_total}"
    if suffix:
        return f"{head}\n{mb_line}\n{suffix}"
    return f"{head}\n{mb_line}"


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
    concurrency: int = UPLOAD_CONCURRENCY,
) -> dict[str, Any]:
    """Concurrent multi-file Records upload with progress + all-or-nothing rollback.

    ``on_progress(files_done, files_total, bytes_done, bytes_total, filename)``
    is called at start (0/0), after each completed file, and once at the end
    with ``files_done == files_total``. ``should_cancel()`` returning True
    aborts in-flight work and discards any files already saved.
    """
    import asyncio
    from pathlib import Path

    readable = [str(p) for p in paths if Path(p).is_file()]
    if not readable:
        raise ValueError("No readable files selected")

    total = len(readable)
    sizes = [Path(p).stat().st_size for p in readable]
    bytes_total = int(sum(sizes))
    workers = max(1, min(int(concurrency or 1), total))

    saved_ids: list[str] = []
    destination: dict[str, Any] = {}
    filed = (file_date or "").strip()
    note_clean = (note or "").strip()
    note_file: dict[str, Any] | None = None
    # Preserve selection order in the returned file list.
    slot_files: list[list[dict[str, Any]]] = [[] for _ in range(total)]

    files_done = 0
    bytes_done = 0
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(workers)
    abort_exc: BaseException | None = None

    def _emit(filename: str = "") -> None:
        if on_progress is not None:
            on_progress(files_done, total, bytes_done, bytes_total, filename)

    def _ingest(index: int, result: dict[str, Any]) -> None:
        nonlocal destination, filed, note_file
        destination = dict(result.get("destination") or destination)
        filed = str(result.get("file_date") or filed)
        batch = list(result.get("files") or [])
        slot_files[index] = batch
        if result.get("note_file"):
            note_file = dict(result["note_file"])
        for meta in batch:
            fid = str(meta.get("id") or "").strip()
            if fid:
                saved_ids.append(fid)

    async def _one(index: int, raw: str, *, attach_note: str) -> None:
        nonlocal files_done, bytes_done
        async with sem:
            if abort_exc is not None:
                return
            if should_cancel is not None and should_cancel():
                raise InterruptedError("Upload cancelled")
            name = Path(raw).name
            result = await upload_record_file(
                raw,
                kind=kind,
                ref_key=ref_key,
                folder_id=folder_id,
                note=attach_note,
                file_date=filed,
            )
            async with lock:
                if abort_exc is not None:
                    # Sibling already failed/cancelled — still track ids for discard.
                    for meta in result.get("files") or []:
                        fid = str(meta.get("id") or "").strip()
                        if fid:
                            saved_ids.append(fid)
                    return
                _ingest(index, result)
                files_done += 1
                bytes_done += sizes[index]
                _emit(name)

    # Attach optional note only on the last path so a mid-batch failure
    # never leaves a note without its companions. Upload that path after
    # the others when a note is present; otherwise all paths run in parallel.
    if note_clean and total > 1:
        early = list(range(total - 1))
        late_index = total - 1
    else:
        early = list(range(total))
        late_index = -1

    committed = False
    _emit("")
    try:
        if early:
            tasks = [
                asyncio.create_task(
                    _one(
                        i,
                        readable[i],
                        attach_note=(
                            note_clean
                            if (late_index < 0 and i == total - 1)
                            else ""
                        ),
                    )
                )
                for i in early
            ]
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                # Always retrieve exceptions so they are not left unhandled.
                exc = task.exception()
                if exc is not None and abort_exc is None:
                    abort_exc = exc
            if abort_exc is not None:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                raise abort_exc

        if should_cancel is not None and should_cancel():
            raise InterruptedError("Upload cancelled")

        if late_index >= 0:
            await _one(late_index, readable[late_index], attach_note=note_clean)

        saved_files = [meta for batch in slot_files for meta in batch]
        if on_progress is not None:
            files_done = total
            bytes_done = bytes_total
            _emit("")
        committed = True
        return {
            "files": saved_files,
            "total": len(saved_files),
            "destination": destination,
            "file_date": filed,
            "note_file": note_file,
            "atomic": True,
            "bytes_total": bytes_total,
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


# Parallel connections for audit packs (IDM-style multi-source saturation).
PACK_DOWNLOAD_CONCURRENCY = 8
# List/download page size — when a page fills, we fetch the next (no hard ceiling).
PACK_BATCH_SIZE = 400
_ZIP_STORE_SUFFIXES = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
        ".zip",
        ".docx",
        ".xlsx",
        ".pptx",
        ".odt",
        ".ods",
        ".mp4",
        ".mov",
        ".avi",
    }
)


def records_zip_stem(*parts: str) -> str:
    """Safe ZIP basename fragment (folder / month labels)."""
    bad = '<>:"/\\|?*'
    chunks: list[str] = []
    for raw in parts:
        text = str(raw or "").strip()
        if not text:
            continue
        cleaned = "".join("_" if ch in bad else ch for ch in text)
        cleaned = cleaned.strip(" .")
        if cleaned:
            chunks.append(cleaned)
    return "_".join(chunks) if chunks else "tahmeed-records"


def _zip_compress_type_for(name: str) -> int:
    import zipfile
    from pathlib import Path

    suf = Path(name or "").suffix.lower()
    if suf in _ZIP_STORE_SUFFIXES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


async def download_records_pack(
    *,
    kind: str = "",
    ref_key: str = "",
    folder_id: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
) -> tuple[bytes, dict[str, str]]:
    """ZIP pack for the active Records filters (single server-built response)."""
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
        timeout=httpx.Timeout(600.0, connect=5.0),
    )
    return response.content, _pack_response_meta(response)


async def export_records_zip_fast(
    dest_zip: str,
    *,
    kind: str = "",
    ref_key: str = "",
    folder_id: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    on_progress: Any | None = None,
    should_cancel: Any | None = None,
    concurrency: int = PACK_DOWNLOAD_CONCURRENCY,
    batch_size: int = PACK_BATCH_SIZE,
) -> dict[str, Any]:
    """IDM-style pack with dynamic paging — no hard file-count ceiling.

    Lists in pages of ``batch_size``, downloads each page with N parallel
    connections, appends into one ZIP, then fetches the next page until empty.

    ``on_progress(files_done, files_total, bytes_done, bytes_total, phase, detail)``
    ``files_total`` grows as pages arrive (``0`` means still discovering).
    phases: ``listing`` | ``downloading`` | ``zipping`` | ``done``.
    """
    import asyncio
    import tempfile
    import zipfile
    from pathlib import Path

    import httpx

    def _emit(
        files_done: int,
        files_total: int,
        bytes_done: int,
        bytes_total: int,
        phase: str,
        detail: str = "",
    ) -> None:
        if on_progress is not None:
            on_progress(
                files_done, files_total, bytes_done, bytes_total, phase, detail
            )

    def _cancelled() -> bool:
        return bool(should_cancel is not None and should_cancel())

    def _arcname(row: dict[str, Any], used: set[str]) -> str:
        rec = row.get("record") if isinstance(row.get("record"), dict) else {}
        preferred = str((rec or {}).get("path") or "").replace("\\", "/").lstrip("/")
        if preferred.startswith("records/"):
            name = preferred
        else:
            name = str(row.get("original_filename") or row.get("id") or "file")
            name = Path(name).name or "file"
        base = name
        n = 1
        while name.lower() in used:
            stem = Path(base)
            name = f"{stem.with_suffix('')}__{n}{stem.suffix}"
            n += 1
        used.add(name.lower())
        return name

    page = max(50, min(int(batch_size or PACK_BATCH_SIZE), 2000))
    workers = max(1, int(concurrency or PACK_DOWNLOAD_CONCURRENCY))
    out = Path(dest_zip)
    if out.suffix.lower() != ".zip":
        out = out.with_suffix(".zip")
    out.parent.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    files_done = 0
    bytes_done = 0
    bytes_total = 0
    known_total = 0  # grows as pages are listed
    missing = 0
    copied = 0
    offset = 0
    batch_index = 0

    _emit(0, 0, 0, 0, "listing", "Listing files…")

    try:
        with zipfile.ZipFile(
            out, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            while True:
                if _cancelled():
                    raise InterruptedError("Download cancelled")

                _emit(
                    files_done,
                    known_total,
                    bytes_done,
                    max(bytes_total, bytes_done),
                    "listing",
                    f"Listing batch {batch_index + 1}…",
                )
                rows = await list_record_files(
                    kind=kind,
                    ref_key=ref_key,
                    folder_id=folder_id,
                    q=q,
                    date_from=date_from,
                    date_to=date_to,
                    limit=page,
                    offset=offset,
                )
                if not rows:
                    break

                batch_index += 1
                known_total += len(rows)
                batch_bytes = sum(max(0, int(r.get("size") or 0)) for r in rows)
                bytes_total += batch_bytes
                arcnames = [_arcname(row, used_names) for row in rows]
                _emit(
                    files_done,
                    known_total,
                    bytes_done,
                    max(bytes_total, bytes_done),
                    "downloading",
                    f"Batch {batch_index} · 0/{len(rows)}",
                )

                slots: list[tuple[Path | None, str]] = [(None, "")] * len(rows)
                lock = asyncio.Lock()
                sem = asyncio.Semaphore(min(workers, len(rows)))
                abort_exc: BaseException | None = None
                batch_done = 0

                async def _one(
                    index: int,
                    row: dict[str, Any],
                    tmp_dir: Path,
                    *,
                    _batch_len: int = len(rows),
                ) -> None:
                    nonlocal files_done, bytes_done, missing, batch_done
                    async with sem:
                        if abort_exc is not None:
                            return
                        if _cancelled():
                            raise InterruptedError("Download cancelled")
                        fid = str(row.get("id") or "").strip()
                        name = arcnames[index]
                        if not fid:
                            async with lock:
                                missing += 1
                                files_done += 1
                                batch_done += 1
                                _emit(
                                    files_done,
                                    known_total,
                                    bytes_done,
                                    max(bytes_total, bytes_done),
                                    "downloading",
                                    name,
                                )
                            return
                        dest = (
                            tmp_dir
                            / f"{batch_index:04d}_{index:05d}_{Path(name).name}"
                        )
                        try:
                            response = await api_client.request_response(
                                "GET",
                                f"v1/receipts/files/{fid}/open",
                                timeout=httpx.Timeout(180.0, connect=5.0),
                            )
                            dest.write_bytes(response.content)
                            got = len(response.content)
                        except Exception:
                            async with lock:
                                missing += 1
                                files_done += 1
                                batch_done += 1
                                _emit(
                                    files_done,
                                    known_total,
                                    bytes_done,
                                    max(bytes_total, bytes_done),
                                    "downloading",
                                    name,
                                )
                            return
                        async with lock:
                            if abort_exc is not None:
                                try:
                                    dest.unlink(missing_ok=True)
                                except OSError:
                                    pass
                                return
                            slots[index] = (dest, name)
                            files_done += 1
                            batch_done += 1
                            bytes_done += got
                            _emit(
                                files_done,
                                known_total,
                                bytes_done,
                                max(bytes_total, bytes_done),
                                "downloading",
                                f"Batch {batch_index} · {batch_done}/{_batch_len} · {name}",
                            )

                with tempfile.TemporaryDirectory(
                    prefix=f"tahmeed-pack-b{batch_index}-"
                ) as tmp:
                    tmp_path = Path(tmp)
                    tasks = [
                        asyncio.create_task(_one(i, row, tmp_path))
                        for i, row in enumerate(rows)
                    ]
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_EXCEPTION
                    )
                    for task in done:
                        exc = task.exception()
                        if exc is not None and abort_exc is None:
                            abort_exc = exc
                    if abort_exc is not None:
                        for task in pending:
                            task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        raise abort_exc

                    if _cancelled():
                        raise InterruptedError("Download cancelled")

                    _emit(
                        files_done,
                        known_total,
                        bytes_done,
                        max(bytes_total, bytes_done),
                        "zipping",
                        f"Writing batch {batch_index}…",
                    )
                    for path, arc in slots:
                        if path is None or not path.is_file():
                            continue
                        zf.write(
                            path,
                            arcname=arc,
                            compress_type=_zip_compress_type_for(arc),
                        )
                        copied += 1

                offset += len(rows)
                if len(rows) < page:
                    break

            if known_total == 0:
                raise ValueError("No files match this folder / month to pack")

            zf.writestr(
                "README.txt",
                (
                    "Tahmeed Paperwork → Records pack (parallel batched export)\n"
                    f"kind={kind or 'any'} ref_key={ref_key or '-'} "
                    f"folder_id={folder_id or '-'} q={q or '-'}\n"
                    f"files_copied={copied} missing={missing} batches={batch_index}\n"
                ),
            )
    except BaseException:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    _emit(
        copied,
        max(known_total, copied),
        bytes_done,
        max(bytes_total, bytes_done),
        "done",
        out.name,
    )
    return {
        "path": str(out),
        "file_count": copied,
        "missing": missing,
        "bytes": out.stat().st_size if out.is_file() else 0,
        "requested": known_total,
        "batches": batch_index,
    }
