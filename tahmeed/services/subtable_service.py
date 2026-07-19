from typing import List, Optional

from bson import ObjectId

from tahmeed.models.sub_table import SubTable
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, get_all_pages


def _subtable(document: dict) -> SubTable:
    return SubTable.from_doc(desktop_document(document))


async def get_subtables(
    parent_key: str, include_inactive: bool = False
) -> List[SubTable]:
    documents = await get_all_pages(
        "v1/subtables",
        params={
            "parent_key": parent_key,
            "include_inactive": include_inactive,
        },
    )
    return [_subtable(document) for document in documents]


async def create_subtable(
    parent_key: str,
    parent_category: str,
    name: str,
    match: Optional[str] = None,
) -> SubTable:
    document = await api_client.request(
        "POST",
        "v1/subtables",
        json={
            "parent_key": parent_key,
            "parent_category": parent_category,
            "name": name,
            "match": match or name,
        },
    )
    return _subtable(document)


async def update_subtable(sub_id: ObjectId, **fields) -> None:
    await api_client.request(
        "PATCH", f"v1/subtables/{sub_id}", json={"values": fields}
    )


async def archive_subtable(sub_id: ObjectId, active: bool = False) -> None:
    await update_subtable(sub_id, active=active)


async def delete_subtable(sub_id: ObjectId) -> None:
    await api_client.request("DELETE", f"v1/subtables/{sub_id}")
