from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    mongodb_uri: str
    db_name: str = "tahmeed_expense"
    jwt_secret: str = Field(min_length=32)
    jwt_issuer: str = "tahmeed-api"
    jwt_audience: str = "tahmeed-clients"
    access_token_minutes: int = Field(15, ge=1, le=120)
    refresh_token_days: int = Field(30, ge=1, le=365)
    cors_origins: list[str] = []

    mongodump_path: str = "mongodump"
    mongorestore_path: str = "mongorestore"
    backup_directory: Path = Path("./var/backups")
    backup_lock_file: Path = Path("./var/backup.lock")
    backup_s3_bucket: str = ""
    backup_s3_region: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_access_key_id: str = ""
    backup_s3_secret_access_key: str = ""
    backup_s3_prefix: str = "tahmeed-expense/database-backups"
    backup_mongodb_uri: str = ""
    backup_min_free_bytes: int = Field(5 * 1024 * 1024 * 1024, ge=0)
    backup_upload_stale_minutes: int = Field(60, ge=5)
    backup_consistency_mode: str = "standalone"
    backup_local_keep_generations: int = Field(3, ge=1)
    backup_lease_minutes: int = Field(90, ge=35)
    backup_keep_daily: int = Field(7, ge=0)
    backup_keep_weekly: int = Field(4, ge=0)
    backup_keep_monthly: int = Field(6, ge=0)
    backup_daily_time_utc: str = "02:00"
    backup_weekly_day_utc: int = Field(6, ge=0, le=6)
    backup_weekly_time_utc: str = "03:00"
    backup_maintenance_interval_minutes: int = Field(30, ge=5)
    backup_scheduler_poll_seconds: int = Field(60, ge=10, le=3600)
    backup_schedule_stale_minutes: int = Field(120, ge=35)
    backup_schedule_catchup_minutes: int = Field(360, ge=0, le=1440)

    @field_validator("mongodb_uri")
    @classmethod
    def validate_mongodb_uri(cls, value: str) -> str:
        if not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("must be a MongoDB URI")
        return value

    @field_validator("backup_mongodb_uri")
    @classmethod
    def validate_backup_mongodb_uri(cls, value: str) -> str:
        if value and not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("must be empty or a MongoDB URI")
        return value

    @field_validator("backup_consistency_mode")
    @classmethod
    def validate_backup_consistency_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"standalone", "oplog"}:
            raise ValueError("must be 'standalone' or 'oplog'")
        return normalized

    @field_validator("backup_daily_time_utc", "backup_weekly_time_utc")
    @classmethod
    def validate_utc_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("must be HH:MM in UTC")
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("must be HH:MM in UTC")
        return f"{hour:02d}:{minute:02d}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
