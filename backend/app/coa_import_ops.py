"""Chart of Accounts import helpers (Phase 10.1)."""

from __future__ import annotations

from typing import Any, Sequence

COA_PALETTE = (
    "#4A90D9",
    "#16A34A",
    "#D97706",
    "#7C3AED",
    "#DC2626",
    "#0891B2",
    "#DB2777",
    "#65A30D",
    "#EA580C",
    "#6366F1",
)

_COA_ROW_KEYS = (
    "name",
    "ref_num",
    "account_type",
    "coa_description",
    "account_number",
    "currency",
)


def normalize_coa_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def normalize_coa_row(row: dict[str, Any]) -> dict[str, str] | None:
    """Return cleaned CoA fields, or ``None`` when the account name is blank."""
    name = normalize_coa_text(row.get("name"))
    if not name:
        return None
    return {
        "name": name,
        "ref_num": normalize_coa_text(row.get("ref_num")),
        "account_type": normalize_coa_text(row.get("account_type")),
        "coa_description": normalize_coa_text(row.get("coa_description")),
        "account_number": normalize_coa_text(row.get("account_number")),
        "currency": normalize_coa_text(row.get("currency")),
    }


def build_coa_category_doc(row: dict[str, str], *, sort_order: int) -> dict[str, Any]:
    """Build one ``categories`` document from a normalized CoA row."""
    coa_description = row.get("coa_description") or ""
    return {
        "name": row["name"],
        "description": coa_description,
        "color": COA_PALETTE[sort_order % len(COA_PALETTE)],
        "icon": "mdi.tag-outline",
        "sidebar_name": "",
        "show_in_sidebar": False,
        "show_in_cashier_sidebar": False,
        "sort_order": sort_order,
        "requires_receipt": False,
        "requires_truck": False,
        "lock_description": False,
        "restrict_in_pdf": False,
        "restrict_in_excel": False,
        "is_supplier": False,
        "active": True,
        "account_type": row.get("account_type") or "",
        "ref_num": row.get("ref_num") or "",
        "account_number": row.get("account_number") or "",
        "currency": row.get("currency") or "",
        "coa_description": coa_description,
    }


def prepare_coa_category_docs(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for raw in rows:
        cleaned = normalize_coa_row(raw if isinstance(raw, dict) else {})
        if cleaned is None:
            continue
        docs.append(build_coa_category_doc(cleaned, sort_order=len(docs)))
    return docs


async def wipe_catalog_collections(db: Any) -> dict[str, int]:
    """Delete categories, subtables, keyword rules, and description mappings."""
    removed = {
        "categories": 0,
        "subtables": 0,
        "keyword_rules": 0,
        "mappings": 0,
    }
    r = await db.categories.delete_many({})
    removed["categories"] = int(r.deleted_count)
    r = await db.category_subtables.delete_many({})
    removed["subtables"] = int(r.deleted_count)
    r = await db.keyword_rules.delete_many({})
    removed["keyword_rules"] = int(r.deleted_count)
    r = await db.description_mappings.delete_many({})
    removed["mappings"] = int(r.deleted_count)
    return removed


async def commit_chart_of_accounts(
    db: Any,
    rows: Sequence[dict[str, Any]],
    *,
    replace_existing: bool = True,
) -> dict[str, Any]:
    """Replace (optional) catalog collections and insert CoA category docs."""
    docs = prepare_coa_category_docs(rows)
    if not docs:
        raise ValueError("No accounts found in the Chart of Accounts payload.")

    removed = {
        "categories": 0,
        "subtables": 0,
        "keyword_rules": 0,
        "mappings": 0,
    }
    if replace_existing:
        removed = await wipe_catalog_collections(db)

    if docs:
        await db.categories.insert_many(docs, ordered=False)

    return {
        "imported": len(docs),
        "removed": removed,
        "replace_existing": bool(replace_existing),
    }
