"""Payee list import helpers (QuickBooks OTHERNAME IIF / JSON rows)."""

from __future__ import annotations

from typing import Any, Sequence


def normalize_payee_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().upper().split())


def normalize_payee_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return cleaned payee fields, or ``None`` when the name is blank."""
    name = normalize_payee_text(row.get("name"))
    if not name:
        return None
    ref_num = str(row.get("ref_num") or "").strip()
    currency = normalize_payee_text(row.get("currency")) or "TZS"
    hidden_raw = str(row.get("hidden") or "N").strip().upper()
    active = hidden_raw not in {"Y", "1", "TRUE"}
    if "active" in row:
        active = bool(row.get("active"))
    return {
        "name": name,
        "ref_num": ref_num,
        "currency": currency,
        "active": active,
    }


def prepare_payee_docs(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by name (last wins) and build ``payees`` documents."""
    by_name: dict[str, dict[str, Any]] = {}
    for raw in rows:
        cleaned = normalize_payee_row(raw if isinstance(raw, dict) else {})
        if cleaned is None:
            continue
        by_name[cleaned["name"]] = cleaned
    return list(by_name.values())


async def wipe_payees(db: Any) -> int:
    result = await db.payees.delete_many({})
    return int(result.deleted_count)


async def commit_payee_list(
    db: Any,
    rows: Sequence[dict[str, Any]],
    *,
    replace_existing: bool = True,
) -> dict[str, Any]:
    """Replace (optional) and upsert payee documents."""
    docs = prepare_payee_docs(rows)
    if not docs:
        raise ValueError("No payees found in the Payee List payload.")

    removed = 0
    if replace_existing:
        removed = await wipe_payees(db)
        if docs:
            await db.payees.insert_many(docs, ordered=False)
        return {
            "imported": len(docs),
            "removed": removed,
            "updated": 0,
            "replace_existing": True,
        }

    updated = 0
    imported = 0
    for doc in docs:
        existing = await db.payees.find_one({"name": doc["name"]})
        await db.payees.update_one(
            {"name": doc["name"]},
            {"$set": doc},
            upsert=True,
        )
        if existing is None:
            imported += 1
        else:
            updated += 1
    return {
        "imported": imported,
        "removed": 0,
        "updated": updated,
        "replace_existing": False,
    }
