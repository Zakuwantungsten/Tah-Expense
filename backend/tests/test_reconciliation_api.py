"""Phase 6.2–6.3 — reconciliation rows / all-records / uploads endpoints."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.main import create_app


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$and":
            if not all(_matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                exists = key in doc and doc.get(key) is not None
                want = bool(expected["$exists"])
                # treat None as missing when $ne None is also used
                if "$ne" in expected and expected["$ne"] is None:
                    exists = key in doc and doc.get(key) is not None
                if exists != want and "$ne" not in expected:
                    return False
            if "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
            if "$nin" in expected:
                if actual in expected["$nin"]:
                    return False
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            if "$gte" in expected or "$lt" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lt" in expected and actual >= expected["$lt"]:
                    return False
            if "$regex" in expected:
                import re

                flags = re.I if "i" in str(expected.get("$options", "")) else 0
                if actual is None or re.search(expected["$regex"], str(actual), flags) is None:
                    return False
            continue
        if actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = list(documents)
        self._skip = 0
        self._limit: int | None = None

    def sort(self, *_args: object, **_kwargs: object) -> "FakeCursor":
        return self

    def skip(self, value: int) -> "FakeCursor":
        self._skip = value
        return self

    def limit(self, value: int) -> "FakeCursor":
        self._limit = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        docs = list(self.documents)[self._skip :]
        if self._limit is not None:
            docs = docs[: self._limit]
        if length is None:
            return docs
        return docs[:length]


class FakeStations:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(doc) for doc in self.documents if _matches(doc, query)]
        )

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, document: dict) -> SimpleNamespace:
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def insert_many(
        self, documents: list[dict], ordered: bool = True
    ) -> SimpleNamespace:
        ids = []
        for document in documents:
            stored = dict(document)
            stored.setdefault("_id", ObjectId())
            self.documents.append(stored)
            ids.append(stored["_id"])
        return SimpleNamespace(inserted_ids=ids)


class FakeEntries:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        matched = [dict(doc) for doc in self.documents if _matches(doc, query)]
        if _projection:
            keep = {k for k, v in _projection.items() if v}
            if "_id" not in _projection or _projection.get("_id"):
                keep.add("_id")
            matched = [{k: d[k] for k in keep if k in d} for d in matched]
        return FakeCursor(matched)

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.documents if _matches(doc, query))

    async def insert_many(
        self, documents: list[dict], ordered: bool = True
    ) -> SimpleNamespace:
        ids = []
        for document in documents:
            stored = dict(document)
            stored.setdefault("_id", ObjectId())
            self.documents.append(stored)
            ids.append(stored["_id"])
        return SimpleNamespace(inserted_ids=ids)

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_many(self, query: dict) -> SimpleNamespace:
        keep: list[dict] = []
        deleted = 0
        for doc in self.documents:
            if _matches(doc, query):
                deleted += 1
            else:
                keep.append(doc)
        self.documents = keep
        return SimpleNamespace(deleted_count=deleted)

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$sort" in stage:
                continue
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    count = len(docs)
                    charge = sum(float(d.get("charge") or 0) for d in docs)
                    confirmed = sum(
                        float(d.get("charge") or 0)
                        for d in docs
                        if d.get("confirmed")
                    )
                    disputed = sum(1 for d in docs if d.get("disputed"))
                    docs = (
                        [
                            {
                                "_id": None,
                                "count": count,
                                "charge_total": charge,
                                "confirmed_total": confirmed,
                                "disputed_count": disputed,
                            }
                        ]
                        if count
                        else []
                    )
                elif isinstance(gid, dict) and "$year" in str(gid):
                    field = (
                        "t1_date" if "t1_date" in str(gid) else "import_date"
                    )
                    years = sorted(
                        {
                            d[field].year
                            for d in docs
                            if isinstance(d.get(field), datetime)
                        }
                    )
                    docs = [{"_id": y} for y in years]
                elif gid == "$upload_id":
                    buckets: dict[str, dict] = {}
                    for d in docs:
                        key = str(d.get("upload_id") or "")
                        if not key:
                            continue
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": key,
                                "source_filename": d.get("source_filename"),
                                "import_date": d.get("import_date"),
                                "schedule_period": d.get("schedule_period"),
                                "record_count": 0,
                                "total_charge": 0.0,
                                "stations": [],
                            },
                        )
                        bucket["record_count"] += 1
                        bucket["total_charge"] += float(d.get("charge") or 0)
                        bucket["stations"].append(d.get("station"))
                    docs = list(buckets.values())
                elif gid == "$station":
                    buckets = {}
                    for d in docs:
                        key = str(d.get("station") or "")
                        if not key:
                            continue
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": key,
                                "first_idx": d.get("source_index"),
                                "first_id": d.get("_id"),
                            },
                        )
                        idx = d.get("source_index")
                        if isinstance(idx, (int, float)):
                            cur = bucket.get("first_idx")
                            if not isinstance(cur, (int, float)) or idx < cur:
                                bucket["first_idx"] = idx
                                bucket["first_id"] = d.get("_id")
                    docs = list(buckets.values())
        return FakeCursor(docs)


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    entries: FakeEntries,
    stations: FakeStations | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            reconciliation_entries=entries,
            recon_stations=stations or FakeStations(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct", "role": "accountant"}


def _sample_docs() -> list[dict]:
    return [
        {
            "_id": ObjectId(),
            "entity": "sm_burhani",
            "table": "bonds",
            "station": "nakonde",
            "sr_no": 1,
            "charge": 100.0,
            "confirmed": True,
            "disputed": False,
            "t1_date": datetime(2026, 4, 10),
            "import_date": datetime(2026, 4, 11),
            "prn_number": "P1",
            "truck_and_trailer": "T100 DVL",
            "upload_id": "up-a",
            "source_filename": "bonds.xlsx",
            "source_index": 0,
        },
        {
            "_id": ObjectId(),
            "entity": "sm_burhani",
            "table": "bonds",
            "station": "sakania",
            "sr_no": 2,
            "charge": 50.0,
            "confirmed": False,
            "disputed": True,
            "t1_date": datetime(2025, 1, 5),
            "import_date": datetime(2025, 1, 6),
            "prn_number": "P2",
            "truck_and_trailer": "T200 ABC",
            "upload_id": "up-a",
            "source_filename": "bonds.xlsx",
            "source_index": 1,
        },
        {
            "_id": ObjectId(),
            "entity": "sm_burhani",
            "table": "rpa_schedule",
            "station": "nakonde",
            "sr_no": 1,
            "charge": 10.0,
            "confirmed": False,
            "disputed": False,
            "t1_date": datetime(2026, 4, 1),
            "import_date": datetime(2026, 4, 2),
            "upload_id": "up-rpa",
            "source_index": 0,
        },
    ]


def test_list_and_count_rows() -> None:
    app = _app(FakeEntries(_sample_docs()))
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        listed = client.get(
            "/v1/reconciliation/bonds/rows",
            params={"station": "nakonde"},
        )
        counted = client.get(
            "/v1/reconciliation/bonds/rows/count",
            params={"station": "nakonde"},
        )
        totals = client.get(
            "/v1/reconciliation/bonds/totals",
            params={"station": "nakonde"},
        )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["station"] == "nakonde"
    assert counted.json()["total"] == 1
    assert totals.json()["count"] == 1
    assert totals.json()["invoiced"] == 100.0
    assert totals.json()["confirmed"] == 100.0


def test_all_records_year_filter() -> None:
    app = _app(FakeEntries(_sample_docs()))
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        response = client.get(
            "/v1/reconciliation/bonds/all",
            params={"year": 2026, "month": 4},
        )
        count = client.get(
            "/v1/reconciliation/bonds/all/count",
            params={"year": 2026, "month": 4},
        )
        totals = client.get(
            "/v1/reconciliation/bonds/all/totals",
            params={"year": 2026},
        )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert count.json()["total"] == 1
    assert totals.json()["count"] == 1


def test_available_years() -> None:
    app = _app(FakeEntries(_sample_docs()))
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        response = client.get("/v1/reconciliation/bonds/years")

    assert response.status_code == 200
    assert response.json()["items"] == [2026, 2025]


def test_invalid_table() -> None:
    app = _app(FakeEntries())
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        response = client.get("/v1/reconciliation/nope/rows")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_table"


def test_forbidden_for_cashier() -> None:
    app = _app(FakeEntries(_sample_docs()))
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "c",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get("/v1/reconciliation/bonds/rows")

    assert response.status_code == 403


def test_list_uploads_and_stations() -> None:
    app = _app(FakeEntries(_sample_docs()))
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        uploads = client.get("/v1/reconciliation/bonds/uploads")
        stations = client.get(
            "/v1/reconciliation/bonds/uploads/up-a/stations"
        )

    assert uploads.status_code == 200
    items = uploads.json()["items"]
    assert len(items) == 1
    assert items[0]["upload_id"] == "up-a"
    assert items[0]["record_count"] == 2
    assert set(items[0]["stations"]) == {"nakonde", "sakania"}
    assert stations.status_code == 200
    assert [s["slug"] for s in stations.json()["items"]] == [
        "nakonde",
        "sakania",
    ]


def test_upload_records_count_totals_and_delete() -> None:
    entries = FakeEntries(_sample_docs())
    app = _app(entries)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        records = client.get(
            "/v1/reconciliation/bonds/uploads/up-a/records",
            params={"station": "nakonde"},
        )
        count = client.get(
            "/v1/reconciliation/bonds/uploads/up-a/records/count"
        )
        totals = client.get(
            "/v1/reconciliation/bonds/uploads/up-a/totals"
        )
        deleted = client.delete("/v1/reconciliation/bonds/uploads/up-a")

    assert records.status_code == 200
    assert len(records.json()["items"]) == 1
    assert count.json()["total"] == 2
    assert totals.json()["invoiced"] == 150.0
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 2
    assert all(d.get("upload_id") != "up-a" for d in entries.documents)


def test_existing_keys_save_rows_and_patch() -> None:
    entries = FakeEntries(_sample_docs())
    stations = FakeStations()
    app = _app(entries, stations)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        entries.documents[0]["dedup_key"] = "P1|E1"
        existing = client.post(
            "/v1/reconciliation/bonds/existing-keys",
            json={"keys": ["P1|E1", "MISSING|X"]},
        )
        assert existing.status_code == 200
        assert existing.json()["keys"] == ["P1|E1"]

        created = client.post(
            "/v1/reconciliation/bonds/rows",
            json={
                "rows": [
                    {
                        "station": "nakonde",
                        "prn_number": "P9",
                        "entry_reg_no": "E9",
                        "charge": 12.5,
                        "upload_id": "up-new",
                    }
                ]
            },
        )
        assert created.status_code == 201
        assert created.json()["inserted"] == 1
        assert any(d.get("dedup_key") == "P9|E9" for d in entries.documents)
        assert any(s.get("slug") == "nakonde" for s in stations.documents)

        row_id = str(
            next(d["_id"] for d in entries.documents if d.get("dedup_key") == "P9|E9")
        )
        patched = client.patch(
            f"/v1/reconciliation/bonds/rows/{row_id}",
            json={"confirmed": True, "disputed": False},
        )
        assert patched.status_code == 200
        assert patched.json()["updated"] is True
        saved = next(d for d in entries.documents if str(d["_id"]) == row_id)
        assert saved["confirmed"] is True


def test_stations_seed_ensure_and_add() -> None:
    stations = FakeStations()
    app = _app(FakeEntries(), stations)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        seeded = client.get("/v1/reconciliation/bonds/stations")
        assert seeded.status_code == 200
        slugs = [s["slug"] for s in seeded.json()["items"]]
        assert slugs == ["nakonde", "kasumbalesa", "sakania"]
        assert len(stations.documents) == 3

        # Second call should not re-seed.
        again = client.get("/v1/reconciliation/bonds/stations")
        assert len(again.json()["items"]) == 3
        assert len(stations.documents) == 3

        ensured = client.post(
            "/v1/reconciliation/rpa_schedule/stations/ensure",
            json={"slugs": ["new_post", "all", "new_post"]},
        )
        assert ensured.status_code == 200
        assert any(s["slug"] == "new_post" for s in stations.documents)

        created = client.post(
            "/v1/reconciliation/rpa_schedule/stations",
            json={"name": "Tunduma", "border_post": "TZ–ZM"},
        )
        assert created.status_code == 201
        assert created.json()["slug"] == "tunduma"
        assert created.json()["border_post"] == "TZ–ZM"

        # Reactivate existing
        created2 = client.post(
            "/v1/reconciliation/rpa_schedule/stations",
            json={"name": "Tunduma"},
        )
        assert created2.status_code == 201
        assert created2.json()["slug"] == "tunduma"
        assert sum(1 for s in stations.documents if s["slug"] == "tunduma") == 1
