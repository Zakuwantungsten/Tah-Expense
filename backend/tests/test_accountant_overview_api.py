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
        if key == "$nor":
            if any(_matches(doc, clause) for clause in expected):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                exists = key in doc and doc.get(key) is not None
                if bool(expected["$exists"]) != exists:
                    return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$regex" in expected:
                import re

                pattern = expected["$regex"]
                flags = re.I if "i" in str(expected.get("$options", "")) else 0
                if actual is None or re.search(pattern, str(actual), flags) is None:
                    return False
                continue
            if "$gte" in expected or "$lte" in expected or "$lt" in expected:
                if actual is None:
                    return False
                if "$gte" in expected and actual < expected["$gte"]:
                    return False
                if "$lte" in expected and actual > expected["$lte"]:
                    return False
                if "$lt" in expected and actual >= expected["$lt"]:
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


def _currency_sum(docs: list[dict], codes: set[str], absolute: bool = False) -> float:
    total = 0.0
    for d in docs:
        cur = str(d.get("currency") or "").upper()
        if cur in codes:
            amt = float(d.get("amount") or 0)
            total += abs(amt) if absolute else amt
    return total


class FakeTransactions:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, projection: dict | None = None) -> FakeCursor:
        docs = [dict(doc) for doc in self.documents if _matches(doc, query)]
        if projection:
            projected = []
            for doc in docs:
                row = {k: doc.get(k) for k in projection if k != "_id"}
                if projection.get("_id", 1):
                    row["_id"] = doc.get("_id")
                projected.append(row)
            docs = projected
        return FakeCursor(docs)

    async def count_documents(self, query: dict, limit: int | None = None) -> int:
        total = sum(1 for doc in self.documents if _matches(doc, query))
        if limit is not None:
            return min(total, limit)
        return total

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$addFields" in stage:
                if "_cat_l" in stage["$addFields"]:
                    for d in docs:
                        d["_cat_l"] = str(d.get("category_name") or "").lower()
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    row: dict = {"_id": None}
                    if "count" in group:
                        row["count"] = len(docs)
                    if "tzs_total" in group:
                        row["tzs_total"] = _currency_sum(
                            docs, {"TZS", "TSH", "TZ"}
                        )
                    if "usd_total" in group:
                        row["usd_total"] = _currency_sum(docs, {"USD"})
                    if "zmw_total" in group:
                        row["zmw_total"] = _currency_sum(
                            docs, {"ZMW", "ZMB", "ZK"}
                        )
                    if "tzs" in group:
                        row["tzs"] = _currency_sum(
                            docs, {"TZS", "TSH", "TZ"}, absolute=True
                        )
                    if "usd" in group:
                        row["usd"] = _currency_sum(docs, {"USD"}, absolute=True)
                    if "zmw" in group:
                        row["zmw"] = _currency_sum(
                            docs, {"ZMW", "ZMB", "ZK"}, absolute=True
                        )
                    if "verified" in group:
                        row["verified"] = sum(
                            1 for d in docs if d.get("verified")
                        )
                    if "total" in group:
                        row["total"] = len(docs)
                    docs = [row]
                elif (
                    isinstance(gid, dict)
                    and "person" in gid
                    and ("month" in gid or "$month" in str(gid))
                ):
                    # Meetings: group by person + year + month
                    person_expr = str(gid.get("person") or "")
                    person_field = "approver"
                    if "ownership" in person_expr:
                        person_field = "ownership"
                    elif "approver" in person_expr:
                        person_field = "approver"
                    buckets_pm: dict[tuple, dict] = {}
                    for d in docs:
                        day = d.get("date")
                        if not isinstance(day, datetime):
                            continue
                        person = str(d.get(person_field) or "")
                        key = (person, day.year, day.month)
                        bucket = buckets_pm.setdefault(
                            key,
                            {
                                "_id": {
                                    "person": person,
                                    "year": day.year,
                                    "month": day.month,
                                },
                                "tzs": 0.0,
                                "usd": 0.0,
                                "zmw": 0.0,
                                "count": 0,
                            },
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = abs(float(d.get("amount") or 0))
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                        bucket["count"] += 1
                    docs = list(buckets_pm.values())
                elif isinstance(gid, dict) and "$month" in str(gid):
                    buckets: dict[int, dict] = {}
                    for d in docs:
                        day = d.get("date")
                        if not isinstance(day, datetime):
                            continue
                        month = day.month
                        bucket = buckets.setdefault(
                            month,
                            {
                                "_id": month,
                                "tzs": 0.0,
                                "usd": 0.0,
                                "zmw": 0.0,
                                "count": 0,
                            },
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = float(d.get("amount") or 0)
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                        bucket["count"] += 1
                    docs = list(buckets.values())
                elif "receipt_status" in str(gid) or gid == "$receipt_status":
                    buckets2: dict[str | None, int] = {}
                    for d in docs:
                        key = d.get("receipt_status")
                        buckets2[key] = buckets2.get(key, 0) + 1
                    docs = [{"_id": k, "count": v} for k, v in buckets2.items()]
                elif isinstance(gid, dict) and "$ifNull" in gid:
                    # Meetings: group by ownership / approver person
                    expr = gid.get("$ifNull") or []
                    field_ref = str(expr[0] if expr else "")
                    person_field = "approver"
                    if "ownership" in field_ref:
                        person_field = "ownership"
                    elif "approver" in field_ref:
                        person_field = "approver"
                    buckets_p: dict[str, dict] = {}
                    for d in docs:
                        person = str(d.get(person_field) or "")
                        bucket = buckets_p.setdefault(
                            person,
                            {
                                "_id": person,
                                "tzs": 0.0,
                                "usd": 0.0,
                                "zmw": 0.0,
                                "count": 0,
                                "receipt_statuses": [],
                            },
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = abs(float(d.get("amount") or 0))
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                        bucket["count"] += 1
                        if "receipt_statuses" in group:
                            bucket["receipt_statuses"].append(
                                d.get("receipt_status")
                            )
                    docs = list(buckets_p.values())
                else:
                    # category breakdown
                    buckets3: dict[str, dict] = {}
                    for d in docs:
                        name = d.get("category_name") or "Uncategorised"
                        bucket = buckets3.setdefault(
                            name,
                            {"_id": name, "tzs": 0.0, "usd": 0.0, "zmw": 0.0},
                        )
                        cur = str(d.get("currency") or "").upper()
                        amt = abs(float(d.get("amount") or 0))
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                    docs = list(buckets3.values())
            elif "$sort" in stage:
                pass
        return FakeCursor(docs)


def _parse_money(value: object) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _feed_amount(doc: dict) -> float:
    if "tender_amount" in doc:
        return abs(_parse_money(doc.get("tender_amount")))
    if "debit" in doc:
        return abs(_parse_money(doc.get("debit")))
    if "total_invoice" in doc:
        return abs(_parse_money(doc.get("total_invoice")))
    if "total_premium" in doc:
        return abs(_parse_money(doc.get("total_premium")))
    if "premium" in doc:
        return abs(_parse_money(doc.get("premium")))
    if "ltrs" in doc or "price_per_ltr" in doc:
        return abs(
            _parse_money(doc.get("ltrs")) * _parse_money(doc.get("price_per_ltr"))
        )
    return abs(_parse_money(doc.get("amount")))


class FakeImportedFeeds:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    row: dict = {
                        "_id": None,
                        "total": sum(_feed_amount(d) for d in docs),
                        "count": len(docs),
                        "liters": sum(float(d.get("ltrs") or 0) for d in docs),
                    }
                    if any(k in group for k in ("tzs", "usd", "zmw")):
                        row["tzs"] = 0.0
                        row["usd"] = 0.0
                        row["zmw"] = 0.0
                        for d in docs:
                            amt = _feed_amount(d)
                            cur = str(d.get("currency") or "").upper()
                            if cur in {"TZS", "TSH", "TZ"}:
                                row["tzs"] += amt
                            elif cur == "USD":
                                row["usd"] += amt
                            elif cur in {"ZMW", "ZMB", "ZK"}:
                                row["zmw"] += amt
                    docs = [row]
                elif (
                    isinstance(gid, dict)
                    and "month" in gid
                    and "currency" in gid
                ):
                    # Diesel stations: group by calendar month + currency.
                    buckets_mc: dict[tuple, float] = {}
                    for d in docs:
                        day = d.get("transaction_date")
                        if not isinstance(day, datetime):
                            continue
                        key = (day.month, str(d.get("currency") or "").upper())
                        buckets_mc[key] = buckets_mc.get(key, 0.0) + _feed_amount(d)
                    docs = [
                        {
                            "_id": {"month": month, "currency": cur},
                            "total": total,
                        }
                        for (month, cur), total in buckets_mc.items()
                    ]
                elif (
                    isinstance(gid, dict)
                    and "feed" in gid
                    and "currency" in gid
                ):
                    # Station spend: group by feed_type + currency.
                    buckets_fc: dict[tuple, float] = {}
                    for d in docs:
                        key = (
                            d.get("feed_type"),
                            str(d.get("currency") or "").upper(),
                        )
                        buckets_fc[key] = buckets_fc.get(key, 0.0) + _feed_amount(d)
                    docs = [
                        {
                            "_id": {"feed": feed, "currency": cur},
                            "total": total,
                        }
                        for (feed, cur), total in buckets_fc.items()
                    ]
                elif isinstance(gid, dict) and "$toUpper" in str(gid):
                    buckets_cur: dict[str, float] = {}
                    for d in docs:
                        cur = str(d.get("currency") or "").upper()
                        buckets_cur[cur] = buckets_cur.get(cur, 0.0) + _feed_amount(d)
                    docs = [
                        {"_id": cur, "total": total}
                        for cur, total in buckets_cur.items()
                    ]
                elif isinstance(gid, dict) and "feed" in str(gid):
                    buckets: dict[tuple, dict] = {}
                    for d in docs:
                        day = d.get("transaction_date")
                        if not isinstance(day, datetime):
                            continue
                        key = (
                            d.get("feed_type"),
                            day.year,
                            day.month,
                            day.day,
                        )
                        bucket = buckets.setdefault(
                            key,
                            {
                                "_id": {
                                    "feed": key[0],
                                    "y": key[1],
                                    "m": key[2],
                                    "d": key[3],
                                },
                                "liters": 0.0,
                            },
                        )
                        bucket["liters"] += float(d.get("ltrs") or 0)
                    docs = list(buckets.values())
                elif isinstance(gid, dict) and "$month" in str(gid):
                    buckets2: dict[int, dict] = {}
                    for d in docs:
                        day = (
                            d.get("transaction_date")
                            or d.get("import_date")
                            or d.get("payment_date")
                            or d.get("toll_date")
                            or d.get("sales_date")
                        )
                        if isinstance(day, str):
                            try:
                                day = datetime.strptime(day, "%d %b %Y")
                            except ValueError:
                                day = None
                        if not isinstance(day, datetime):
                            continue
                        amt = _feed_amount(d)
                        month = day.month
                        bucket = buckets2.setdefault(
                            month,
                            {
                                "_id": month,
                                "total": 0.0,
                                "tzs": 0.0,
                                "usd": 0.0,
                                "zmw": 0.0,
                                "liters": 0.0,
                                "count": 0,
                            },
                        )
                        cur = str(d.get("currency") or "").upper()
                        if cur in {"TZS", "TSH", "TZ"}:
                            bucket["tzs"] += amt
                        elif cur == "USD":
                            bucket["usd"] += amt
                        elif cur in {"ZMW", "ZMB", "ZK"}:
                            bucket["zmw"] += amt
                        bucket["total"] += amt
                        bucket["liters"] += float(d.get("ltrs") or 0)
                        bucket["count"] += 1
                    docs = list(buckets2.values())
                else:
                    docs = []
        return FakeCursor(docs)


class FakeReconciliationEntries:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    docs = [
                        {
                            "_id": None,
                            "total": sum(
                                abs(float(d.get("charge") or 0)) for d in docs
                            ),
                            "count": len(docs),
                        }
                    ]
                elif isinstance(gid, dict) and "$month" in str(gid):
                    buckets: dict[int, dict] = {}
                    for d in docs:
                        day = d.get("t1_date")
                        if not isinstance(day, datetime):
                            continue
                        month = day.month
                        bucket = buckets.setdefault(
                            month,
                            {"_id": month, "total": 0.0, "count": 0},
                        )
                        bucket["total"] += abs(float(d.get("charge") or 0))
                        bucket["count"] += 1
                    docs = list(buckets.values())
                else:
                    docs = []
        return FakeCursor(docs)


class FakeSeparateExpenses:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def aggregate(self, pipeline: list) -> FakeCursor:
        docs = [dict(d) for d in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _matches(d, stage["$match"])]
            elif "$group" in stage:
                group = stage["$group"]
                gid = group.get("_id")
                if gid is None:
                    row = {
                        "_id": None,
                        "total": sum(
                            abs(float(d.get("amount_usd") or 0)) for d in docs
                        ),
                        "count": len(docs),
                        "tzs": 0.0,
                        "usd": sum(
                            abs(float(d.get("amount_usd") or 0)) for d in docs
                        ),
                        "zmw": 0.0,
                    }
                    docs = [row]
                else:
                    buckets: dict[int, dict] = {}
                    for d in docs:
                        day = d.get("expense_date")
                        if not isinstance(day, datetime):
                            continue
                        amt = abs(float(d.get("amount_usd") or 0))
                        if amt <= 0:
                            continue
                        month = day.month
                        bucket = buckets.setdefault(
                            month,
                            {
                                "_id": month,
                                "total": 0.0,
                                "tzs": 0.0,
                                "usd": 0.0,
                                "zmw": 0.0,
                                "count": 0,
                            },
                        )
                        bucket["usd"] += amt
                        bucket["total"] += amt
                        bucket["count"] += 1
                    docs = list(buckets.values())
        return FakeCursor(docs)


class FakeCategories:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = list(documents or [])

    def find(self, query: dict, _projection: dict | None = None) -> FakeCursor:
        return FakeCursor(
            [dict(d) for d in self.documents if _matches(d, query)]
        )


class FakeSystemSettings:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = {doc["key"]: dict(doc) for doc in (documents or [])}

    async def find_one(self, query: dict) -> dict | None:
        key = query.get("key")
        doc = self.documents.get(key)
        return dict(doc) if doc else None


def settings() -> Settings:
    return Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
    )


def _app(
    transactions: FakeTransactions,
    imported_feeds: FakeImportedFeeds | None = None,
    separate_expenses: FakeSeparateExpenses | None = None,
    categories: FakeCategories | None = None,
    system_settings: FakeSystemSettings | None = None,
    reconciliation_entries: FakeReconciliationEntries | None = None,
):
    database = SimpleNamespace(
        db=SimpleNamespace(
            transactions=transactions,
            imported_feeds=imported_feeds or FakeImportedFeeds(),
            separate_expenses=separate_expenses or FakeSeparateExpenses(),
            categories=categories or FakeCategories(),
            system_settings=system_settings or FakeSystemSettings(),
            reconciliation_entries=reconciliation_entries
            or FakeReconciliationEntries(),
        )
    )
    return create_app(settings=settings(), database=database)


def _manager() -> dict:
    return {"_id": ObjectId(), "username": "acct1", "role": "accountant"}


def _tx(**kwargs) -> dict:
    doc = {
        "_id": ObjectId(),
        "verified": True,
        "rejected": False,
        "deletion_requested": False,
        "trashed": False,
        "date": datetime(2026, 3, 10),
        "description": "Diesel 200 LTRS",
        "amount": 100.0,
        "currency": "TZS",
        "category_name": "Fuel",
        "item": "Fuel",
        "receipt_status": "received",
        "created_at": datetime(2026, 3, 10, 8),
    }
    doc.update(kwargs)
    return doc


def test_overview_kpis_categories_receipts_and_dashboard(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.accountant_overview_api.get_rates_to_tzs",
        lambda force_refresh=False: {
            "rates": {"TZS": 1.0, "USD": 2500.0, "ZMW": 100.0},
            "source": "test",
            "as_of": 0.0,
        },
    )
    docs = [
        _tx(),
        _tx(
            amount=50.0,
            currency="USD",
            category_name="Lube",
            receipt_status="pending",
        ),
        _tx(
            category_name="Supplier X",
            amount=200.0,
            currency="TZS",
            description="Supplier pay",
        ),
        _tx(verified=False, rejected=False, description="Pending inbox"),
        _tx(
            category_name="Diesel Cash",
            item="Diesel Cash",
            description="Cash fill 400 LTRS",
            amount=80.0,
        ),
    ]
    categories = FakeCategories(
        [{"name": "Supplier X", "is_supplier": True, "active": True}]
    )
    feeds = FakeImportedFeeds(
        [
            {
                "feed_type": "diesel_infinity",
                "transaction_date": datetime(2026, 3, 10),
                "ltrs": 100,
                "price_per_ltr": 2.0,
                "currency": "TZS",
            },
            {
                "feed_type": "toll_plaza",
                "transaction_date": datetime(2026, 2, 5),
                "tender_amount": 30.0,
            },
            {
                "feed_type": "third_party",
                "import_date": datetime(2026, 4, 1),
                "total_premium": 40.0,
            },
            {
                "feed_type": "rahntech",
                "transaction_date": datetime(2026, 5, 1),
                "amount": 12.0,
            },
            # Legacy Parking Congo row: display payment_date only (no BSON date).
            {
                "feed_type": "parking_congo",
                "payment_date": "15 Mar 2026",
                "amount": "1,250.50",
            },
        ]
    )
    separate = FakeSeparateExpenses(
        [
            {
                "expense_type": "congo_expenses",
                "expense_date": datetime(2026, 1, 15),
                "amount_usd": 25.0,
            }
        ]
    )
    recon = FakeReconciliationEntries(
        [
            {
                "entity": "sm_burhani",
                "t1_date": datetime(2026, 6, 1),
                "charge": 8.0,
            }
        ]
    )
    app = _app(
        FakeTransactions(docs),
        imported_feeds=feeds,
        separate_expenses=separate,
        categories=categories,
        reconciliation_entries=recon,
    )
    app.dependency_overrides[current_user] = lambda: _manager()

    with TestClient(app) as client:
        kpis = client.get("/v1/accountant/overview/kpis", params={"year": 2026})
        cats = client.get(
            "/v1/accountant/overview/categories", params={"year": 2026}
        )
        receipts = client.get(
            "/v1/accountant/overview/receipts", params={"year": 2026}
        )
        fuel = client.get(
            "/v1/accountant/overview/fuel-liters", params={"year": 2026}
        )
        months = client.get(
            "/v1/accountant/overview/month-totals", params={"year": 2026}
        )
        sources = client.get(
            "/v1/accountant/overview/sources", params={"year": 2026}
        )
        dash = client.get("/v1/accountant/overview", params={"year": 2026})

    assert kpis.status_code == 200
    assert kpis.json()["pending_count"] == 1
    assert kpis.json()["master_count"] == 4
    assert cats.status_code == 200
    cat_items = cats.json()["items"]
    names = {row["name"] for row in cat_items}
    # Source/tab pie: Master (incl. Diesel Cash) + separate tabs + stations.
    assert "Master Expenses" in names
    assert "Diesel Cash" not in names
    assert "Toll Plaza" in names
    assert "Congo Expenses" in names
    assert "Infinity" in names
    master_row = next(r for r in cat_items if r["name"] == "Master Expenses")
    # 100 Fuel + 50 USD ignored in tzs + 200 Supplier + 80 Diesel Cash
    assert master_row["tzs"] == 100.0 + 200.0 + 80.0
    assert master_row["usd"] == 50.0
    infinity = next(r for r in cat_items if r["name"] == "Infinity")
    assert infinity["tzs"] == 200.0  # 100 L × 2.0
    assert receipts.json()["received"] >= 1
    assert receipts.json()["pending"] >= 1
    assert fuel.status_code == 200
    assert "diesel_cash" in fuel.json()["series"]
    assert "infinity" in fuel.json()["series"]
    assert months.status_code == 200
    # Non-master add-ons: all separate tabs + diesel stations by currency.
    assert months.json()["feed_tzs_ytd"] == 200.0 + 40.0  # Infinity + Third Party
    # Congo + RahnTech + Burhani + legacy Parking Congo ("1,250.50")
    assert months.json()["feed_usd_ytd"] == 25.0 + 12.0 + 8.0 + 1250.50
    assert months.json()["feed_zmw_ytd"] == 30.0  # Toll Plaza
    assert sources.status_code == 200
    source_items = {row["key"]: row for row in sources.json()["items"]}
    # Separate tabs are ungrouped — one bar per sidebar tab.
    assert source_items["toll_plaza"]["native"]["zmw"] == 30.0
    assert source_items["congo_exp"]["native"]["usd"] == 25.0
    assert source_items["parking_congo"]["native"]["usd"] == 1250.50
    assert source_items["parking_congo"]["amount_tzs"] == 1250.50 * 2500.0
    assert "congo" not in source_items
    assert "toll_zambia" not in source_items
    assert source_items["fuel"]["native"]["tzs"] == 80.0 + 200.0
    assert source_items["congo_exp"]["amount_tzs"] == 25.0 * 2500.0
    assert dash.status_code == 200
    assert "kpis" in dash.json()
    assert "sources" in dash.json()
    # Master TZS (100+200+80) + Infinity 200 + Third Party 40
    assert dash.json()["kpis"]["total_tzs_ytd"] == 380.0 + 240.0
    # Master USD 50 + Congo 25 + RahnTech 12 + SM Burhani 8 + Parking Congo 1250.50
    assert dash.json()["kpis"]["total_usd_ytd"] == 50.0 + 45.0 + 1250.50
    # Master ZMW 0 + Toll 30
    assert dash.json()["kpis"]["total_zmw_ytd"] == 30.0
    # Monthly trend: Master USD (Mar) + legacy Parking Congo (15 Mar 2026).
    assert months.json()["months"]["3"]["usd"] == 50.0 + 1250.50
    assert dash.json()["kpis"]["supplier_tzs_ytd"] == 200.0
    assert len(dash.json()["recent"]) <= 8


def test_overview_requires_manager() -> None:
    app = _app(FakeTransactions())
    app.dependency_overrides[current_user] = lambda: {
        "_id": ObjectId(),
        "username": "cash",
        "role": "cashier",
    }

    with TestClient(app) as client:
        response = client.get(
            "/v1/accountant/overview/kpis", params={"year": 2026}
        )

    assert response.status_code == 403
