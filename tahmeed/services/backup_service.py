"""Read-only backup job status supplied by the backend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document


@dataclass(frozen=True)
class BackupJob:
    filename: str
    status: str
    created_at: datetime
    updated_at: datetime
    size: int = 0
    attempts: int = 0
    error: str = ""


async def list_backup_jobs(limit: int = 100) -> list[BackupJob]:
    documents = await api_client.request(
        "GET", "v1/backups", params={"limit": limit}
    )
    jobs: list[BackupJob] = []
    for document in documents:
        data = desktop_document(document)
        jobs.append(
            BackupJob(
                filename=data.get("filename", "Unknown"),
                status=data.get("status", "unknown"),
                created_at=data["created_at"],
                updated_at=data.get("updated_at", data["created_at"]),
                size=int(data.get("size", 0)),
                attempts=int(data.get("attempts", 0)),
                error=data.get("error", ""),
            )
        )
    return jobs
