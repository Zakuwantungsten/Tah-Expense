"""Phase 8.2 — feeds API (toll-plaza proof)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import current_user
from app.import_ops import reset_import_index_cache
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
                exists = key in doc
                if bool(expected["$exists"]) != exists and "$ne" not in expected:
                    if expected.get("$ne") is None and actual is None:
                        pass
                    elif not expected["$exists"] and key not in doc:
                        pass
                    else:
                        return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected or "$lt" in expected or "$lte" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lt" in expected and actual >= expected["$lt"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
            if "$regex" in expected:
                import re

                flags = re.I if "i" in str(expected.get("$options", "")) else 0
                if actual is None or re.search(
                    expected["$regex"], str(actual), flags
                ) is None:
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

    def sort(self, *_a: object, **_k: object) -> "FakeCursor":
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


class FakeFeeds:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])
        self.indexes: list = []

    async def create_index(self, *args: object, **kwargs: object) -> str:
        self.indexes.append((args, kwargs))
        return "ok"

    def find(self, query: dict, projection: dict | None = None) -> FakeCursor:
        matched = [dict(d) for d in self.documents if _matches(d, query)]
        if projection:
            keep = {k for k, v in projection.items() if v}
            keep.add("_id")
            matched = [{k: d[k] for k in keep if k in d} for d in matched]
        return FakeCursor(matched)

    async def count_documents(self, query: dict) -> int:
        return sum(1 for d in self.documents if _matches(d, query))

    async def distinct(self, field: str, query: dict) -> list:
        vals = []
        for d in self.documents:
            if _matches(d, query) and field in d:
                vals.append(d[field])
        return vals

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

    async def delete_many(self, query: dict) -> SimpleNamespace:
        keep = []
        deleted = 0
        for doc in self.documents:
            if _matches(doc, query):
                deleted += 1
            else:
                keep.append(doc)
        self.documents = keep
        return SimpleNamespace(deleted_count=deleted)

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.documents:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$facet" in stage:
                facet = stage["$facet"]
                out: dict = {}
                for name, subpipe in facet.items():
                    subset = list(docs)
                    for sub in subpipe:
                        if "$group" in sub:
                            g = sub["$group"]
                            if g.get("_id") is None:
                                count = len(subset)
                                subset = [
                                    {
                                        "_id": None,
                                        "count": count,
                                        "ltrs": sum(
                                            float(d.get("ltrs") or 0)
                                            for d in subset
                                        ),
                                        "total_amount": sum(
                                            float(d.get("ltrs") or 0)
                                            * float(d.get("price_per_ltr") or 0)
                                            for d in subset
                                        ),
                                    }
                                ] if count else []
                            else:
                                buckets: dict = {}
                                for d in subset:
                                    key = d.get("currency") or "USD"
                                    buckets.setdefault(
                                        key, {"_id": key, "amount": 0.0}
                                    )
                                    buckets[key]["amount"] += float(
                                        d.get("ltrs") or 0
                                    ) * float(d.get("price_per_ltr") or 0)
                                subset = list(buckets.values())
                    out[name] = subset
                docs = [out]
            elif "$project" in stage:
                proj = stage["$project"]
                if "label" in proj:
                    projected = []
                    for d in docs:
                        ul = str(d.get("upload_label") or "").strip()
                        fn = str(d.get("source_filename") or "").strip()
                        projected.append({"label": ul or fn})
                    docs = projected
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    count = len(docs)
                    if not count:
                        docs = []
                    elif any("tender_amount" in d for d in docs):
                        total = sum(
                            float(d.get("tender_amount") or 0) for d in docs
                        )
                        docs = [{"_id": None, "count": count, "total_zmw": total}]
                    elif any("debit" in d or "credit" in d for d in docs):
                        docs = [
                            {
                                "_id": None,
                                "count": count,
                                "total_debit": sum(
                                    float(d.get("debit") or 0) for d in docs
                                ),
                                "total_credit": sum(
                                    float(d.get("credit") or 0) for d in docs
                                ),
                            }
                        ]
                    elif any("ltrs" in d for d in docs):
                        docs = [
                            {
                                "_id": None,
                                "count": count,
                                "ltrs": sum(float(d.get("ltrs") or 0) for d in docs),
                                "total_amount": sum(
                                    float(d.get("ltrs") or 0)
                                    * float(d.get("price_per_ltr") or 0)
                                    for d in docs
                                ),
                            }
                        ]
                    elif any("amount_usd" in d for d in docs):
                        money_in = sum(
                            float(d.get("amount_usd") or 0)
                            for d in docs
                            if float(d.get("amount_usd") or 0) < 0
                        )
                        money_out = sum(
                            float(d.get("amount_usd") or 0)
                            for d in docs
                            if float(d.get("amount_usd") or 0) > 0
                        )
                        docs = [
                            {
                                "_id": None,
                                "count": count,
                                "money_in": money_in,
                                "money_out": money_out,
                            }
                        ]
                    elif any("total_invoice" in d for d in docs):
                        docs = [
                            {
                                "_id": None,
                                "count": count,
                                "total_tahmeed": sum(
                                    float(d.get("total_tahmeed") or 0) for d in docs
                                ),
                                "total_invoice": sum(
                                    float(d.get("total_invoice") or 0) for d in docs
                                ),
                                "total_variance": sum(
                                    float(d.get("variance") or 0) for d in docs
                                ),
                            }
                        ]
                    elif any("amount" in d for d in docs):
                        total = sum(float(d.get("amount") or 0) for d in docs)
                        docs = [{"_id": None, "count": count, "amount": total}]
                    else:
                        docs = [{"_id": None, "count": count}]
                elif gid == "$label":
                    labels = sorted(
                        {
                            str(d.get("label") or "")
                            for d in docs
                            if d.get("label")
                        }
                    )
                    docs = [{"_id": lab} for lab in labels]
                elif isinstance(gid, dict) and "$year" in gid:
                    years = sorted(
                        {
                            d["transaction_date"].year
                            for d in docs
                            if isinstance(d.get("transaction_date"), datetime)
                        }
                    )
                    docs = [{"_id": y} for y in years]
                elif gid == "$upload_id":
                    buckets: dict[str, dict] = {}
                    for d in docs:
                        key = str(d.get("upload_id") or "")
                        if not key:
                            continue
                        b = buckets.setdefault(
                            key,
                            {
                                "_id": key,
                                "source_filename": d.get("source_filename"),
                                "import_date": d.get("import_date"),
                                "record_count": 0,
                                "total_zmw": 0.0,
                                "min_toll_date": d.get("toll_date"),
                                "max_toll_date": d.get("toll_date"),
                            },
                        )
                        b["record_count"] += 1
                        b["total_zmw"] += float(d.get("tender_amount") or 0)
                    docs = list(buckets.values())
            elif "$sort" in stage:
                continue
        return FakeCursor(docs)


class FakeOperationEvents:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> SimpleNamespace:
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=ObjectId())


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    feeds: FakeFeeds,
    *,
    separate: FakeFeeds | None = None,
    skipped: FakeFeeds | None = None,
    recon: FakeFeeds | None = None,
):
    reset_import_index_cache()
    database = SimpleNamespace(
        db=SimpleNamespace(
            imported_feeds=feeds,
            separate_expenses=separate or FakeFeeds(),
            skipped_import_rows=skipped or FakeFeeds(),
            reconciliation_entries=recon or FakeFeeds(),
            transactions=FakeFeeds(),
            operation_events=FakeOperationEvents(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct", "role": "accountant"}


def _sample() -> list[dict]:
    return [
        {
            "_id": ObjectId(),
            "feed_type": "toll_plaza",
            "upload_id": "up-1",
            "source_filename": "toll.xlsx",
            "vehicle_reg": "T103 DVL",
            "toll_plaza": "NAKONDE",
            "receipt_no": "R1",
            "tender_amount": 50.0,
            "transaction_date": datetime(2026, 3, 10),
            "toll_date": "10 Mar 2026",
            "import_date": datetime(2026, 3, 11),
        },
        {
            "_id": ObjectId(),
            "feed_type": "toll_plaza",
            "upload_id": "up-1",
            "source_filename": "toll.xlsx",
            "vehicle_reg": "T200 ABC",
            "tender_amount": 25.0,
            "transaction_date": datetime(2026, 3, 12),
            "import_date": datetime(2026, 3, 11),
        },
    ]


def test_invalid_feed() -> None:
    app = _app(FakeFeeds())
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        r = client.get("/v1/feeds/nope/records")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_feed"


def test_toll_records_count_totals_years() -> None:
    app = _app(FakeFeeds(_sample()))
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        records = client.get("/v1/feeds/toll-plaza/records")
        count = client.get("/v1/feeds/toll-plaza/records/count")
        totals = client.get("/v1/feeds/toll-plaza/totals")
        years = client.get("/v1/feeds/toll-plaza/years")

    assert records.status_code == 200
    assert len(records.json()) == 2
    assert count.json()["count"] == 2
    assert totals.json()["total_zmw"] == 75.0
    assert 2026 in years.json()["items"]


def test_toll_uploads_and_delete() -> None:
    feeds = FakeFeeds(_sample())
    app = _app(feeds)
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        uploads = client.get("/v1/feeds/toll-plaza/uploads")
        recs = client.get("/v1/feeds/toll-plaza/uploads/up-1/records")
        cnt = client.get("/v1/feeds/toll-plaza/uploads/up-1/records/count")
        tot = client.get("/v1/feeds/toll-plaza/uploads/up-1/totals")
        deleted = client.delete("/v1/feeds/toll-plaza/uploads/up-1")

    assert uploads.status_code == 200
    assert uploads.json()[0]["_id"] == "up-1"
    assert uploads.json()[0]["record_count"] == 2
    assert len(recs.json()) == 2
    assert cnt.json()["count"] == 2
    assert tot.json()["total_zmw"] == 75.0
    assert deleted.json()["deleted"] == 2
    assert feeds.documents == []


def test_existing_keys_and_import() -> None:
    feeds = FakeFeeds(_sample())
    app = _app(feeds)
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        keys = client.post(
            "/v1/feeds/toll-plaza/existing-keys",
            json={"keys": ["R1", "MISSING"]},
        )
        created = client.post(
            "/v1/feeds/toll-plaza/import",
            json={
                "rows": [
                    {
                        "feed_type": "toll_plaza",
                        "upload_id": "up-2",
                        "source_filename": "new.xlsx",
                        "vehicle_reg": "t 1 aa",
                        "toll_date": "01 Apr 2026",
                        "tender_amount": "10",
                        "receipt_no": "R99",
                    }
                ]
            },
        )

    assert keys.status_code == 200
    assert "R1" in keys.json()["keys"]
    assert "MISSING" not in keys.json()["keys"]
    assert created.status_code == 201
    assert created.json()["inserted"] == 1
    assert any(d.get("receipt_no") == "R99" for d in feeds.documents)
    new = next(d for d in feeds.documents if d.get("receipt_no") == "R99")
    assert new["feed_type"] == "toll_plaza"
    assert new["vehicle_reg"] == "T 1 AA"
    assert isinstance(new.get("transaction_date"), datetime)


def test_kimvi_and_afritrack() -> None:
    kimvi_docs = [
        {
            "_id": ObjectId(),
            "expense_type": "ahmed_kimvi",
            "upload_id": "k-1",
            "sheet_label": "SHEET1",
            "truck_no": "T1",
            "amount_usd": -50.0,
            "expense_date": datetime(2026, 9, 1),
            "import_date": datetime(2026, 9, 2),
        },
        {
            "_id": ObjectId(),
            "expense_type": "ahmed_kimvi",
            "upload_id": "k-1",
            "amount_usd": 20.0,
            "expense_date": datetime(2026, 9, 3),
            "import_date": datetime(2026, 9, 2),
        },
    ]
    afr_docs = [
        {
            "_id": ObjectId(),
            "feed_type": "afritrack",
            "upload_id": "af-1",
            "truck": "T9",
            "total_tahmeed": 1.0,
            "total_invoice": 2.0,
            "variance": 1.0,
            "import_date": datetime(2026, 2, 1),
        }
    ]
    reset_import_index_cache()
    database = SimpleNamespace(
        db=SimpleNamespace(
            imported_feeds=FakeFeeds(afr_docs),
            separate_expenses=FakeFeeds(kimvi_docs),
            transactions=FakeFeeds(),
            operation_events=FakeOperationEvents(),
        )
    )
    app = create_app(settings=settings(), database=database)
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        kimvi = client.get("/v1/feeds/kimvi/records")
        kimvi_tot = client.get("/v1/feeds/kimvi/totals")
        sheets = client.post(
            "/v1/feeds/kimvi/sheet-labels",
            json={"labels": ["SHEET1", "OTHER"]},
        )
        afr = client.get("/v1/feeds/afritrack/records")
        afr_tot = client.get("/v1/feeds/afritrack/totals")

    assert kimvi.status_code == 200
    assert len(kimvi.json()) == 2
    assert kimvi_tot.json()["balance_usd"] == -30.0
    assert sheets.json()["labels"] == ["SHEET1"]
    assert afr.status_code == 200
    assert afr_tot.json()["total_invoice"] == 2.0


def test_insurance_and_diesel() -> None:
    docs = [
        {
            "_id": ObjectId(),
            "feed_type": "comesa",
            "name": "Acme",
            "truck_reg": "T1 AA",
            "dedup_id": "C1",
            "premium": 100,
            "vat": 18,
            "total_premium": 118,
            "month": "JAN 2026",
        },
        {
            "_id": ObjectId(),
            "feed_type": "diesel_infinity",
            "upload_id": "d-1",
            "source_filename": "fuel.xlsx",
            "upload_label": "Station A",
            "truck_no": "T9 ZZ",
            "ltrs": 10,
            "price_per_ltr": 5,
            "transaction_date": datetime(2026, 8, 1),
            "import_date": datetime(2026, 8, 2),
            "content_hash": "dh1",
        },
    ]
    app = _app(FakeFeeds(docs))
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        ins = client.get("/v1/feeds/comesa/records", params={"month": "JAN"})
        ins_tot = client.get("/v1/feeds/comesa/totals")
        keys = client.post(
            "/v1/feeds/comesa/existing-keys", json={"keys": ["C1", "X"]}
        )
        diesel = client.get("/v1/feeds/diesel-infinity/records")
        d_tot = client.get("/v1/feeds/diesel-infinity/totals")
        labels = client.get("/v1/feeds/diesel-infinity/file-labels")
        dup = client.post(
            "/v1/feeds/diesel-infinity/duplicate-check",
            json={"content_hash": "dh1", "source_filename": "fuel.xlsx"},
        )

    assert ins.status_code == 200
    assert len(ins.json()) == 1
    assert ins_tot.json()["count"] == 1
    assert keys.json()["keys"] == ["C1"]
    assert diesel.status_code == 200
    assert d_tot.json()["ltrs"] == 10.0
    assert "Station A" in labels.json()["items"]
    assert dup.json()["matches"]


def test_rahntech_and_zambia_records() -> None:
    docs = [
        {
            "_id": ObjectId(),
            "feed_type": "rahntech",
            "upload_id": "rt-1",
            "truck_number": "T1 AA",
            "sales_date": "01 Jun 2026",
            "transaction_date": datetime(2026, 6, 1),
            "import_date": datetime(2026, 6, 2),
        },
        {
            "_id": ObjectId(),
            "feed_type": "zambia_parking",
            "upload_id": "zp-1",
            "sheet_label": "WEEK 1",
            "plate_num": "T2 BB",
            "debit": 10.0,
            "credit": 30.0,
            "content_hash": "abc",
            "transaction_date": datetime(2026, 7, 1),
            "import_date": datetime(2026, 7, 2),
        },
    ]
    app = _app(FakeFeeds(docs))
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        rt = client.get("/v1/feeds/rahntech/records")
        rt_tot = client.get("/v1/feeds/rahntech/totals")
        zp = client.get("/v1/feeds/zambia-parking/records")
        zp_tot = client.get("/v1/feeds/zambia-parking/totals")
        sheets = client.post(
            "/v1/feeds/zambia-parking/existing-sheet-labels",
            json={"labels": ["WEEK 1", "WEEK 9"]},
        )
        hashes = client.post(
            "/v1/feeds/zambia-parking/content-hash-matches",
            json={"content_hash": "abc"},
        )

    assert rt.status_code == 200
    assert len(rt.json()) == 1
    assert rt_tot.json()["count"] == 1
    assert zp.status_code == 200
    assert zp_tot.json()["balance_zmw"] == 20.0
    assert sheets.json()["labels"] == ["WEEK 1"]
    assert hashes.json()["matches"][0]["upload_id"] == "zp-1"


def test_parking_congo_records_and_deposits() -> None:
    docs = [
        {
            "_id": ObjectId(),
            "feed_type": "parking_congo",
            "upload_id": "pc-1",
            "source_filename": "park.xlsx",
            "vehicle_no": "T1 AA",
            "amount": 40.0,
            "transaction_date": datetime(2026, 5, 1),
            "payment_date": "01 May 2026",
            "import_date": datetime(2026, 5, 2),
            "is_deposit": False,
        },
        {
            "_id": ObjectId(),
            "feed_type": "parking_congo",
            "upload_id": "pc-1",
            "amount": 100.0,
            "transaction_type": "deposit",
            "is_deposit": True,
            "ledger_id": "L1",
            "transaction_date": datetime(2026, 5, 3),
            "import_date": datetime(2026, 5, 2),
        },
    ]
    feeds = FakeFeeds(docs)
    app = _app(feeds)
    app.dependency_overrides[current_user] = _manager
    with TestClient(app) as client:
        records = client.get("/v1/feeds/parking-congo/records")
        totals = client.get("/v1/feeds/parking-congo/totals")
        deposits = client.get("/v1/feeds/parking-congo/deposits")
        dep_count = client.get("/v1/feeds/parking-congo/deposits/count")
        dep_tot = client.get("/v1/feeds/parking-congo/deposits/totals")
        uploads = client.get("/v1/feeds/parking-congo/uploads")

    assert records.status_code == 200
    assert len(records.json()) == 2
    assert totals.json()["amount"] == 140.0
    assert deposits.status_code == 200
    assert len(deposits.json()) == 1
    assert dep_count.json()["count"] == 1
    assert dep_tot.json()["amount"] == 100.0
    assert uploads.status_code == 200
    assert uploads.json()[0]["record_count"] == 2


def test_cashier_forbidden() -> None:
    app = _app(FakeFeeds())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "c",
        "role": "cashier",
    }
    with TestClient(app) as client:
        r = client.get("/v1/feeds/toll-plaza/records")
    assert r.status_code == 403


def test_skipped_import_rows_lifecycle() -> None:
    skipped = FakeFeeds()
    feeds = FakeFeeds()
    separate = FakeFeeds()
    app = _app(feeds, separate=separate, skipped=skipped)
    app.dependency_overrides[current_user] = _manager

    with TestClient(app) as client:
        created = client.post(
            "/v1/feeds/skipped",
            json={
                "rows": [
                    {
                        "feed_key": "toll_plaza",
                        "truck_field": "vehicle_reg",
                        "truck_value": "BAD",
                        "target_upload_id": "up-skip",
                        "source_filename": "toll.xlsx",
                        "source_row": 3,
                        "reason": "not_in_fleet",
                        "original_truck": "BAD",
                        "record": {
                            "feed_type": "toll_plaza",
                            "vehicle_reg": "BAD",
                            "receipt_no": "RS1",
                            "tender_amount": 12,
                            "toll_date": "01 Apr 2026",
                        },
                    },
                    {
                        "feed_key": "ahmed_kimvi",
                        "truck_field": "truck_no",
                        "truck_value": "X1",
                        "target_upload_id": "k-skip",
                        "source_row": 1,
                        "reason": "not_in_fleet",
                        "record": {
                            "truck_no": "X1",
                            "amount_usd": 5,
                            "date_str": "01 Sep 2026",
                        },
                    },
                ]
            },
        )
        assert created.status_code == 201
        assert created.json()["inserted"] == 2

        listed = client.get(
            "/v1/feeds/skipped",
            params={"feed_key": "toll_plaza"},
        )
        counted = client.get(
            "/v1/feeds/skipped/count",
            params={"feed_key": "toll_plaza"},
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert counted.json()["count"] == 1
        toll_id = listed.json()[0]["_id"]

        patched = client.patch(
            f"/v1/feeds/skipped/{toll_id}",
            json={"truck_value": "T103 DVL"},
        )
        assert patched.status_code == 200
        assert patched.json()["ok"] is True
        assert skipped.documents[0]["truck_value"] == "T103 DVL"
        assert skipped.documents[0]["record"]["vehicle_reg"] == "T103 DVL"

        kimvi_id = str(
            next(
                d["_id"]
                for d in skipped.documents
                if d.get("feed_key") == "ahmed_kimvi"
            )
        )
        reup = client.post(
            "/v1/feeds/skipped/reupload",
            json={"ids": [toll_id, kimvi_id]},
        )
        assert reup.status_code == 200
        assert reup.json()["saved"] == 2
        assert any(d.get("receipt_no") == "RS1" for d in feeds.documents)
        assert any(
            d.get("expense_type") == "ahmed_kimvi" for d in separate.documents
        )
        assert skipped.documents == []

        again = client.post(
            "/v1/feeds/skipped",
            json={
                "rows": [
                    {
                        "feed_key": "toll_plaza",
                        "truck_field": "vehicle_reg",
                        "truck_value": "Z",
                        "target_upload_id": "up-del",
                        "source_row": 9,
                        "record": {"feed_type": "toll_plaza"},
                    }
                ]
            },
        )
        del_id = str(skipped.documents[0]["_id"])
        deleted = client.post(
            "/v1/feeds/skipped/delete",
            json={"ids": [del_id]},
        )
        assert again.status_code == 201
        assert deleted.json()["deleted"] == 1
        assert skipped.documents == []
