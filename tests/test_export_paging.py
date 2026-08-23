"""Paginated export fetch — no row cap."""

from __future__ import annotations

import asyncio

from tahmeed.ui.widgets.export_paging import EXPORT_PAGE_SIZE, fetch_all_pages


def test_fetch_all_pages_walks_until_short_batch() -> None:
    batches = [
        [{"id": 1}, {"id": 2}],
        [{"id": 3}],
    ]

    async def fetch(*, limit, skip):
        assert limit == 2
        idx = skip // 2
        return batches[idx] if idx < len(batches) else []

    recs = asyncio.run(fetch_all_pages(fetch, page_size=2))
    assert [r["id"] for r in recs] == [1, 2, 3]


def test_fetch_all_pages_no_artificial_cap() -> None:
    """Many pages must all be returned (no 250k-style cutoff)."""
    total = EXPORT_PAGE_SIZE * 3 + 7

    async def fetch(*, limit, skip):
        end = min(skip + limit, total)
        if skip >= total:
            return []
        return [{"n": i} for i in range(skip, end)]

    recs = asyncio.run(fetch_all_pages(fetch, page_size=EXPORT_PAGE_SIZE))
    assert len(recs) == total
