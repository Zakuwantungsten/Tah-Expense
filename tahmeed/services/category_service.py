import re
from typing import List

from bson import ObjectId

from tahmeed.models.category import Category
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, get_all_pages


def item_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _category(document: dict) -> Category:
    return Category.from_doc(desktop_document(document))


async def get_all_categories(include_inactive: bool = False) -> List[Category]:
    documents = await get_all_pages(
        "v1/categories", params={"include_inactive": include_inactive}
    )
    return sorted(
        (_category(document) for document in documents),
        key=lambda category: category.name,
    )


async def list_categories(
    *,
    search: str = "",
    include_inactive: bool = False,
    limit: int = 100,
    skip: int = 0,
) -> List[Category]:
    page = await api_client.request(
        "GET",
        "v1/categories",
        params={
            "search": search,
            "include_inactive": include_inactive,
            "limit": limit,
            "offset": max(0, skip),
        },
    )
    return [_category(document) for document in page["items"]]


async def count_categories(
    *, search: str = "", include_inactive: bool = False
) -> int:
    page = await api_client.request(
        "GET",
        "v1/categories",
        params={
            "search": search,
            "include_inactive": include_inactive,
            "limit": 1,
        },
    )
    return int(page["total"])


async def get_sidebar_categories() -> List[Category]:
    categories = await get_all_categories()
    return sorted(
        (category for category in categories if category.show_in_sidebar),
        key=lambda category: (category.sort_order, category.sidebar_label.lower()),
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
    sort_order: int = 0,
    lock_description: bool = False,
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
            "sort_order": sort_order,
            "requires_receipt": requires_receipt,
            "requires_truck": requires_truck,
            "lock_description": lock_description,
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
    sort_order: int = 0,
    lock_description: bool = False,
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
        sort_order,
        lock_description,
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
