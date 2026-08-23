"""Per-item export restrictions — configurable by export surface."""

from __future__ import annotations

from typing import Any, Iterable, Literal, Sequence, Set

from tahmeed.models.category import Category
from tahmeed.services.category_service import get_all_categories
from tahmeed.services.settings_service import get_setting

ExportFmt = Literal["pdf", "excel"]

# Surfaces that can honour restrict_in_pdf / restrict_in_excel (item_quick_report excluded).
EXPORT_SURFACES: dict[str, str] = {
    "cashier_register": "Cashier daily register (Excel, CSV, PDF)",
    "cashier_print": "Cashier day print (PDF)",
    "master_expenses": "Master Expenses (Excel)",
    "category_tables": "Item sidebar tables (Excel)",
    "truck_overview": "Truck Overview (Excel, PDF)",
    "diesel_cash": "Diesel Cash (Excel)",
    "skipped_trucks": "Skipped trucks (Excel, CSV)",
}

DEFAULT_EXPORT_RESTRICT_SURFACES: list[str] = [
    "cashier_register",
    "cashier_print",
    "master_expenses",
    "category_tables",
    "truck_overview",
    "diesel_cash",
]

_SETTING_KEY = "export_restrict_surfaces"


def normalize_export_surfaces(value: Any) -> list[str]:
    """Return valid surface ids from a settings value."""
    if not isinstance(value, list):
        return list(DEFAULT_EXPORT_RESTRICT_SURFACES)
    known = set(EXPORT_SURFACES)
    picked = [str(v) for v in value if str(v) in known]
    return picked


async def get_enabled_export_surfaces() -> Set[str]:
    raw = await get_setting(_SETTING_KEY)
    return set(normalize_export_surfaces(raw))


async def set_enabled_export_surfaces(surfaces: Sequence[str]) -> None:
    from tahmeed.services.settings_service import set_setting

    await set_setting(_SETTING_KEY, normalize_export_surfaces(list(surfaces)))


def restricted_names_from_categories(
    categories: Iterable[Category],
    fmt: ExportFmt,
) -> Set[str]:
    flag = "restrict_in_pdf" if fmt == "pdf" else "restrict_in_excel"
    return {
        (c.name or "").strip().lower()
        for c in categories
        if (c.name or "").strip() and getattr(c, flag, False)
    }


async def get_restricted_item_names(fmt: ExportFmt) -> Set[str]:
    categories = await get_all_categories(include_inactive=True)
    return restricted_names_from_categories(categories, fmt)


def transaction_item_name(tx: Any) -> str:
    item = getattr(tx, "item", None) or getattr(tx, "category_name", None) or ""
    return str(item).strip()


def is_item_restricted(name: str, restricted: Set[str]) -> bool:
    key = (name or "").strip().lower()
    return bool(key and key in restricted)


def filter_transactions(
    txs: Sequence[Any],
    restricted: Set[str],
) -> list:
    if not restricted:
        return list(txs)
    return [tx for tx in txs if not is_item_restricted(transaction_item_name(tx), restricted)]


def filter_register_rows(
    rows: Sequence[Sequence[Any]],
    *,
    item_col_index: int,
    restricted: Set[str],
) -> list:
    if not restricted:
        return [list(r) for r in rows]
    out: list = []
    for row in rows:
        item = ""
        if item_col_index < len(row):
            item = str(row[item_col_index] or "")
        if not is_item_restricted(item, restricted):
            out.append(list(row))
    return out


def filter_overview_rows(
    rows: Sequence[dict],
    restricted: Set[str],
) -> list:
    if not restricted:
        return list(rows)
    out: list = []
    for row in rows:
        item = str(row.get("item_name") or "")
        if not item or not is_item_restricted(item, restricted):
            out.append(row)
    return out


async def should_apply_export_restriction(surface: str, fmt: ExportFmt) -> bool:
    if surface not in EXPORT_SURFACES:
        return False
    enabled = await get_enabled_export_surfaces()
    return surface in enabled


async def filter_transactions_for_export(
    txs: Sequence[Any],
    *,
    surface: str,
    fmt: ExportFmt,
) -> list:
    if not await should_apply_export_restriction(surface, fmt):
        return list(txs)
    restricted = await get_restricted_item_names(fmt)
    return filter_transactions(txs, restricted)


async def filter_register_rows_for_export(
    rows: Sequence[Sequence[Any]],
    *,
    surface: str,
    fmt: ExportFmt,
    item_col_index: int,
) -> list:
    if not await should_apply_export_restriction(surface, fmt):
        return [list(r) for r in rows]
    restricted = await get_restricted_item_names(fmt)
    return filter_register_rows(rows, item_col_index=item_col_index, restricted=restricted)


async def filter_overview_rows_for_export(
    rows: Sequence[dict],
    *,
    surface: str,
    fmt: ExportFmt,
) -> list:
    if not await should_apply_export_restriction(surface, fmt):
        return list(rows)
    restricted = await get_restricted_item_names(fmt)
    return filter_overview_rows(rows, restricted)
