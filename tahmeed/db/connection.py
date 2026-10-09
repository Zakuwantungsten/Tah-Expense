"""Optional Motor client for admin scripts / tests.

The desktop app is API-only and must not call these helpers. Prefer the
backend process (``backend/.env``) for application data access.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from tahmeed.config import MONGODB_URI, DB_NAME

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        if not MONGODB_URI:
            raise RuntimeError(
                "MONGODB_URI is not set. Desktop builds do not need it; "
                "for admin scripts set MONGODB_URI in the environment "
                "(loopback for local work — see LOCAL_DEV.md)."
            )
        _client = AsyncIOMotorClient(MONGODB_URI)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[DB_NAME]


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
