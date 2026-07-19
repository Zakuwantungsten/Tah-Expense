from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pymongo import AsyncMongoClient

from .config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongodb_uri,
            appname="tahmeed-api",
            serverSelectionTimeoutMS=5000,
            tz_aware=True,
        )
        self.db = self.client[settings.db_name]

    async def ping(self) -> None:
        await self.db.command("ping")

    async def close(self) -> None:
        await self.client.close()


@asynccontextmanager
async def database_lifespan(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings)
    try:
        yield database
    finally:
        await database.close()
