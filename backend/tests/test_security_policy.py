"""Tests for IP/MAC allowlists, password policy, and login lockout."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app
from app.security import hash_password
from app.security_policy import (
    enforce_password_policy,
    ip_allowed,
    mac_from_int,
    normalize_mac,
    password_policy_errors,
)


class FakeFind:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self._docs = docs or []

    async def to_list(self, length=None):
        return list(self._docs)


class FakeSettingsCollection:
    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def find(self, query=None):
        if not query:
            docs = [{"key": k, "value": v} for k, v in self.values.items()]
            return FakeFind(docs)
        keys = None
        if isinstance(query, dict) and "key" in query:
            key_q = query["key"]
            if isinstance(key_q, dict) and "$in" in key_q:
                keys = set(key_q["$in"])
            else:
                keys = {key_q}
        docs = [
            {"key": k, "value": v}
            for k, v in self.values.items()
            if keys is None or k in keys
        ]
        return FakeFind(docs)

    async def find_one(self, query):
        key = query.get("key")
        if key in self.values:
            return {"key": key, "value": self.values[key]}
        return None

    async def update_one(self, query, update, upsert=False):
        key = query["key"]
        self.values[key] = update["$set"]["value"]
        return SimpleNamespace(matched_count=1)


class FakeLockouts:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query):
        return self.docs.get(query.get("_id"))

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        current = self.docs.get(key, {"_id": key})
        current.update(update.get("$set", {}))
        if "$setOnInsert" in update and key not in self.docs:
            current.update(update["$setOnInsert"])
        self.docs[key] = current
        return SimpleNamespace(matched_count=1)

    async def delete_one(self, query):
        self.docs.pop(query.get("_id"), None)
        return SimpleNamespace(deleted_count=1)


class FakeUsers:
    def __init__(self, users: list[dict]) -> None:
        self.users = users

    async def find_one(self, query):
        for user in self.users:
            if all(user.get(k) == v for k, v in query.items()):
                return user
        return None

    async def update_one(self, query, update):
        return SimpleNamespace(matched_count=1)

    async def count_documents(self, query):
        return len(self.users)


class FakeSessions:
    async def insert_one(self, document):
        return SimpleNamespace(inserted_id=document.get("_id") or ObjectId())

    async def update_many(self, *args, **kwargs):
        return SimpleNamespace(modified_count=0)


class FakeBackupJobs:
    def find(self):
        return self

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        return []


def _settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(system_settings: dict | None = None, users: list[dict] | None = None):
    lockouts = FakeLockouts()
    db = SimpleNamespace(
        system_settings=FakeSettingsCollection(system_settings),
        auth_lockouts=lockouts,
        users=FakeUsers(users or []),
        auth_sessions=FakeSessions(),
        backup_jobs=FakeBackupJobs(),
        operation_locks=SimpleNamespace(),
    )
    database = SimpleNamespace(db=db, ping=lambda: None, close=lambda: None)
    app = create_app(settings=_settings(), database=database)
    app.state._lockouts = lockouts
    return app


def test_normalize_mac_and_ip_helpers() -> None:
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("bad") is None
    assert ip_allowed("203.0.113.10", ["203.0.113.0/24"])
    assert not ip_allowed("198.51.100.1", ["203.0.113.0/24"])
    assert mac_from_int(0xAABBCCDDEEFF) == "AA:BB:CC:DD:EE:FF"


def test_password_policy_rejects_weak() -> None:
    policy = {
        "security_password_min_length": 10,
        "security_password_require_upper": True,
        "security_password_require_lower": True,
        "security_password_require_digit": True,
        "security_password_require_special": False,
    }
    assert password_policy_errors("short", policy)
    with pytest.raises(Exception):
        enforce_password_policy("alllowercase1", policy)
    enforce_password_policy("GoodPass12", policy)


def test_ip_allowlist_blocks_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAHMEED_SECURITY_BYPASS", raising=False)
    app = _app(
        {
            "security_ip_allowlist_enabled": True,
            "security_allowed_ips": ["203.0.113.10"],
        }
    )
    with TestClient(app) as client:
        response = client.get(
            "/v1/settings",
            headers={"CF-Connecting-IP": "198.51.100.1"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ip_not_allowed"


def test_ip_allowlist_allows_listed_and_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAHMEED_SECURITY_BYPASS", raising=False)
    app = _app(
        {
            "security_ip_allowlist_enabled": True,
            "security_allowed_ips": ["203.0.113.10"],
        }
    )
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "admin",
        "role": "admin",
    }
    with TestClient(app) as client:
        ok = client.get(
            "/v1/settings",
            headers={"CF-Connecting-IP": "203.0.113.10"},
        )
    assert ok.status_code == 200

    monkeypatch.setenv("TAHMEED_SECURITY_BYPASS", "1")
    with TestClient(app) as client:
        bypassed = client.get(
            "/v1/settings",
            headers={"CF-Connecting-IP": "198.51.100.1"},
        )
    assert bypassed.status_code == 200


def test_mac_allowlist_blocks_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAHMEED_SECURITY_BYPASS", raising=False)
    app = _app(
        {
            "security_mac_allowlist_enabled": True,
            "security_allowed_macs": ["AA:BB:CC:DD:EE:FF"],
        }
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"CF-Connecting-IP": "203.0.113.10"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "mac_not_allowed"


def test_login_lockout_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAHMEED_SECURITY_BYPASS", raising=False)
    user = {
        "_id": ObjectId(),
        "username": "cashier1",
        "active": True,
        "role": "cashier",
        "password_hash": hash_password("GoodPass12"),
        "full_name": "Cashier",
    }
    app = _app(
        {
            "security_login_max_attempts": 2,
            "security_login_lockout_minutes": 15,
        },
        users=[user],
    )
    headers = {"CF-Connecting-IP": "203.0.113.10", "X-Device-Mac": "AA:BB:CC:DD:EE:FF"}
    with TestClient(app) as client:
        for _ in range(2):
            bad = client.post(
                "/v1/auth/login",
                json={"username": "cashier1", "password": "wrong"},
                headers=headers,
            )
            assert bad.status_code == 401
        locked = client.post(
            "/v1/auth/login",
            json={"username": "cashier1", "password": "GoodPass12"},
            headers=headers,
        )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"


def test_backups_require_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAHMEED_SECURITY_BYPASS", raising=False)
    app = _app()
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "acct",
        "role": "accountant",
    }
    with TestClient(app) as client:
        denied = client.get("/v1/backups")
        denied_run = client.post("/v1/backups/run")
    assert denied.status_code == 403
    assert denied_run.status_code == 403

    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "admin",
        "role": "admin",
    }
    with TestClient(app) as client:
        allowed = client.get("/v1/backups")
    assert allowed.status_code == 200
