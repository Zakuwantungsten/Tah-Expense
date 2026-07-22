"""Description-to-item mappings served by the FastAPI backend."""

from typing import Dict, List, Optional, Tuple

from bson import ObjectId

from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, get_all_pages


def normalize_description(description: str) -> str:
    return " ".join((description or "").strip().upper().split())


def _mapping(document: dict) -> DescriptionMapping:
    return DescriptionMapping.from_doc(desktop_document(document))


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
) -> DescriptionMapping:
    document = await api_client.request(
        "PUT",
        "v1/description-mappings",
        json={
            "description": " ".join((description or "").strip().split()),
            "category_id": str(category_id),
            "category_name": category_name,
        },
    )
    return _mapping(document)


async def delete_all_mappings() -> int:
    payload = await api_client.request("DELETE", "v1/description-mappings")
    return int(payload["deleted_count"])


async def resolve_category_for_description(
    description: str,
    cache: Optional[Dict[str, DescriptionMapping]] = None,
) -> Optional[Tuple[ObjectId, str]]:
    key = normalize_description(description)
    if not key:
        return None
    mapping = (cache or {}).get(key)
    if mapping is None:
        mapping = await get_mapping(description)
    if mapping is None:
        return None
    return mapping.category_id, mapping.category_name


def transaction_needs_item(item: str = "", category_name: Optional[str] = None) -> bool:
    """True when a transaction still needs an item/category assigned."""
    return not (item or "").strip() and not (category_name or "").strip()
