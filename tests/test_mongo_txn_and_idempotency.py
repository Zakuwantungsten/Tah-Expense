"""Tests for Mongo transaction helper and import idempotency."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import BulkWriteError, OperationFailure

from tahmeed.db import import_idempotency as idem
from tahmeed.db import mongo_txn


@pytest.fixture(autouse=True)
def _reset_caches():
    mongo_txn.reset_transaction_support_cache()
    idem.reset_import_index_cache()
    yield
    mongo_txn.reset_transaction_support_cache()
    idem.reset_import_index_cache()


@pytest.mark.asyncio
async def test_run_in_transaction_falls_back_when_no_replica_set():
    with patch(
        "tahmeed.db.mongo_txn.supports_transactions",
        new=AsyncMock(return_value=False),
    ):
        seen = []

        async def body(session):
            seen.append(session)
            return "ok"

        assert await mongo_txn.run_in_transaction(body) == "ok"
        assert seen == [None]


@pytest.mark.asyncio
async def test_run_in_transaction_uses_session_when_supported():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.start_transaction = MagicMock(return_value=session)

    client = MagicMock()
    client.start_session = AsyncMock(return_value=session)

    with (
        patch(
            "tahmeed.db.mongo_txn.supports_transactions",
            new=AsyncMock(return_value=True),
        ),
        patch("tahmeed.db.mongo_txn.get_client", return_value=client),
    ):
        seen = []

        async def body(sess):
            seen.append(sess)
            return 42

        assert await mongo_txn.run_in_transaction(body) == 42
        assert seen == [session]


@pytest.mark.asyncio
async def test_run_in_transaction_falls_back_on_unsupported_error():
    client = MagicMock()
    client.start_session = AsyncMock(
        side_effect=OperationFailure(
            "Transaction numbers are only allowed on a replica set member",
            code=20,
        )
    )

    with (
        patch(
            "tahmeed.db.mongo_txn.supports_transactions",
            new=AsyncMock(return_value=True),
        ),
        patch("tahmeed.db.mongo_txn.get_client", return_value=client),
    ):
        async def body(session):
            return session is None

        assert await mongo_txn.run_in_transaction(body) is True


def test_daily_import_row_key_stable():
    payload = {
        "serial": 12,
        "date": datetime(2026, 8, 1),
        "description": "  parking  ",
        "truck_number": "t688 eaf",
        "amount": 5000,
        "item": "Parking",
        "lpo_do": "",
        "do_number": "",
        "currency": "tzs",
    }
    a = idem.daily_import_row_key(payload)
    b = idem.daily_import_row_key(dict(payload))
    assert a == b
    assert len(a) == 40


@pytest.mark.asyncio
async def test_insert_many_idempotent_counts_duplicates():
    collection = MagicMock()
    collection.insert_many = AsyncMock(
        side_effect=BulkWriteError(
            {
                "nInserted": 1,
                "writeErrors": [
                    {"code": 11000, "index": 1},
                    {"code": 11000, "index": 2},
                ],
            }
        )
    )
    inserted, dupes = await idem.insert_many_idempotent(
        collection, [{"a": 1}, {"a": 2}, {"a": 3}]
    )
    assert inserted == 1
    assert dupes == 2


@pytest.mark.asyncio
async def test_insert_many_idempotent_reraises_other_errors():
    collection = MagicMock()
    collection.insert_many = AsyncMock(
        side_effect=BulkWriteError(
            {
                "nInserted": 0,
                "writeErrors": [{"code": 121, "index": 0}],
            }
        )
    )
    with pytest.raises(BulkWriteError):
        await idem.insert_many_idempotent(collection, [{"a": 1}])
