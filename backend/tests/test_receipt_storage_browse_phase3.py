"""Phase 3 — indexed storage tree / objects browse."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.receipt_ops import (
    ensure_receipt_workspace,
    list_storage_objects,
    list_storage_tree,
    normalize_storage_prefix,
    store_receipt_file_bytes,
)


class FakeColl:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for doc in self.documents:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict) -> None:
        self.documents.append(dict(doc))

    def find(self, query: dict, projection: dict | None = None):
        class _C:
            def __init__(self, docs: list[dict]) -> None:
                self._docs = docs

            def sort(self, *a, **k):  # noqa: ANN002, ANN003
                return self

            def limit(self, n: int):
                self._docs = self._docs[:n]
                return self

            async def to_list(self, length=None):  # noqa: ANN001
                return list(self._docs)

        # Minimal matchers used by browse enrichment
        docs = list(self.documents)
        if "$in" in str(query):
            if "_id" in query and isinstance(query["_id"], dict) and "$in" in query["_id"]:
                ids = set(query["_id"]["$in"])
                docs = [d for d in docs if d.get("_id") in ids]
            elif "stored_path" in query and isinstance(query["stored_path"], dict):
                keys = set(query["stored_path"].get("$in") or [])
                docs = [d for d in docs if d.get("stored_path") in keys]
            elif "$or" in query:
                keys: set[str] = set()
                for clause in query["$or"]:
                    for field in ("stored_path", "quarantine_path"):
                        if field in clause and isinstance(clause[field], dict):
                            keys.update(clause[field].get("$in") or [])
                    if "materialized_paths" in clause and isinstance(
                        clause["materialized_paths"], dict
                    ):
                        keys.update(clause["materialized_paths"].get("$in") or [])
                if keys:
                    filtered = []
                    for d in docs:
                        if d.get("stored_path") in keys or d.get("quarantine_path") in keys:
                            filtered.append(d)
                            continue
                        for mp in d.get("materialized_paths") or []:
                            if mp in keys:
                                filtered.append(d)
                                break
                    docs = filtered
        return _C(docs)


def _db() -> SimpleNamespace:
    return SimpleNamespace(receipt_files=FakeColl())


def test_normalize_prefix_jail() -> None:
    assert normalize_storage_prefix("files/") == "files"
    assert normalize_storage_prefix("approved/2024/01") == "approved/2024/01"
    with pytest.raises(ValueError):
        normalize_storage_prefix("../etc")
    with pytest.raises(ValueError):
        normalize_storage_prefix("secrets")


@pytest.mark.asyncio
async def test_tree_and_objects_for_files(tmp_path: Path) -> None:
    db = _db()
    ensure_receipt_workspace(tmp_path)
    meta = await store_receipt_file_bytes(
        db,
        tmp_path,
        filename="slip.jpg",
        content=b"\xff\xd8\xff\xe0jpeg-bytes",
        source="desktop",
    )
    tree = await list_storage_tree(db, tmp_path, prefix="")
    names = {f["name"] for f in tree["folders"]}
    assert names == {"files", "approved", "quarantine", "parked"}

    files_tree = await list_storage_tree(db, tmp_path, prefix="files")
    assert files_tree["folders"] == []

    objects = await list_storage_objects(db, tmp_path, prefix="files")
    assert objects["total"] >= 1
    match = next(i for i in objects["items"] if i["file_id"] == meta["id"])
    assert match["indexed"] is True
    assert "slip" in match["name"].lower() or match["key"].endswith(
        Path(meta["stored_path"]).name
    )
