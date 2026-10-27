"""Merged WhatsApp sequence: day_order persist + Verify inbox sort."""
from __future__ import annotations

import asyncio
from datetime import date

from tahmeed.services import cashier_service
from tahmeed.services.accountant_service import UNVERIFIED_INBOX_SORT


def test_unverified_inbox_sort_is_date_then_whatsapp_order():
    assert UNVERIFIED_INBOX_SORT == [
        ("date", -1),
        ("day_order", 1),
        ("created_at", 1),
    ]


def _fake_api(monkeypatch, payload):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return payload

    monkeypatch.setattr(cashier_service.api_client, "request", request)
    return calls


def test_next_day_order_reads_server_next(monkeypatch):
    calls = _fake_api(monkeypatch, {"next": 5})
    assert asyncio.run(cashier_service.next_day_order(date(2026, 7, 23))) == 5
    assert calls == [
        ("GET", "v1/transactions/next-day-order", {"params": {"date": "2026-07-23"}})
    ]


def test_next_day_order_starts_at_zero_when_missing(monkeypatch):
    _fake_api(monkeypatch, {})
    assert asyncio.run(cashier_service.next_day_order(date(2026, 7, 23))) == 0
