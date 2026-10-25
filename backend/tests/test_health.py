import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.batch_lock import batch_lock
from app.config import Settings
from app.main import create_app


class FakeDatabase:
    def __init__(self) -> None:
        self.db = object()
        self.pings = 0

    async def ping(self) -> None:
        self.pings += 1


def test_health_endpoints_without_mongodb() -> None:
    database = FakeDatabase()
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )
    with TestClient(create_app(settings=settings, database=database)) as client:
        live = client.get("/health/live")
        assert live.json() == {"status": "ok"}
        assert database.pings == 0
        ready = client.get("/health/ready")
    assert ready.json() == {"status": "ready"}
    assert ready.headers["x-request-id"]
    assert database.pings == 1


class _RecordingDb:
    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str):
        self.touched.append(name)
        raise RuntimeError("no database in this test")


def test_live_skips_the_settings_read() -> None:
    database = FakeDatabase()
    database.db = _RecordingDb()
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )
    with TestClient(create_app(settings=settings, database=database)) as client:
        database.db.touched.clear()
        live = client.get("/health/live")
        assert live.json() == {"status": "ok"}
        assert database.db.touched == []
        client.get("/not-a-route")
        assert "system_settings" in database.db.touched


@pytest.mark.asyncio
async def test_live_answers_while_import_lock_is_held() -> None:
    lock = batch_lock()
    await lock.acquire()
    try:
        database = FakeDatabase()
        settings = Settings(
            mongodb_uri="mongodb://localhost:27017",
            jwt_secret="a-test-secret-that-is-definitely-32-characters",
        )
        app = create_app(settings=settings, database=database)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(client.get("/health/live"), timeout=1)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert database.pings == 0
    finally:
        lock.release()
