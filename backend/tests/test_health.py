from fastapi.testclient import TestClient

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
        ready = client.get("/health/ready")
    assert live.json() == {"status": "ok"}
    assert ready.json() == {"status": "ready"}
    assert ready.headers["x-request-id"]
    assert database.pings == 1
