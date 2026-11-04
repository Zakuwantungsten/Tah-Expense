"""Feed import client chunks stay under API schema caps."""

from __future__ import annotations

from tahmeed.services.feed_service import (
    _EXISTING_KEYS_CHUNK,
    _IMPORT_CHUNK,
    _chunks,
    _import_row_payload,
)


def test_chunks_cover_all_items_without_overlap() -> None:
    items = list(range(5_001))
    parts = [list(c) for c in _chunks(items, _IMPORT_CHUNK)]
    assert sum(len(p) for p in parts) == len(items)
    assert parts[0] == list(range(_IMPORT_CHUNK))
    assert parts[-1] == list(range(4_000, 5_001))


def test_chunk_sizes_fit_schema_caps() -> None:
    assert _IMPORT_CHUNK <= 20_000
    assert _EXISTING_KEYS_CHUNK <= 10_000


def test_import_row_payload_drops_raw() -> None:
    payload = _import_row_payload(
        {"feed_type": "parking_congo", "ledger_id": "L1", "_raw": ["a", "b"]}
    )
    assert "_raw" not in payload
    assert payload["ledger_id"] == "L1"
