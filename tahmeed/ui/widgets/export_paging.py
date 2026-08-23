"""Paginated fetch helper for Excel exports (no row cap)."""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, TypeVar

T = TypeVar("T")

EXPORT_PAGE_SIZE = 2000


async def fetch_all_pages(
    fetch: Callable[..., Awaitable[List[T]]],
    *,
    page_size: int = EXPORT_PAGE_SIZE,
    on_batch: Optional[Callable[[int], None]] = None,
) -> List[T]:
    """Walk a (limit, skip) fetch until the source is exhausted."""
    out: List[T] = []
    skip = 0
    while True:
        batch = await fetch(limit=page_size, skip=skip)
        if not batch:
            break
        out.extend(batch)
        if on_batch is not None:
            on_batch(len(out))
        if len(batch) < page_size:
            break
        skip += len(batch)
    return out
