"""One import batch at a time inside this API process.

Saves, verify, and ``/health/live`` do not acquire this lock. A second import
in the same process waits. This is not a separate service.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

_lock: asyncio.Lock | None = None


def batch_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def serialized_batch(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    async def wrapped(*args: object, **kwargs: object) -> T:
        async with batch_lock():
            return await fn(*args, **kwargs)

    wrapped.__name__ = getattr(fn, "__name__", "serialized_batch")
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    return wrapped
