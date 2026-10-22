"""Stored sidebar badges. Reads are one document; writes adjust by delta.

The request path must not ``count_documents`` on ``transactions``. Startup and
``python -m app.cli.migrate`` rebuild the document once if it has drifted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .accountant_ops import DELETION_BASE_MATCH, pending_inbox_count_query
from .transaction_ops import DRAFT_BASE_MATCH, DRAFT_OR_PARKED_MATCH, PARKED_BASE_MATCH

logger = logging.getLogger(__name__)

BADGE_ID = "badges"
BADGE_KEYS = (
    "verify",
    "drafts",
    "parked",
    "deletion_requests",
    "restore_requests",
)
BADGE_TOUCH_FIELDS = frozenset(
    {
        "register_status",
        "verified",
        "rejected",
        "discarded",
        "deletion_requested",
        "trashed",
        "restore_requested",
    }
)
_BADGE_PROJECTION = {field: 1 for field in BADGE_TOUCH_FIELDS}
_BADGE_PROJECTION["cashier_id"] = 1

RESTORE_COUNT_MATCH: dict[str, Any] = {
    "trashed": True,
    "restore_requested": True,
}


def _not_true(doc: dict[str, Any], field: str) -> bool:
    return doc.get(field) is not True


def in_verify(doc: dict[str, Any]) -> bool:
    """Same rows as the verify inbox: submitted and not yet verified."""
    return (
        doc.get("verified") is False
        and doc.get("register_status") == "submitted"
        and _not_true(doc, "rejected")
        and _not_true(doc, "trashed")
        and _not_true(doc, "deletion_requested")
    )


def in_drafts(doc: dict[str, Any]) -> bool:
    return doc.get("register_status") == "draft" and _open_register(doc)


def in_parked(doc: dict[str, Any]) -> bool:
    return doc.get("register_status") == "parked" and _open_register(doc)


def _open_register(doc: dict[str, Any]) -> bool:
    return (
        _not_true(doc, "verified")
        and _not_true(doc, "rejected")
        and _not_true(doc, "discarded")
        and _not_true(doc, "deletion_requested")
        and _not_true(doc, "trashed")
    )


def in_deletion_requests(doc: dict[str, Any]) -> bool:
    return doc.get("deletion_requested") is True and _not_true(doc, "trashed")


def in_restore_requests(doc: dict[str, Any]) -> bool:
    return doc.get("trashed") is True and doc.get("restore_requested") is True


def membership(doc: dict[str, Any] | None) -> dict[str, int]:
    if not doc:
        return {key: 0 for key in BADGE_KEYS}
    return {
        "verify": int(in_verify(doc)),
        "drafts": int(in_drafts(doc)),
        "parked": int(in_parked(doc)),
        "deletion_requests": int(in_deletion_requests(doc)),
        "restore_requests": int(in_restore_requests(doc)),
    }


def _cashier_key(doc: dict[str, Any] | None) -> str | None:
    if not doc or doc.get("cashier_id") is None:
        return None
    return str(doc["cashier_id"])


def transition_delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Global $inc plus per-cashier draft/parked $inc for one row change."""
    old = membership(before)
    new = membership(after)
    deltas = {key: new[key] - old[key] for key in BADGE_KEYS if new[key] != old[key]}
    cashiers: dict[str, dict[str, int]] = {}

    def touch(doc: dict[str, Any] | None, sign: int) -> None:
        key = _cashier_key(doc)
        if key is None or doc is None:
            return
        bucket = cashiers.setdefault(key, {"drafts": 0, "parked": 0})
        if in_drafts(doc):
            bucket["drafts"] += sign
        if in_parked(doc):
            bucket["parked"] += sign

    touch(before, -1)
    touch(after, 1)
    cashiers = {
        key: fields
        for key, fields in cashiers.items()
        if fields["drafts"] or fields["parked"]
    }
    return deltas, cashiers


def verify_badge_total(doc: dict[str, Any] | None) -> int:
    src = doc or {}
    return (
        int(src.get("verify") or 0)
        + int(src.get("deletion_requests") or 0)
        + int(src.get("restore_requests") or 0)
    )


def drafts_badge_total(
    doc: dict[str, Any] | None,
    *,
    cashier_id: Any = None,
) -> int:
    src = doc or {}
    if cashier_id is None:
        return int(src.get("drafts") or 0) + int(src.get("parked") or 0)
    row = (src.get("cashiers") or {}).get(str(cashier_id)) or {}
    return int(row.get("drafts") or 0) + int(row.get("parked") or 0)


def badge_etag(doc: dict[str, Any] | None) -> str:
    """Quoted token for ``updated_at``. Missing stamp is ``"0"``."""
    stamp = (doc or {}).get("updated_at")
    if stamp is None:
        token = "0"
    elif hasattr(stamp, "isoformat"):
        token = stamp.isoformat()
    else:
        token = str(stamp)
    return f'"{token}"'


def badge_etag_matches(header: str | None, etag: str) -> bool:
    if not header or not etag:
        return False
    return header.strip() == etag


async def read_badges(db: Any) -> dict[str, Any]:
    counters = getattr(db, "system_counters", None)
    if counters is None or not hasattr(counters, "find_one"):
        return {}
    doc = await counters.find_one({"_id": BADGE_ID})
    return dict(doc) if doc else {}


async def adjust_badges(
    db: Any,
    deltas: dict[str, int],
    *,
    cashier_deltas: dict[str, dict[str, int]] | None = None,
) -> None:
    """$inc the stored badge document. No-op when nothing changed."""
    counters = getattr(db, "system_counters", None)
    if counters is None or not hasattr(counters, "update_one"):
        return
    inc: dict[str, int] = {}
    for key, value in deltas.items():
        amount = int(value or 0)
        if key in BADGE_KEYS and amount:
            inc[key] = amount
    for cashier_id, fields in (cashier_deltas or {}).items():
        for field in ("drafts", "parked"):
            amount = int(fields.get(field) or 0)
            if amount:
                inc[f"cashiers.{cashier_id}.{field}"] = amount
    if not inc:
        return
    await counters.update_one(
        {"_id": BADGE_ID},
        {"$inc": inc, "$set": {"updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def note_transition(
    db: Any,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    deltas, cashiers = transition_delta(before, after)
    await adjust_badges(db, deltas, cashier_deltas=cashiers)


async def note_many(
    db: Any,
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]],
) -> None:
    total: dict[str, int] = {}
    cashiers: dict[str, dict[str, int]] = {}
    for before, after in pairs:
        deltas, parts = transition_delta(before, after)
        for key, value in deltas.items():
            total[key] = total.get(key, 0) + value
        for cashier_id, fields in parts.items():
            bucket = cashiers.setdefault(cashier_id, {"drafts": 0, "parked": 0})
            bucket["drafts"] += int(fields.get("drafts") or 0)
            bucket["parked"] += int(fields.get("parked") or 0)
    await adjust_badges(db, total, cashier_deltas=cashiers)


async def note_mutations(
    db: Any,
    docs: list[dict[str, Any]],
    changes: dict[str, Any],
) -> None:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for doc in docs:
        after = dict(doc)
        after.update(changes)
        pairs.append((doc, after))
    await note_many(db, pairs)


async def load_badge_docs(db: Any, query: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Load the rows a write is about to change. None when the fake has no find."""
    transactions = getattr(db, "transactions", None)
    find = getattr(transactions, "find", None)
    if find is None:
        return None
    try:
        cursor = find(query, _BADGE_PROJECTION)
    except TypeError:
        cursor = find(query)
    if not hasattr(cursor, "to_list"):
        return None
    docs = await cursor.to_list(length=None)
    return [dict(doc) for doc in docs]


async def apply_remembered(
    db: Any,
    docs: list[dict[str, Any]] | None,
    changes: dict[str, Any] | None,
    modified: int,
) -> None:
    """Apply a delta captured before the write. ``changes is None`` means delete."""
    if not modified or not docs:
        return
    if changes is None:
        await note_many(db, [(doc, None) for doc in docs])
        return
    await note_mutations(db, docs, changes)


async def repair_badge_counters(db: Any) -> None:
    """One full count, then overwrite. Not for the request path."""
    transactions = getattr(db, "transactions", None)
    counters = getattr(db, "system_counters", None)
    if transactions is None or not hasattr(transactions, "count_documents"):
        return
    if counters is None or not hasattr(counters, "update_one"):
        return
    actual = {
        "verify": int(await transactions.count_documents(pending_inbox_count_query())),
        "drafts": int(await transactions.count_documents(dict(DRAFT_BASE_MATCH))),
        "parked": int(await transactions.count_documents(dict(PARKED_BASE_MATCH))),
        "deletion_requests": int(
            await transactions.count_documents(dict(DELETION_BASE_MATCH))
        ),
        "restore_requests": int(
            await transactions.count_documents(dict(RESTORE_COUNT_MATCH))
        ),
    }
    cashiers = await _cashier_buckets(transactions)
    previous = None
    if hasattr(counters, "find_one"):
        previous = await counters.find_one({"_id": BADGE_ID})
    if previous is not None:
        drifted = {
            key: (int(previous.get(key) or 0), actual[key])
            for key in BADGE_KEYS
            if int(previous.get(key) or 0) != actual[key]
        }
        if drifted:
            logger.warning("badge counters drifted; rewriting stored=%s", drifted)
    await counters.update_one(
        {"_id": BADGE_ID},
        {
            "$set": {
                **actual,
                "cashiers": cashiers,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


async def _cashier_buckets(transactions: Any) -> dict[str, dict[str, int]]:
    """Draft and parked totals per cashier. One pass, startup only."""
    if not hasattr(transactions, "find"):
        return {}
    try:
        cursor = transactions.find(
            dict(DRAFT_OR_PARKED_MATCH),
            {"cashier_id": 1, "register_status": 1},
        )
    except TypeError:
        cursor = transactions.find(dict(DRAFT_OR_PARKED_MATCH))
    if not hasattr(cursor, "to_list"):
        return {}
    docs = await cursor.to_list(length=None)
    buckets: dict[str, dict[str, int]] = {}
    for doc in docs:
        if doc.get("cashier_id") is None:
            continue
        key = str(doc["cashier_id"])
        row = buckets.setdefault(key, {"drafts": 0, "parked": 0})
        status = doc.get("register_status")
        if status == "draft":
            row["drafts"] += 1
        elif status == "parked":
            row["parked"] += 1
    return buckets
