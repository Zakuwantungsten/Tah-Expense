"""Payee list import helpers + IIF-shaped rows."""

from __future__ import annotations

from backend.app.payee_import_ops import (
    commit_payee_list,
    normalize_payee_row,
    prepare_payee_docs,
)


def test_normalize_payee_row_uppercases_name() -> None:
    row = normalize_payee_row({"name": "  Spare Parts ", "currency": "tzs", "ref_num": "1"})
    assert row == {
        "name": "SPARE PARTS",
        "ref_num": "1",
        "currency": "TZS",
        "active": True,
    }


def test_normalize_skips_blank() -> None:
    assert normalize_payee_row({"name": "   "}) is None


def test_prepare_dedupes_by_name() -> None:
    docs = prepare_payee_docs(
        [
            {"name": "Parking Dar", "currency": "TZS"},
            {"name": "PARKING DAR", "currency": "USD", "ref_num": "9"},
        ]
    )
    assert len(docs) == 1
    assert docs[0]["name"] == "PARKING DAR"
    assert docs[0]["currency"] == "USD"
    assert docs[0]["ref_num"] == "9"


class _FakePayees:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def delete_many(self, _query):
        n = len(self.docs)
        self.docs.clear()

        class _R:
            deleted_count = n

        return _R()

    async def insert_many(self, docs, ordered=False):
        self.docs.extend(docs)

    async def find_one(self, query):
        name = query.get("name")
        for doc in self.docs:
            if doc.get("name") == name:
                return dict(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        name = query.get("name")
        payload = update.get("$set", {})
        for doc in self.docs:
            if doc.get("name") == name:
                doc.update(payload)
                return
        if upsert:
            self.docs.append(dict(payload))


class _FakeDb:
    def __init__(self) -> None:
        self.payees = _FakePayees()


async def test_commit_replace() -> None:
    db = _FakeDb()
    db.payees.docs = [{"name": "OLD", "active": True}]
    result = await commit_payee_list(
        db,
        [{"name": "Spare Parts"}, {"name": "Mileage"}],
        replace_existing=True,
    )
    assert result["imported"] == 2
    assert result["removed"] == 1
    assert {d["name"] for d in db.payees.docs} == {"SPARE PARTS", "MILEAGE"}


async def test_commit_merge() -> None:
    db = _FakeDb()
    db.payees.docs = [{"name": "SPARE PARTS", "active": True, "currency": "TZS"}]
    result = await commit_payee_list(
        db,
        [{"name": "Spare Parts", "currency": "USD"}, {"name": "Fine"}],
        replace_existing=False,
    )
    assert result["imported"] == 1
    assert result["updated"] == 1
    spare = next(d for d in db.payees.docs if d["name"] == "SPARE PARTS")
    assert spare["currency"] == "USD"
