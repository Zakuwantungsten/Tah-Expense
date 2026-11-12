"""Receipt disk path resolution, probe, and candidate roots.

The env/default ``ATTACHMENTS_DIRECTORY`` is the baseline. An admin or
accountant can store an absolute override in ``system_settings`` under
``attachments_directory``. Empty / missing override means use the env path.
"""

from __future__ import annotations

import os
import platform
import shutil
import string
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings

SETTING_KEY = "attachments_directory"
_PROBE_NAME = ".tahmeed-receipt-write-test"


def env_attachments_directory(settings: Settings) -> Path:
    return Path(settings.attachments_directory).expanduser()


def normalize_directory(raw: str | Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Path is required")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("Path must be absolute")
    # Reject odd relative fragments after expanduser on weird inputs.
    resolved_parts = path.as_posix().split("/")
    if ".." in resolved_parts:
        raise ValueError("Path must not contain '..'")
    return path


async def load_override_path(db: Any) -> Path | None:
    try:
        doc = await db.system_settings.find_one({"key": SETTING_KEY})
    except Exception:
        return None
    if not doc or "value" not in doc:
        return None
    value = doc.get("value")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return normalize_directory(text)


async def resolve_attachments_directory(settings: Settings, db: Any) -> Path:
    override = await load_override_path(db)
    if override is not None:
        return override
    return env_attachments_directory(settings)


def _fmt_bytes(n: int | None) -> str | None:
    if n is None:
        return None
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{n} B"


def probe_directory(path: Path, *, create: bool = False) -> dict[str, Any]:
    """Inspect a path and optionally create it, then try a tiny write."""
    try:
        path = normalize_directory(path)
    except ValueError as exc:
        return {
            "path": str(path),
            "exists": False,
            "is_dir": False,
            "writable": False,
            "created": False,
            "write_ok": False,
            "error": str(exc),
            "total_bytes": None,
            "free_bytes": None,
            "used_bytes": None,
            "total_human": None,
            "free_human": None,
            "drive": None,
            "mount_hint": None,
        }

    created = False
    error: str | None = None
    exists = path.exists()
    is_dir = path.is_dir() if exists else False

    if create and not exists:
        try:
            path.mkdir(parents=True, exist_ok=True)
            created = True
            exists = True
            is_dir = True
        except OSError as exc:
            error = f"Could not create directory: {exc}"

    writable = False
    write_ok = False
    if exists and is_dir:
        try:
            fd, raw = tempfile.mkstemp(prefix=_PROBE_NAME, dir=str(path))
            try:
                os.write(fd, b"ok")
            finally:
                os.close(fd)
                Path(raw).unlink(missing_ok=True)
            writable = True
            write_ok = True
        except OSError as exc:
            error = error or f"Not writable: {exc}"
    elif exists and not is_dir:
        error = error or "Path exists but is not a directory"
    elif not exists:
        error = error or "Path does not exist"

    total_bytes = free_bytes = used_bytes = None
    try:
        usage = shutil.disk_usage(path if exists else path.parent if path.parent.exists() else path)
        total_bytes = int(usage.total)
        free_bytes = int(usage.free)
        used_bytes = int(usage.used)
    except OSError:
        pass

    drive = None
    mount_hint = None
    if platform.system() == "Windows":
        drive = path.drive or None
        if drive:
            mount_hint = f"{drive}\\"
    else:
        mount_hint = _linux_mount_hint(path)

    return {
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "writable": writable,
        "created": created,
        "write_ok": write_ok,
        "error": error,
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "used_bytes": used_bytes,
        "total_human": _fmt_bytes(total_bytes),
        "free_human": _fmt_bytes(free_bytes),
        "drive": drive,
        "mount_hint": mount_hint,
    }


def _linux_mount_hint(path: Path) -> str | None:
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    best = ""
    target = path.as_posix()
    for line in mounts:
        parts = line.split()
        if len(parts) < 2:
            continue
        mount_point = parts[1]
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) > len(best):
                best = mount_point
    return best or None


def list_candidate_roots() -> list[dict[str, Any]]:
    """Drives / common mounts the UI can offer when choosing a receipt disk."""
    roots: list[Path] = []
    system = platform.system()
    if system == "Windows":
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:/")
            if root.exists():
                roots.append(root)
    else:
        roots.append(Path("/"))
        mnt = Path("/mnt")
        if mnt.is_dir():
            roots.extend(sorted(p for p in mnt.iterdir() if p.is_dir()))
        media = Path("/media")
        if media.is_dir():
            for user_dir in sorted(p for p in media.iterdir() if p.is_dir()):
                roots.append(user_dir)
                roots.extend(sorted(p for p in user_dir.iterdir() if p.is_dir()))

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        probe = probe_directory(root, create=False)
        label = root.drive if system == "Windows" and root.drive else root.name or str(root)
        out.append(
            {
                "path": str(root),
                "label": label,
                "exists": probe["exists"],
                "writable": probe["writable"],
                "free_bytes": probe["free_bytes"],
                "total_bytes": probe["total_bytes"],
                "free_human": probe["free_human"],
                "total_human": probe["total_human"],
                "mount_hint": probe["mount_hint"],
                "suggested_receipts_path": str(root / "tahmeed-receipts"),
            }
        )
    return out


async def pending_receipt_backup_count(db: Any) -> int:
    try:
        return int(
            await db.transactions.count_documents(
                {
                    "attachments": {
                        "$elemMatch": {
                            "$or": [
                                {"s3_key": {"$exists": False}},
                                {"s3_key": None},
                                {"s3_key": ""},
                            ]
                        }
                    }
                }
            )
        )
    except Exception:
        return 0


async def storage_status(settings: Settings, db: Any) -> dict[str, Any]:
    env_path = env_attachments_directory(settings)
    override = await load_override_path(db)
    effective = override if override is not None else env_path
    probe = probe_directory(effective, create=False)
    return {
        "effective_path": str(effective),
        "env_path": str(env_path),
        "override_path": str(override) if override is not None else None,
        "source": "override" if override is not None else "env",
        "host": platform.node(),
        "platform": system_label(),
        "receipt_s3_prefix": settings.receipt_s3_prefix,
        "s3_configured": bool(settings.backup_s3_bucket),
        "s3_bucket": settings.backup_s3_bucket or None,
        "pending_s3_rows": await pending_receipt_backup_count(db),
        "probe": probe,
        "candidates": list_candidate_roots(),
    }


def system_label() -> str:
    return f"{platform.system()} {platform.release()}".strip()
