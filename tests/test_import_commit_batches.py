"""Daily commit is one request per batch, and a retry skips finished batches."""

from __future__ import annotations

import pytest

from tahmeed.services import daily_import_service as svc


def _rows(count: int) -> list[dict]:
    return [
        {
            "description": f"Row {index}",
            "amount": index,
            "category_name": "DIESEL",
            "daily_import_id": "up-1",
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_failed_batch_retries_without_resending_earlier_batches(monkeypatch) -> None:
    calls: list[int] = []
    fail_once = {"left": 1}

    async def request(method: str, path: str, *, json: dict):
        assert method == "POST"
        assert path == "v1/imports/daily/commit"
        sent = json["rows"]
        calls.append(len(sent))
        # Second batch fails the first time it is posted.
        if len(calls) == 2 and fail_once["left"]:
            fail_once["left"] = 0
            raise RuntimeError("batch failed")
        return {
            "inserted": len(sent),
            "duplicates_skipped": 0,
            "skipped_no_item": 0,
            "upload_id": "up-1",
            "source": "daily.xlsx",
            "min_date": None,
            "max_date": None,
        }

    monkeypatch.setattr(svc.api_client, "request", request)
    result = await svc.commit_daily_to_master(_rows(1200))

    assert calls == [500, 500, 500, 200]
    assert result["inserted"] == 1200
    assert result["upload_id"] == "up-1"


@pytest.mark.asyncio
async def test_start_batch_sends_only_the_unfinished_tail(monkeypatch) -> None:
    calls: list[int] = []

    async def request(_method: str, _path: str, *, json: dict):
        calls.append(len(json["rows"]))
        return {
            "inserted": len(json["rows"]),
            "duplicates_skipped": 0,
            "skipped_no_item": 0,
            "upload_id": "up-1",
        }

    monkeypatch.setattr(svc.api_client, "request", request)
    result = await svc.commit_daily_to_master(_rows(1200), start_batch=1)

    assert calls == [500, 200]
    assert result["inserted"] == 700
