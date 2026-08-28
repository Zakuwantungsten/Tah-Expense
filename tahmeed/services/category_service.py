import re
from typing import List, Optional

from bson import ObjectId

from tahmeed.models.category import Category
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, get_all_pages


def item_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _category(document: dict) -> Category:
    return Category.from_doc(desktop_document(document))


def _apply_supplier_filter(
    categories: List[Category],
    is_supplier: Optional[bool],
) -> List[Category]:
    """Client-side guard — keeps tabs correct even if the API omits the filter."""
    if is_supplier is True:
        return [category for category in categories if category.is_supplier]
    if is_supplier is False:
        return [category for category in categories if not category.is_supplier]
    return categories


def sort_payment_targets(categories: List[Category]) -> List[Category]:
    """Expense items first, then suppliers — same order in table Item and map dialogs."""
    items = sorted(
        [category for category in categories if not category.is_supplier],
        key=lambda category: category.name.lower(),
    )
    suppliers = sorted(
        [category for category in categories if category.is_supplier],
        key=lambda category: category.name.lower(),
    )
    return items + suppliers


async def get_payment_target_categories(
    include_inactive: bool = False,
) -> List[Category]:
    """All selectable payment targets for the table Item column and description maps."""
    categories = await get_all_categories(include_inactive=include_inactive)
    return sort_payment_targets(categories)


async def get_all_categories(
    include_inactive: bool = False,
    *,
    is_supplier: Optional[bool] = None,
) -> List[Category]:
    params: dict = {"include_inactive": include_inactive}
    if is_supplier is not None:
        params["is_supplier"] = is_supplier
    documents = await get_all_pages("v1/categories", params=params)
    categories = sorted(
        (_category(document) for document in documents),
        key=lambda category: category.name,
    )
    return _apply_supplier_filter(categories, is_supplier)


async def list_categories(
    *,
    search: str = "",
    include_inactive: bool = False,
    is_supplier: Optional[bool] = None,
    limit: int = 100,
    skip: int = 0,
) -> List[Category]:
    params: dict = {
        "search": search,
        "include_inactive": include_inactive,
        "limit": limit,
        "offset": max(0, skip),
    }
    if is_supplier is not None:
        params["is_supplier"] = is_supplier
    page = await api_client.request("GET", "v1/categories", params=params)
    items = [_category(document) for document in page["items"]]
    return _apply_supplier_filter(items, is_supplier)


async def count_categories(
    *,
    search: str = "",
    include_inactive: bool = False,
    is_supplier: Optional[bool] = None,
) -> int:
    if is_supplier is not None:
        categories = await get_all_categories(
            include_inactive=include_inactive,
            is_supplier=is_supplier,
        )
        if search:
            q = search.strip().lower()
            categories = [
                category for category in categories
                if q in (category.name or "").lower()
                or q in (category.description or "").lower()
            ]
        return len(categories)
    params: dict = {
        "search": search,
        "include_inactive": include_inactive,
        "limit": 1,
    }
    page = await api_client.request("GET", "v1/categories", params=params)
    return int(page["total"])


def _sorted_sidebar(categories: List[Category]) -> List[Category]:
    return sorted(
        categories,
        key=lambda category: (category.sort_order, category.sidebar_label.lower()),
    )


async def get_sidebar_categories() -> List[Category]:
    """Items shown in the accountant ITEMS sidebar (excludes suppliers)."""
    categories = await get_all_categories(is_supplier=False)
    return _sorted_sidebar(
        [category for category in categories if category.show_in_sidebar]
    )


async def get_cashier_sidebar_categories() -> List[Category]:
    """Items shown in the cashier ITEMS sidebar (excludes suppliers)."""
    categories = await get_all_categories(is_supplier=False)
    return _sorted_sidebar(
        [category for category in categories if category.show_in_cashier_sidebar]
    )


async def _create_category(
    path: str,
    name: str,
    color: str,
    requires_receipt: bool,
    requires_truck: bool,
    description: str = "",
    icon: str = "mdi.tag-outline",
    sidebar_name: str = "",
    show_in_sidebar: bool = False,
    show_in_cashier_sidebar: bool = False,
    sort_order: int = 0,
    lock_description: bool = False,
    restrict_in_pdf: bool = False,
    restrict_in_excel: bool = False,
    is_supplier: bool = False,
) -> Category:
    document = await api_client.request(
        "POST",
        path,
        json={
            "name": name,
            "description": description,
            "color": color,
            "icon": icon,
            "sidebar_name": sidebar_name,
            "show_in_sidebar": show_in_sidebar,
            "show_in_cashier_sidebar": show_in_cashier_sidebar,
            "sort_order": sort_order,
            "requires_receipt": requires_receipt,
            "requires_truck": requires_truck,
            "lock_description": lock_description,
            "restrict_in_pdf": restrict_in_pdf,
            "restrict_in_excel": restrict_in_excel,
            "is_supplier": is_supplier,
        },
    )
    return _category(document)


async def create_category(
    name: str,
    color: str,
    requires_receipt: bool,
    requires_truck: bool,
    description: str = "",
    icon: str = "mdi.tag-outline",
    sidebar_name: str = "",
    show_in_sidebar: bool = False,
    show_in_cashier_sidebar: bool = False,
    sort_order: int = 0,
    lock_description: bool = False,
    restrict_in_pdf: bool = False,
    restrict_in_excel: bool = False,
    is_supplier: bool = False,
) -> Category:
    return await _create_category(
        "v1/categories",
        name,
        color,
        requires_receipt,
        requires_truck,
        description,
        icon,
        sidebar_name,
        show_in_sidebar,
        show_in_cashier_sidebar,
        sort_order,
        lock_description,
        restrict_in_pdf,
        restrict_in_excel,
        is_supplier,
    )


async def create_cashier_category(
    name: str,
    color: str,
    requires_receipt: bool,
    requires_truck: bool,
    description: str = "",
    icon: str = "mdi.tag-outline",
    sidebar_name: str = "",
    show_in_sidebar: bool = False,
    show_in_cashier_sidebar: bool = False,
    sort_order: int = 0,
    lock_description: bool = False,
) -> Category:
    return await _create_category(
        "v1/categories/cashier-create",
        name,
        color,
        requires_receipt,
        requires_truck,
        description,
        icon,
        sidebar_name,
        show_in_sidebar,
        show_in_cashier_sidebar,
        sort_order,
        lock_description,
    )


async def update_category(cat_id: ObjectId, **fields) -> None:
    await api_client.request(
        "PATCH", f"v1/categories/{cat_id}", json={"values": fields}
    )


async def toggle_category(cat_id: ObjectId, active: bool) -> None:
    await update_category(cat_id, active=active)


async def delete_category(cat_id: ObjectId) -> None:
    await api_client.request("DELETE", f"v1/categories/{cat_id}")
