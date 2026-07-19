from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import socket
import shutil
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import boto3
from botocore.config import Config
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..config import Settings, get_settings

UTC = timezone.utc

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is exercised instead
    fcntl = None  # type: ignore[assignment]


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Take a non-blocking lock compatible with Linux's flock command."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise RuntimeError(f"Another backup process holds the lock: {path}") from None
        try:
            os.ftruncate(descriptor, 0)
            os.write(descriptor, f"{os.getpid()}\n".encode())
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        return

    # O_EXCL is the portable fallback. Reclaim a lock when its recorded process
    # no longer exists so an interrupted Windows/manual run cannot block forever.
    for attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if attempt or _lock_owner_is_alive(path):
                raise RuntimeError(f"Backup lock already exists: {path}") from None
            path.unlink(missing_ok=True)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _lock_owner_is_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def ensure_free_space(directory: Path, minimum_free_bytes: int) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < minimum_free_bytes:
        raise RuntimeError(
            f"Insufficient backup disk space: {free} bytes free; "
            f"{minimum_free_bytes} bytes required"
        )
    return free


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def mongodump_config(uri: str) -> Iterator[Path]:
    """Put the Mongo URI in a mode-0600 YAML file instead of process arguments."""
    descriptor, raw_path = tempfile.mkstemp(prefix="tahmeed-mongodump-", suffix=".yml")
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            # JSON strings are valid YAML scalars and safely quote URI punctuation.
            stream.write(f"uri: {json.dumps(uri)}\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


async def mongodump_version(executable: str) -> str:
    process = await asyncio.create_subprocess_exec(
        executable,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("mongodump --version timed out") from None
    if process.returncode:
        raise RuntimeError(f"mongodump --version exited {process.returncode}")
    first_line = stdout.decode(errors="replace").splitlines()
    return first_line[0].strip() if first_line else "mongodump (version unknown)"


def manifest_path(archive: Path) -> Path:
    return archive.with_name(f"{archive.name}.manifest.json")


def mongodb_instance_uri(uri: str) -> str:
    """Remove a URI database path because mongodump --oplog requires a full dump."""
    parsed = urlsplit(uri)
    return urlunsplit((parsed.scheme, parsed.netloc, "/", parsed.query, parsed.fragment))


def write_manifest(path: Path, document: dict[str, Any]) -> str:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return sha256(path)


async def validate_consistency_mode(settings: Settings, db: Any) -> None:
    """Validate runtime requirements for the selected dump consistency mode."""
    if settings.backup_consistency_mode != "oplog":
        return
    hello = await db.command("hello")
    if not hello.get("setName"):
        raise RuntimeError("BACKUP_CONSISTENCY_MODE=oplog requires a MongoDB replica set")


@asynccontextmanager
async def distributed_lease(settings: Settings, db: Any) -> AsyncIterator[None]:
    """Serialize backup activity across hosts using a renewable Mongo lease record."""
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.backup_lease_minutes)
    query = {
        "_id": "global-backup",
        "$or": [{"expires_at": {"$lte": now}}, {"owner": owner}],
    }
    try:
        lease = await db.backup_leases.find_one_and_update(
            query,
            {
                "$set": {
                    "owner": owner,
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "acquired_at": now,
                    "expires_at": expires_at,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        lease = None
    if not lease or lease.get("owner") != owner:
        raise RuntimeError(
            "Another host holds the Mongo backup lease; only one active backup host is allowed"
        )
    stop_heartbeat = asyncio.Event()
    parent_task = asyncio.current_task()

    async def heartbeat() -> None:
        interval = max(60, settings.backup_lease_minutes * 20)
        try:
            while True:
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
                    return
                except TimeoutError:
                    renewed = await db.backup_leases.update_one(
                        {"_id": "global-backup", "owner": owner},
                        {
                            "$set": {
                                "expires_at": datetime.now(UTC)
                                + timedelta(minutes=settings.backup_lease_minutes),
                                "renewed_at": datetime.now(UTC),
                            }
                        },
                    )
                    if getattr(renewed, "matched_count", 1) != 1:
                        raise RuntimeError("Mongo backup lease ownership was lost")
        except Exception:
            if parent_task is not None:
                parent_task.cancel()
            raise

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        yield
    finally:
        stop_heartbeat.set()
        with suppress(Exception, asyncio.CancelledError):
            await heartbeat_task
        await db.backup_leases.update_one(
            {"_id": "global-backup", "owner": owner},
            {"$set": {"expires_at": datetime.now(UTC), "released_at": datetime.now(UTC)}},
        )


def s3_client(settings: Settings) -> Any:
    kwargs: dict[str, Any] = {
        "config": Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"})
    }
    for key, value in (
        ("region_name", settings.backup_s3_region),
        ("endpoint_url", settings.backup_s3_endpoint_url),
        ("aws_access_key_id", settings.backup_s3_access_key_id),
        ("aws_secret_access_key", settings.backup_s3_secret_access_key),
    ):
        if value:
            kwargs[key] = value
    return boto3.client("s3", **kwargs)


def object_key(settings: Settings, filename: str) -> str:
    prefix = settings.backup_s3_prefix.strip("/")
    return f"{prefix}/{filename}" if prefix else filename


async def create_local_backup(
    settings: Settings,
    db: Any,
    cadence: str = "daily",
    *,
    schedule_id: str | None = None,
) -> Path:
    if cadence not in {"daily", "weekly", "monthly"}:
        raise ValueError(f"Unsupported backup cadence: {cadence}")
    settings.backup_directory.mkdir(parents=True, exist_ok=True)
    ensure_free_space(settings.backup_directory, settings.backup_min_free_bytes)
    now = datetime.now(UTC)
    filename = f"{settings.db_name}-{cadence}-{now.strftime('%Y%m%dT%H%M%SZ')}.archive.gz"
    archive = settings.backup_directory / filename
    local_manifest = manifest_path(archive)
    backup_uri = settings.backup_mongodb_uri or settings.mongodb_uri
    object_name = object_key(settings, filename)
    manifest_name = object_key(settings, local_manifest.name)
    tool_version = await mongodump_version(settings.mongodump_path)
    await validate_consistency_mode(settings, db)
    job = {
        "filename": filename,
        "cadence": cadence,
        "local_path": str(archive.resolve()),
        "manifest_path": str(local_manifest.resolve()),
        "object_key": object_name,
        "manifest_key": manifest_name,
        "consistency_mode": settings.backup_consistency_mode,
        "mongodump_version": tool_version,
        "status": "dumping",
        "created_at": now,
        "updated_at": now,
        "attempts": 0,
    }
    if schedule_id:
        job["schedule_id"] = schedule_id
    await db.backup_jobs.insert_one(job)
    try:
        dump_uri = (
            mongodb_instance_uri(backup_uri)
            if settings.backup_consistency_mode == "oplog"
            else backup_uri
        )
        with mongodump_config(dump_uri) as config_path:
            command = [
                settings.mongodump_path,
                f"--config={config_path}",
                f"--archive={archive}",
                "--gzip",
            ]
            if settings.backup_consistency_mode == "oplog":
                # mongodump forbids --oplog with --db. The backup credential must
                # therefore be restricted to the intended database plus oplog access.
                command.append("--oplog")
            else:
                command.extend(("--db", settings.db_name))
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=1800)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("mongodump timed out after 30 minutes") from None
        if process.returncode:
            detail = (
                stderr.decode(errors="replace")
                .replace(backup_uri, "<redacted>")
                .replace(dump_uri, "<redacted>")
            )
            raise RuntimeError(detail.strip() or f"mongodump exited {process.returncode}")
        if not archive.is_file() or archive.stat().st_size == 0:
            raise RuntimeError("mongodump produced no archive")
        checksum = await asyncio.to_thread(sha256, archive)
        completed_at = datetime.now(UTC)
        manifest = {
            "schema_version": 1,
            "archive_filename": filename,
            "archive_object_key": object_name,
            "manifest_object_key": manifest_name,
            "database": settings.db_name,
            "cadence": cadence,
            "consistency_mode": settings.backup_consistency_mode,
            "consistency_note": (
                "replica-set oplog-consistent mongodump"
                if settings.backup_consistency_mode == "oplog"
                else "standalone/off-hours best-effort logical dump; not PITR"
            ),
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
            "archive_size": archive.stat().st_size,
            "archive_sha256": checksum,
            "mongodump_version": tool_version,
            "tools": {
                "mongodump": tool_version,
                "python": platform.python_version(),
            },
        }
        manifest_checksum = await asyncio.to_thread(write_manifest, local_manifest, manifest)
        await db.backup_jobs.update_one(
            {"filename": filename},
            {
                "$set": {
                    "status": "pending_upload",
                    "size": archive.stat().st_size,
                    "sha256": checksum,
                    "manifest_sha256": manifest_checksum,
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                }
            },
        )
        return archive
    except Exception as exc:
        await db.backup_jobs.update_one(
            {"filename": filename},
            {
                "$set": {
                    "status": "failed",
                    "error": str(exc)[:2000],
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        raise


async def upload_pending(settings: Settings, db: Any) -> int:
    if not settings.backup_s3_bucket:
        return 0
    client = s3_client(settings)
    uploaded = 0
    stale_before = datetime.now(UTC) - timedelta(minutes=settings.backup_upload_stale_minutes)
    cursor = db.backup_jobs.find(
        {
            "$or": [
                {"status": {"$in": ["pending_upload", "upload_failed"]}},
                {"status": "uploading", "updated_at": {"$lt": stale_before}},
            ]
        }
    )
    async for job in cursor:
        path = Path(job["local_path"])
        local_manifest = Path(job.get("manifest_path") or manifest_path(path))
        if not path.is_file():
            await db.backup_jobs.update_one(
                {"_id": job["_id"]},
                {"$set": {"status": "failed", "error": "Local archive is missing"}},
            )
            continue
        if not local_manifest.is_file():
            await db.backup_jobs.update_one(
                {"_id": job["_id"]},
                {"$set": {"status": "failed", "error": "Local manifest is missing"}},
            )
            continue
        try:
            archive_size = path.stat().st_size
            archive_checksum = await asyncio.to_thread(sha256, path)
            if archive_size != job.get("size", archive_size) or archive_checksum != job["sha256"]:
                raise RuntimeError("Local archive differs from the checksum or size recorded at dump time")
            manifest_document = json.loads(local_manifest.read_text(encoding="utf-8"))
            if (
                manifest_document.get("archive_size") != archive_size
                or manifest_document.get("archive_sha256") != archive_checksum
            ):
                raise RuntimeError("Local manifest does not match the archive")
            await db.backup_jobs.update_one(
                {"_id": job["_id"]},
                {
                    "$set": {"status": "uploading", "updated_at": datetime.now(UTC)},
                    "$inc": {"attempts": 1},
                },
            )
            await asyncio.to_thread(
                client.upload_file,
                str(path),
                settings.backup_s3_bucket,
                job["object_key"],
                ExtraArgs={
                    "ContentType": "application/gzip",
                    "Metadata": {
                        "sha256": archive_checksum,
                        "database": settings.db_name,
                        "cadence": job.get("cadence", "unspecified"),
                    },
                },
            )
            archive_head = await asyncio.to_thread(
                client.head_object,
                Bucket=settings.backup_s3_bucket,
                Key=job["object_key"],
            )
            archive_metadata = archive_head.get("Metadata", {})
            if (
                archive_head.get("ContentLength") != archive_size
                or archive_metadata.get("sha256") != archive_checksum
            ):
                raise RuntimeError("S3 archive verification failed: size or SHA-256 metadata differs")

            uploaded_at = datetime.now(UTC)
            manifest_document["archive_version_id"] = archive_head.get("VersionId")
            manifest_document["uploaded_at"] = uploaded_at.isoformat().replace("+00:00", "Z")
            manifest_checksum = await asyncio.to_thread(
                write_manifest, local_manifest, manifest_document
            )
            await asyncio.to_thread(
                client.upload_file,
                str(local_manifest),
                settings.backup_s3_bucket,
                job["manifest_key"],
                ExtraArgs={
                    "ContentType": "application/json",
                    "Metadata": {"sha256": manifest_checksum},
                },
            )
            manifest_head = await asyncio.to_thread(
                client.head_object,
                Bucket=settings.backup_s3_bucket,
                Key=job["manifest_key"],
            )
            if (
                manifest_head.get("ContentLength") != local_manifest.stat().st_size
                or manifest_head.get("Metadata", {}).get("sha256") != manifest_checksum
            ):
                raise RuntimeError("S3 manifest verification failed: size or SHA-256 metadata differs")
            await db.backup_jobs.update_one(
                {"_id": job["_id"]},
                {
                    "$set": {
                        "status": "uploaded",
                        "uploaded_at": uploaded_at,
                        "updated_at": uploaded_at,
                        "archive_version_id": archive_head.get("VersionId"),
                        "manifest_version_id": manifest_head.get("VersionId"),
                        "manifest_sha256": manifest_checksum,
                        "remote_verified": True,
                        "local_available": True,
                    },
                    "$unset": {"error": ""},
                },
            )
            uploaded += 1
        except Exception as exc:
            await db.backup_jobs.update_one(
                {"_id": job["_id"]},
                {
                    "$set": {
                        "status": "upload_failed",
                        "error": str(exc)[:2000],
                        "updated_at": datetime.now(UTC),
                    }
                },
            )
    await prune_local(settings, db)
    return uploaded


async def prune_local(settings: Settings, db: Any) -> int:
    """Keep the newest configured number of uploaded archives per cadence."""
    jobs = await db.backup_jobs.find(
        {
            "status": {"$in": ["uploaded", "pruned"]},
            "local_available": {"$ne": False},
        }
    ).sort("created_at", -1).to_list()
    kept: dict[str, int] = {}
    removed = 0
    for job in jobs:
        cadence = job.get("cadence", "unspecified")
        kept[cadence] = kept.get(cadence, 0) + 1
        if kept[cadence] <= settings.backup_local_keep_generations:
            continue
        archive = Path(job["local_path"])
        local_manifest = Path(job.get("manifest_path") or manifest_path(archive))
        archive.unlink(missing_ok=True)
        local_manifest.unlink(missing_ok=True)
        await db.backup_jobs.update_one(
            {"_id": job["_id"]},
            {
                "$set": {
                    "local_available": False,
                    "local_pruned_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        removed += 1
    return removed


def retention_set(jobs: list[dict], daily: int, weekly: int, monthly: int) -> set:
    keep: set = set()
    buckets = (
        (daily, lambda dt: dt.date().isoformat()),
        (weekly, lambda dt: f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"),
        (monthly, lambda dt: f"{dt.year}-{dt.month:02d}"),
    )
    for limit, bucket in buckets:
        seen: set[str] = set()
        for job in jobs:
            key = bucket(job["created_at"])
            if key not in seen and len(seen) < limit:
                keep.add(job["_id"])
                seen.add(key)
    return keep


async def prune(settings: Settings, db: Any) -> int:
    if not settings.backup_s3_bucket:
        return 0
    jobs = await db.backup_jobs.find({"status": "uploaded"}).sort("created_at", -1).to_list()
    keep = retention_set(
        jobs, settings.backup_keep_daily, settings.backup_keep_weekly, settings.backup_keep_monthly
    )
    obsolete = [job for job in jobs if job["_id"] not in keep]
    if not obsolete:
        return 0
    client = s3_client(settings)
    for job in obsolete:
        archive_delete = {
            "Bucket": settings.backup_s3_bucket,
            "Key": job["object_key"],
        }
        if job.get("archive_version_id"):
            archive_delete["VersionId"] = job["archive_version_id"]
        await asyncio.to_thread(client.delete_object, **archive_delete)
        if job.get("manifest_key"):
            manifest_delete = {
                "Bucket": settings.backup_s3_bucket,
                "Key": job["manifest_key"],
            }
            if job.get("manifest_version_id"):
                manifest_delete["VersionId"] = job["manifest_version_id"]
            await asyncio.to_thread(
                client.delete_object,
                **manifest_delete,
            )
        await db.backup_jobs.update_one(
            {"_id": job["_id"]},
            {
                "$set": {
                    "status": "pruned",
                    "pruned_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            },
        )
    return len(obsolete)


async def preflight(settings: Settings, db: Any) -> None:
    executable = shutil.which(settings.mongodump_path)
    if not executable:
        raise RuntimeError(f"mongodump executable not found: {settings.mongodump_path}")
    ensure_free_space(settings.backup_directory, settings.backup_min_free_bytes)
    settings.backup_lock_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=settings.backup_directory, prefix=".write-test-"):
        pass
    with exclusive_lock(settings.backup_lock_file):
        async with distributed_lease(settings, db):
            pass
    version = await mongodump_version(executable)
    await validate_consistency_mode(settings, db)
    if settings.backup_s3_bucket:
        await asyncio.to_thread(s3_client(settings).head_bucket, Bucket=settings.backup_s3_bucket)
    print(
        f"Backup preflight passed ({settings.backup_consistency_mode}; {version}; "
        f"local generations={settings.backup_local_keep_generations})."
    )


async def execute(command: str, cadence: str = "daily") -> None:
    settings = get_settings()
    backup_uri = settings.backup_mongodb_uri or settings.mongodb_uri
    client = AsyncMongoClient(backup_uri, appname="tahmeed-backup")
    try:
        db = client[settings.db_name]
        await db.command("ping")
        if command == "preflight":
            await preflight(settings, db)
            return
        with exclusive_lock(settings.backup_lock_file):
            async with distributed_lease(settings, db):
                if command in {"run", "all"}:
                    archive = await create_local_backup(settings, db, cadence)
                    print(f"Created local {cadence} backup: {archive}")
                if command in {"retry", "all"}:
                    print(f"Uploaded {await upload_pending(settings, db)} backup(s).")
                if command in {"prune", "all"}:
                    print(f"Pruned {await prune(settings, db)} backup(s).")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and upload MongoDB backups.")
    parser.add_argument(
        "command",
        choices=("run", "retry", "prune", "preflight", "all"),
        nargs="?",
        default="all",
    )
    parser.add_argument(
        "--cadence",
        choices=("daily", "weekly", "monthly"),
        default="daily",
        help="Retention cadence metadata for a newly created archive.",
    )
    args = parser.parse_args()
    asyncio.run(execute(args.command, args.cadence))


if __name__ == "__main__":
    main()
