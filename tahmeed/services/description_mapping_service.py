"""Description-to-item mappings served by the FastAPI backend."""

from typing import Dict, List, Optional, Tuple

import httpx
from bson import ObjectId

from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, get_all_pages

_BULK_MUTATION_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def normalize_description(description: str) -> str:
    return " ".join((description or "").strip().upper().split())


def _mapping(document: dict) -> DescriptionMapping:
    return DescriptionMapping.from_doc(desktop_document(document))


async def list_mappings(
    *,
    search: str = "",
    limit: int = 100,
    skip: int = 0,
) -> List[DescriptionMapping]:
    page = await api_client.request(
        "GET",
        "v1/description-mappings",
        params={
            "search": search,
            "limit": max(1, min(limit, 500)),
            "offset": max(0, skip),
            "include_inactive": "true",
        },
    )
    return [_mapping(document) for document in page["items"]]


async def count_mappings(*, search: str = "") -> int:
    page = await api_client.request(
        "GET",
        "v1/description-mappings",
        params={
            "search": search,
            "limit": 1,
            "offset": 0,
            "include_inactive": "true",
        },
    )
    return int(page["total"])


async def list_all_mappings() -> List[DescriptionMapping]:
    documents = await get_all_pages("v1/description-mappings")
    mappings = [_mapping(document) for document in documents]
    return sorted(mappings, key=lambda mapping: mapping.description)


async def get_mapping(description: str) -> Optional[DescriptionMapping]:
    key = normalize_description(description)
    if not key:
        return None
    mappings = await get_mappings_for_descriptions([description])
    return mappings.get(key)


async def get_mappings_for_descriptions(
    descriptions: List[str],
) -> Dict[str, DescriptionMapping]:
    keys = {normalize_description(value) for value in descriptions}
    keys.discard("")
    if not keys:
        return {}
    documents = await get_all_pages(
        "v1/description-mappings", params={"description_keys": sorted(keys)}
    )
    return {
        document["description_key"]: _mapping(document) for document in documents
    }


async def save_mapping(
    description: str,
    category_id: ObjectId,
    category_name: str,
    *,
    payee: str = "",
) -> DescriptionMapping:
    document = await api_client.request(
        "PUT",
        "v1/description-mappings",
        json={
            "description": " ".join((description or "").strip().split()),
            "category_id": str(category_id),
            "category_name": category_name,
            "payee": " ".join((payee or "").strip().upper().split()),
        },
    )
    return _mapping(document)


async def delete_mapping(mapping_id: ObjectId | str) -> None:
    await api_client.request(
        "DELETE",
        f"v1/description-mappings/{mapping_id}",
    )


async def delete_all_mappings(*, search: str = "") -> int:
    params: dict[str, str] = {}
    text = (search or "").strip()
    if text:
        params["search"] = text
    kwargs: dict = {}
    if params:
        kwargs["params"] = params
    payload = await api_client.request(
        "DELETE",
        "v1/description-mappings",
        timeout=_BULK_MUTATION_TIMEOUT,
        **kwargs,
    )
    return int(payload["deleted_count"])


async def delete_mappings_bulk(mapping_ids: List[ObjectId | str]) -> int:
    ids = [str(item_id) for item_id in mapping_ids if item_id]
    if not ids:
        return 0
    payload = await api_client.request(
        "POST",
        "v1/description-mappings/bulk-delete",
        json={"ids": ids},
        timeout=_BULK_MUTATION_TIMEOUT,
    )
    return int(payload["deleted_count"])


async def resolve_mapping_for_description(
    description: str,
    cache: Optional[Dict[str, DescriptionMapping]] = None,
) -> Optional[DescriptionMapping]:
    """Return the saved description map (item + payee), if any."""
    key = normalize_description(description)
    if not key:
        return None
    mapping = (cache or {}).get(key)
    if mapping is None:
        mapping = await get_mapping(description)
    return mapping


async def resolve_category_for_description(
    description: str,
    cache: Optional[Dict[str, DescriptionMapping]] = None,
) -> Optional[Tuple[ObjectId, str]]:
    mapping = await resolve_mapping_for_description(description, cache)
    if mapping is None:
        return None
    return mapping.category_id, mapping.category_name


def transaction_needs_item(item: str = "", category_name: Optional[str] = None) -> bool:
    """True when a transaction still needs an item/category assigned."""
    return not (item or "").strip() and not (category_name or "").strip()


def transaction_needs_payee(payee: str = "") -> bool:
    """True when a transaction still needs a payee assigned."""
    return not (payee or "").strip()


def transaction_needs_mapping(
    item: str = "",
    category_name: Optional[str] = None,
    payee: str = "",
) -> bool:
    """True when Item and/or Payee are still missing (Verify / Day Issues gate)."""
    return transaction_needs_item(item, category_name) or transaction_needs_payee(payee)
