import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.cli import backup
from app.cli.backup import create_local_backup, ensure_free_space, exclusive_lock, retention_set
from app.config import Settings
from app.serialization import json_safe


def test_json_safe_handles_nested_bson_and_datetime() -> None:
    oid = ObjectId()
    value = {"_id": oid, "nested": [datetime(2026, 1, 1, tzinfo=timezone.utc)]}
    result = json_safe(value)
    assert result["_id"] == str(oid)
    assert result["nested"] == ["2026-01-01T00:00:00Z"]


def test_retention_keeps_union_of_time_buckets() -> None:
    jobs = [
        {"_id": index, "created_at": datetime(2026, month, 1, tzinfo=timezone.utc)}
        for index, month in enumerate(range(6, 0, -1))
    ]
    kept = retention_set(jobs, daily=1, weekly=1, monthly=3)
    assert {0, 1, 2}.issubset(kept)
    assert 5 not in kept


def test_backup_plan_defaults() -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )
    assert (
        settings.backup_keep_daily,
        settings.backup_keep_weekly,
        settings.backup_keep_monthly,
    ) == (7, 4, 6)
    assert settings.backup_consistency_mode == "standalone"
    assert settings.backup_local_keep_generations == 3


def test_free_space_check_rejects_low_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=99))
    with pytest.raises(RuntimeError, match="Insufficient backup disk space"):
        ensure_free_space(tmp_path, 100)


def test_exclusive_lock_rejects_concurrent_owner(tmp_path: Path) -> None:
    lock = tmp_path / "backup.lock"
    with exclusive_lock(lock), pytest.raises(RuntimeError, match="lock"):
        with exclusive_lock(lock):
            pass


class FakeBackupJobs:
    def __init__(self) -> None:
        self.inserted: dict | None = None
        self.updates: list[tuple[dict, dict]] = []

    async def insert_one(self, job: dict) -> None:
        self.inserted = job

    async def update_one(self, query: dict, update: dict) -> None:
        self.updates.append((query, update))


class FakeProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


class FakeBackupDb:
    def __init__(self, jobs: FakeBackupJobs, replica_set: bool = True) -> None:
        self.backup_jobs = jobs
        self.replica_set = replica_set
        self.commands: list[str] = []

    async def command(self, name: str) -> dict:
        self.commands.append(name)
        return {"setName": "rs0"} if self.replica_set else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["standalone", "oplog"])
async def test_local_backup_records_cadence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    jobs = FakeBackupJobs()
    db = FakeBackupDb(jobs)
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_directory=tmp_path,
        backup_min_free_bytes=0,
        backup_consistency_mode=mode,
    )

    observed: dict[str, object] = {}

    async def fake_subprocess(*args: object, **kwargs: object) -> FakeProcess:
        observed["args"] = args
        config_arg = next(str(arg) for arg in args if str(arg).startswith("--config="))
        config_path = Path(config_arg.removeprefix("--config="))
        observed["config"] = config_path.read_text()
        observed["config_mode"] = stat.S_IMODE(config_path.stat().st_mode)
        archive_arg = next(str(arg) for arg in args if str(arg).startswith("--archive="))
        Path(archive_arg.removeprefix("--archive=")).write_bytes(b"archive")
        return FakeProcess()

    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_subprocess)
    async def fake_version(_: str) -> str:
        return "mongodump version test"

    monkeypatch.setattr(backup, "mongodump_version", fake_version)
    archive = await create_local_backup(
        settings, db, cadence="weekly", schedule_id="backup-weekly:20260719T0300Z"
    )

    assert archive.is_file()
    assert backup.manifest_path(archive).is_file()
    assert "-weekly-" in archive.name
    assert settings.mongodb_uri not in " ".join(str(arg) for arg in observed["args"])
    if os.name == "posix":
        assert observed["config_mode"] == 0o600
    if mode == "standalone":
        assert settings.mongodb_uri in str(observed["config"])
    else:
        assert backup.mongodb_instance_uri(settings.mongodb_uri) in str(observed["config"])
    assert ("--oplog" in observed["args"]) is (mode == "oplog")
    assert ("--db" in observed["args"]) is (mode == "standalone")
    assert jobs.inserted is not None
    assert jobs.inserted["cadence"] == "weekly"
    assert jobs.inserted["schedule_id"] == "backup-weekly:20260719T0300Z"
    assert db.commands == (["hello"] if mode == "oplog" else [])
    assert jobs.updates[-1][1]["$set"]["status"] == "pending_upload"
    manifest = json.loads(backup.manifest_path(archive).read_text())
    if mode == "standalone":
        assert manifest["consistency_note"].endswith("not PITR")
    else:
        assert manifest["consistency_note"] == "replica-set oplog-consistent mongodump"
    assert manifest["mongodump_version"] == "mongodump version test"
    assert manifest["tools"]["mongodump"] == "mongodump version test"


@pytest.mark.asyncio
async def test_oplog_backup_rejects_standalone_before_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_directory=tmp_path,
        backup_min_free_bytes=0,
        backup_consistency_mode="oplog",
    )
    jobs = FakeBackupJobs()

    async def fake_version(_: str) -> str:
        return "mongodump version test"

    monkeypatch.setattr(backup, "mongodump_version", fake_version)
    with pytest.raises(RuntimeError, match="requires a MongoDB replica set"):
        await create_local_backup(settings, FakeBackupDb(jobs, replica_set=False))
    assert jobs.inserted is None


@pytest.mark.asyncio
async def test_retry_without_s3_is_local_only() -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_s3_bucket="",
    )
    assert await backup.upload_pending(settings, object()) == 0


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def __aiter__(self):
        async def iterate():
            for document in self.documents:
                yield document

        return iterate()

    def sort(self, *_: object) -> "FakeCursor":
        return self

    async def to_list(self) -> list[dict]:
        return self.documents


class UploadBackupJobs:
    def __init__(self, pending: list[dict], uploaded: list[dict] | None = None) -> None:
        self.pending = pending
        self.uploaded = uploaded or []
        self.updates: list[tuple[dict, dict]] = []
        self.find_count = 0

    def find(self, _: dict) -> FakeCursor:
        self.find_count += 1
        return FakeCursor(self.pending if self.find_count == 1 else self.uploaded)

    async def update_one(self, query: dict, update: dict) -> None:
        self.updates.append((query, update))


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.deletions: list[dict] = []

    def upload_file(
        self, filename: str, bucket: str, key: str, ExtraArgs: dict  # noqa: N803
    ) -> None:
        self.objects[key] = {
            "ContentLength": Path(filename).stat().st_size,
            "Metadata": ExtraArgs["Metadata"],
            "VersionId": f"version-{len(self.objects) + 1}",
            "Bucket": bucket,
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        assert self.objects[Key]["Bucket"] == Bucket
        return self.objects[Key]

    def delete_object(self, **kwargs: str) -> None:
        self.deletions.append(kwargs)


@pytest.mark.asyncio
async def test_upload_verifies_archive_and_manifest_and_keeps_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "db-daily.archive.gz"
    archive.write_bytes(b"archive")
    local_manifest = backup.manifest_path(archive)
    document = {
        "archive_object_key": "prefix/db-daily.archive.gz",
        "archive_size": archive.stat().st_size,
        "archive_sha256": backup.sha256(archive),
    }
    backup.write_manifest(local_manifest, document)
    job = {
        "_id": 1,
        "local_path": str(archive),
        "manifest_path": str(local_manifest),
        "object_key": "prefix/db-daily.archive.gz",
        "manifest_key": "prefix/db-daily.archive.gz.manifest.json",
        "sha256": backup.sha256(archive),
        "cadence": "daily",
        "created_at": datetime.now(timezone.utc),
        "status": "pending_upload",
    }
    jobs = UploadBackupJobs([job])
    db = SimpleNamespace(backup_jobs=jobs)
    client = FakeS3()
    monkeypatch.setattr(backup, "s3_client", lambda _: client)
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_s3_bucket="bucket",
        backup_directory=tmp_path,
    )

    assert await backup.upload_pending(settings, db) == 1
    assert archive.is_file()
    assert local_manifest.is_file()
    uploaded_manifest = json.loads(local_manifest.read_text())
    assert uploaded_manifest["archive_version_id"] == "version-1"
    completed = [update for _, update in jobs.updates if update["$set"].get("status") == "uploaded"]
    assert completed[0]["$set"]["remote_verified"] is True
    assert completed[0]["$set"]["manifest_version_id"] == "version-2"
    assert uploaded_manifest["uploaded_at"].endswith("Z")


@pytest.mark.asyncio
async def test_upload_rejects_locally_modified_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "db-daily.archive.gz"
    archive.write_bytes(b"original")
    original_checksum = backup.sha256(archive)
    local_manifest = backup.manifest_path(archive)
    backup.write_manifest(
        local_manifest,
        {
            "archive_size": archive.stat().st_size,
            "archive_sha256": original_checksum,
        },
    )
    archive.write_bytes(b"tampered")
    job = {
        "_id": 1,
        "local_path": str(archive),
        "manifest_path": str(local_manifest),
        "object_key": "prefix/db-daily.archive.gz",
        "manifest_key": "prefix/db-daily.archive.gz.manifest.json",
        "size": archive.stat().st_size,
        "sha256": original_checksum,
        "cadence": "daily",
        "created_at": datetime.now(timezone.utc),
        "status": "pending_upload",
    }
    jobs = UploadBackupJobs([job])
    client = FakeS3()
    monkeypatch.setattr(backup, "s3_client", lambda _: client)
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_s3_bucket="bucket",
        backup_directory=tmp_path,
    )

    assert await backup.upload_pending(settings, SimpleNamespace(backup_jobs=jobs)) == 0
    assert client.objects == {}
    failed = [update for _, update in jobs.updates if update["$set"].get("status") == "upload_failed"]
    assert "Local archive differs" in failed[0]["$set"]["error"]


@pytest.mark.asyncio
async def test_prune_local_keeps_configured_generations_per_cadence(tmp_path: Path) -> None:
    jobs_list = []
    for index in range(3):
        archive = tmp_path / f"db-daily-{index}.archive.gz"
        archive.write_bytes(str(index).encode())
        backup.manifest_path(archive).write_text("{}")
        jobs_list.append(
            {
                "_id": index,
                "local_path": str(archive),
                "manifest_path": str(backup.manifest_path(archive)),
                "cadence": "daily",
                "created_at": datetime(2026, 1, 3 - index, tzinfo=timezone.utc),
            }
        )
    jobs = UploadBackupJobs([], uploaded=jobs_list)
    jobs.find_count = 1
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_directory=tmp_path,
        backup_local_keep_generations=2,
    )

    assert await backup.prune_local(settings, SimpleNamespace(backup_jobs=jobs)) == 1
    assert Path(jobs_list[0]["local_path"]).is_file()
    assert Path(jobs_list[1]["local_path"]).is_file()
    assert not Path(jobs_list[2]["local_path"]).exists()


@pytest.mark.asyncio
async def test_remote_prune_deletes_archive_and_manifest_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = {
        "_id": 1,
        "object_key": "prefix/archive.gz",
        "manifest_key": "prefix/archive.gz.manifest.json",
        "archive_version_id": "archive-version",
        "manifest_version_id": "manifest-version",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "status": "uploaded",
    }
    jobs = UploadBackupJobs([job])
    client = FakeS3()
    monkeypatch.setattr(backup, "s3_client", lambda _: client)
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_s3_bucket="bucket",
        backup_keep_daily=0,
        backup_keep_weekly=0,
        backup_keep_monthly=0,
    )

    assert await backup.prune(settings, SimpleNamespace(backup_jobs=jobs)) == 1
    assert client.deletions == [
        {"Bucket": "bucket", "Key": "prefix/archive.gz", "VersionId": "archive-version"},
        {
            "Bucket": "bucket",
            "Key": "prefix/archive.gz.manifest.json",
            "VersionId": "manifest-version",
        },
    ]
    assert jobs.updates[-1][1]["$set"]["status"] == "pruned"


class RestoreBackupJobs:
    def __init__(self, job: dict | None) -> None:
        self.job = job

    async def find_one(self, query: dict) -> dict | None:
        if self.job and all(self.job.get(key) == value for key, value in query.items()):
            return self.job
        return None


class RestoreAudit:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.inserted.append(document)


@pytest.mark.asyncio
async def test_restore_requires_matching_confirmation(tmp_path: Path) -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_directory=tmp_path,
        backup_min_free_bytes=0,
    )
    db = SimpleNamespace(backup_jobs=RestoreBackupJobs(None), restore_audit=RestoreAudit())
    with pytest.raises(RuntimeError, match="Confirmation filename"):
        await backup.restore_database(
            settings,
            db,
            filename="a.archive.gz",
            confirm_filename="b.archive.gz",
        )


@pytest.mark.asyncio
async def test_restore_rejects_non_uploaded_status(tmp_path: Path) -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_directory=tmp_path,
        backup_min_free_bytes=0,
    )
    job = {"filename": "db.archive.gz", "status": "pending_upload"}
    db = SimpleNamespace(backup_jobs=RestoreBackupJobs(job), restore_audit=RestoreAudit())
    with pytest.raises(RuntimeError, match="Only uploaded"):
        await backup.restore_database(
            settings,
            db,
            filename="db.archive.gz",
            confirm_filename="db.archive.gz",
        )


@pytest.mark.asyncio
async def test_restore_runs_mongorestore_for_local_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "db-daily.archive.gz"
    archive.write_bytes(b"archive-bytes")
    checksum = backup.sha256(archive)
    job = {
        "filename": archive.name,
        "status": "uploaded",
        "local_path": str(archive),
        "object_key": f"prefix/{archive.name}",
        "sha256": checksum,
        "size": archive.stat().st_size,
    }
    audit = RestoreAudit()
    db = SimpleNamespace(backup_jobs=RestoreBackupJobs(job), restore_audit=audit)
    commands: list[list[str]] = []

    class FakeRestoreProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*args: str, **_kwargs: object) -> FakeRestoreProcess:
        commands.append(list(args))
        return FakeRestoreProcess()

    monkeypatch.setattr(backup.shutil, "which", lambda _: "/usr/bin/mongorestore")
    monkeypatch.setattr(backup.asyncio, "create_subprocess_exec", fake_exec)
    settings = Settings(
        mongodb_uri="mongodb://restore-user@localhost:27017/tahmeed_expense",
        db_name="tahmeed_expense",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_directory=tmp_path,
        backup_min_free_bytes=0,
        mongorestore_path="mongorestore",
    )

    result = await backup.restore_database(
        settings,
        db,
        filename=archive.name,
        confirm_filename=archive.name,
        actor={"_id": ObjectId(), "username": "admin"},
    )
    assert result["status"] == "completed"
    assert result["filename"] == archive.name
    assert commands and commands[0][0] == "/usr/bin/mongorestore"
    assert "--drop" in commands[0]
    assert f"--archive={archive}" in commands[0]
    assert audit.inserted and audit.inserted[0]["actor_username"] == "admin"
