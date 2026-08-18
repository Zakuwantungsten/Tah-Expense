"""Merged WhatsApp sequence: day_order persist + Verify inbox sort."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import List

from tahmeed.services import accountant_service, cashier_service
from tahmeed.services.accountant_service import UNVERIFIED_INBOX_SORT


class _AggCursor:
    def __init__(self, docs: list):
        self._docs = docs

    async def to_list(self, _n=None):
        return list(self._docs)


class _RecordingCursor:
    def __init__(self, docs: List[dict]):
        self._docs = docs
        self.sort_spec = None

    def sort(self, spec):
        self.sort_spec = spec
        return self

    def skip(self, n=0):
        return self

    def limit(self, n=None):
        return self

    async def to_list(self, length=None):
        return list(self._docs)


def test_unverified_inbox_sort_is_date_then_whatsapp_order():
    assert UNVERIFIED_INBOX_SORT == [
        ("date", -1),
        ("day_order", 1),
        ("created_at", 1),
    ]


def test_get_unverified_filtered_uses_whatsapp_sort(monkeypatch):
    cursor = _RecordingCursor([])
    db = SimpleNamespace(transactions=SimpleNamespace(find=lambda *_a, **_k: cursor))
    monkeypatch.setattr(accountant_service, "get_db", lambda: db)

    asyncio.run(accountant_service.get_unverified_filtered(edited=False))
    assert cursor.sort_spec == list(UNVERIFIED_INBOX_SORT)


def test_next_day_order_appends_after_max(monkeypatch):
    coll = SimpleNamespace(
        aggregate=lambda *_a, **_k: _AggCursor([{"_id": None, "mx": 4}])
    )
    monkeypatch.setattr(
        cashier_service, "get_db", lambda: SimpleNamespace(transactions=coll)
    )
    assert asyncio.run(cashier_service.next_day_order(date(2026, 7, 23))) == 5


def test_next_day_order_starts_at_zero_when_empty(monkeypatch):
    coll = SimpleNamespace(aggregate=lambda *_a, **_k: _AggCursor([]))
    monkeypatch.setattr(
        cashier_service, "get_db", lambda: SimpleNamespace(transactions=coll)
    )
    assert asyncio.run(cashier_service.next_day_order(date(2026, 7, 23))) == 0
