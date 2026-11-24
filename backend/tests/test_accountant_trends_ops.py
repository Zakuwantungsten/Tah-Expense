"""Unit tests for Trends helpers (Phase 0–9)."""

from datetime import date, datetime

from app.accountant_trends_ops import (
    DIESEL_STATION_BY_KEY,
    ENTITY_COMPARE_MAX,
    TREND_COMPARE_MAX,
    TREND_COMPARE_COLORS,
    ENTITY_FAMILIES,
    ENTITY_FAMILY_KEYS,
    build_data_table_month_trend,
    build_data_table_rows,
    build_truck_fleet_table,
    iter_year_months,
    month_trend_label,
    entity_cache_clear,
    entity_cache_get,
    entity_cache_key,
    entity_cache_set,
    entity_set_label,
    PHASE3_DIESEL_KEYS,
    PHASE4_DIESEL_CASH_KEY,
    PHASE4_DIESEL_CASH_KEYS,
    PHASE5_SEPARATE_DIMS,
    PHASE5_SEPARATE_KEYS,
    PHASE6_USD_LEDGER_DIMS,
    PHASE6_USD_LEDGER_KEYS,
    PHASE7_INSURANCE_DIMS,
    PHASE7_INSURANCE_KEYS,
    PHASE8_OUTLIER_DIMS,
    PHASE8_OUTLIER_KEYS,
    READY_FUEL_TREND_KEYS,
    READY_SEPARATE_TREND_KEYS,
    category_name_clause,
    category_names_clause,
    currency_bucket_key,
    humanize_dimension_name,
    is_sparse_trends_payload,
    item_key_from_name,
    liters_by_month_from_docs,
    liters_from_description_docs,
    merge_liters_into_month_series,
    mom_deltas,
    month_deltas,
    native_money,
    parse_item_source_key,
    payee_clause,
    payees_clause,
    polish_dimension_rows,
    static_trend_source_catalog,
    truck_number_clause,
    attach_fleet_sn_and_share,
    filter_truck_fleet_rows,
    resolve_fleet_vehicle_kind,
)


def test_static_catalog_includes_master_and_groups() -> None:
    groups = static_trend_source_catalog()
    titles = [g["group"] for g in groups]
    assert titles == ["Master", "Items", "Separate Expenses", "Fuel"]
    master = groups[0]["sources"][0]
    assert master["key"] == "master"
    assert master["ready"] is True
    assert groups[2]["sources"], "separate expenses should be listed"
    assert groups[3]["sources"], "fuel sources should be listed"


def test_month_deltas_yoy() -> None:
    current = [{"month": 1, "tzs": 120.0}, {"month": 2, "tzs": 80.0}]
    prior = [{"month": 1, "tzs": 100.0}, {"month": 2, "tzs": 100.0}]
    rows = month_deltas(current, prior, currency_key="tzs")
    assert rows[0]["direction"] == "up"
    assert rows[0]["delta"] == 20.0
    assert rows[1]["direction"] == "down"
    assert rows[1]["delta"] == -20.0


def test_mom_deltas() -> None:
    current = [
        {"month": 1, "usd": 50.0},
        {"month": 2, "usd": 75.0},
        {"month": 3, "usd": 60.0},
    ]
    rows = mom_deltas(current, currency_key="usd")
    assert rows[0]["direction"] == "flat"
    assert rows[1]["direction"] == "up"
    assert rows[2]["direction"] == "down"


def test_item_key_helpers() -> None:
    assert item_key_from_name("Diesel Cash") == "diesel_cash"
    assert parse_item_source_key("item:mileage") == "mileage"
    assert parse_item_source_key("master") is None
    clause = category_name_clause("LATRA")
    assert "$or" in clause
    assert clause["$or"][0]["category_name"]["$options"] == "i"


def test_phase3_diesel_stations_ready_in_catalog() -> None:
    assert PHASE3_DIESEL_KEYS == {
        "infinity",
        "lake_zambia",
        "lake_tunduma",
        "gbp_diesel",
    }
    fuel = next(g for g in static_trend_source_catalog() if g["group"] == "Fuel")
    by_key = {s["key"]: s for s in fuel["sources"]}
    for key in PHASE3_DIESEL_KEYS:
        assert by_key[key]["ready"] is True
        assert by_key[key]["phase"] == 3
        assert by_key[key]["kind"] == "fuel"
        assert key in DIESEL_STATION_BY_KEY
    assert DIESEL_STATION_BY_KEY["infinity"] == ("Infinity", "diesel_infinity", "TZS")
    assert DIESEL_STATION_BY_KEY["lake_zambia"] == (
        "Lake Zambia",
        "diesel_lake_zambia",
        "USD",
    )


def test_phase4_diesel_cash_ready_in_catalog() -> None:
    assert PHASE4_DIESEL_CASH_KEYS == {PHASE4_DIESEL_CASH_KEY}
    assert PHASE4_DIESEL_CASH_KEY in READY_FUEL_TREND_KEYS
    fuel = next(g for g in static_trend_source_catalog() if g["group"] == "Fuel")
    by_key = {s["key"]: s for s in fuel["sources"]}
    cash = by_key["diesel_cash"]
    assert cash["ready"] is True
    assert cash["phase"] == 4
    assert cash["kind"] == "fuel"
    assert cash["currency"] == "TZS"


def test_diesel_cash_liters_helpers() -> None:
    docs = [
        {"date": datetime(2026, 1, 5), "description": "Fill 120 Ltrs"},
        {"date": datetime(2026, 1, 20), "description": "80 litres"},
        {"date": datetime(2026, 2, 3), "description": "no qty"},
        {"date": datetime(2025, 1, 1), "description": "50 ltrs"},
    ]
    assert liters_from_description_docs(docs) == 250.0
    by_m = liters_by_month_from_docs(docs, 2026)
    assert by_m[1] == 200.0
    assert by_m[2] == 0.0
    series = merge_liters_into_month_series(
        [{"month": 1, "tzs": 1.0}, {"month": 2, "tzs": 2.0}],
        by_m,
    )
    assert series[0]["liters"] == 200.0
    assert series[1]["liters"] == 0.0


def test_diesel_currency_sum_fields_native_buckets() -> None:
    from app.accountant_analytics_api import _diesel_currency_sum_fields

    tzs = _diesel_currency_sum_fields("TZS")
    assert "$sum" in tzs["tzs"]
    assert tzs["usd"] == {"$sum": 0}
    assert tzs["zmw"] == {"$sum": 0}

    usd = _diesel_currency_sum_fields("USD")
    assert usd["tzs"] == {"$sum": 0}
    assert "$cond" in usd["usd"]["$sum"]
    assert "$cond" in usd["zmw"]["$sum"]


def test_phase5_separate_feeds_ready_with_signature_dims() -> None:
    assert PHASE5_SEPARATE_KEYS == {
        "toll_plaza",
        "parking_congo",
        "zambia_parking",
        "rahntech",
    }
    assert PHASE5_SEPARATE_DIMS["parking_congo"].exclude_deposits is True
    assert PHASE5_SEPARATE_DIMS["toll_plaza"].mix_field == "toll_plaza"
    assert PHASE5_SEPARATE_DIMS["zambia_parking"].truck_field == "plate_num"
    assert PHASE5_SEPARATE_DIMS["rahntech"].slice_field == "trip_number"
    assert currency_bucket_key("ZMW") == "zmw"
    assert native_money("USD", 12.5) == {"tzs": 0.0, "usd": 12.5, "zmw": 0.0}

    separate = next(
        g for g in static_trend_source_catalog() if g["group"] == "Separate Expenses"
    )
    by_key = {s["key"]: s for s in separate["sources"]}
    for key in PHASE5_SEPARATE_KEYS:
        assert by_key[key]["ready"] is True
        assert by_key[key]["phase"] == 5
        assert by_key[key]["kind"] == "separate"
    # Phase 8 sources asserted in test_phase8_outliers_ready_in_catalog


def test_phase6_usd_ledgers_ready_in_catalog() -> None:
    assert PHASE6_USD_LEDGER_KEYS == {"congo_exp", "ahmed_kimvi"}
    assert PHASE6_USD_LEDGER_KEYS <= READY_SEPARATE_TREND_KEYS
    assert PHASE6_USD_LEDGER_DIMS.mix_field == "description"
    assert PHASE6_USD_LEDGER_DIMS.truck_field == "truck_no"
    assert PHASE6_USD_LEDGER_DIMS.slice_label == "Money In vs Called Out"

    separate = next(
        g for g in static_trend_source_catalog() if g["group"] == "Separate Expenses"
    )
    by_key = {s["key"]: s for s in separate["sources"]}
    for key in PHASE6_USD_LEDGER_KEYS:
        assert by_key[key]["ready"] is True
        assert by_key[key]["phase"] == 6
        assert by_key[key]["kind"] == "ledger"
        assert by_key[key]["currency"] == "USD"


def test_usd_ledger_sum_fields_signed_split() -> None:
    from app.accountant_analytics_api import _usd_ledger_sum_fields

    fields = _usd_ledger_sum_fields()
    assert "$cond" in fields["money_out"]["$sum"]
    assert "$cond" in fields["money_in"]["$sum"]
    assert fields["net"] == {"$sum": {"$ifNull": ["$amount_usd", 0]}}
    assert fields["count"] == {"$sum": 1}


def test_phase7_insurance_pair_ready_in_catalog() -> None:
    assert PHASE7_INSURANCE_KEYS == {"comesa", "third_party"}
    assert PHASE7_INSURANCE_KEYS <= READY_SEPARATE_TREND_KEYS
    assert PHASE7_INSURANCE_DIMS["comesa"].mix_field == "card_no"
    assert PHASE7_INSURANCE_DIMS["comesa"].truck_field == "truck_reg"
    assert PHASE7_INSURANCE_DIMS["third_party"].mix_field == "status"
    assert PHASE7_INSURANCE_DIMS["third_party"].truck_field == "reg_no"
    assert PHASE7_INSURANCE_DIMS["third_party"].slice_field == "month"

    separate = next(
        g for g in static_trend_source_catalog() if g["group"] == "Separate Expenses"
    )
    by_key = {s["key"]: s for s in separate["sources"]}
    for key in PHASE7_INSURANCE_KEYS:
        assert by_key[key]["ready"] is True
        assert by_key[key]["phase"] == 7
        assert by_key[key]["kind"] == "insurance"
        assert by_key[key]["currency"] == "TZS"


def test_phase8_outliers_ready_in_catalog() -> None:
    assert PHASE8_OUTLIER_KEYS == {"afritrack", "sm_burhani"}
    assert PHASE8_OUTLIER_KEYS <= READY_SEPARATE_TREND_KEYS
    assert PHASE8_OUTLIER_DIMS["afritrack"].mix_field == "period"
    assert PHASE8_OUTLIER_DIMS["afritrack"].slice_label == "By Variance"
    assert PHASE8_OUTLIER_DIMS["sm_burhani"].mix_field == "table"
    assert PHASE8_OUTLIER_DIMS["sm_burhani"].truck_field == "station"
    assert PHASE8_OUTLIER_DIMS["sm_burhani"].slice_field == "confirmed"

    separate = next(
        g for g in static_trend_source_catalog() if g["group"] == "Separate Expenses"
    )
    by_key = {s["key"]: s for s in separate["sources"]}
    for key in PHASE8_OUTLIER_KEYS:
        assert by_key[key]["ready"] is True
        assert by_key[key]["phase"] == 8
        assert by_key[key]["kind"] == "outlier"
        assert by_key[key]["currency"] == "USD"
    # Full separate-expense coverage — nothing left pending.
    assert all(s["ready"] is True for s in separate["sources"])


def test_entities_phase_e1_helpers() -> None:
    assert ENTITY_FAMILY_KEYS == {"items", "payees", "suppliers"}
    assert [k for k, _ in ENTITY_FAMILIES] == ["items", "payees", "suppliers"]
    multi = category_names_clause(["Parking A", "Parking B"])
    assert "$or" in multi
    rx = multi["$or"][0]["category_name"]["$regex"]
    assert "Parking\\ A" in rx and "Parking\\ B" in rx
    assert payee_clause("Acme Ltd")["payee"]["$options"] == "i"
    assert payee_clause("") == {}


def test_entities_phase_e2_helpers() -> None:
    multi_pay = payees_clause(["Acme Ltd", "Beta Co"])
    assert "Acme\\ Ltd" in multi_pay["payee"]["$regex"]
    assert "Beta\\ Co" in multi_pay["payee"]["$regex"]
    assert payees_clause(["Solo"]) == payee_clause("Solo")
    assert payees_clause([]) == {}
    assert entity_set_label("items", ["Parking A"]) == "Parking A"
    assert entity_set_label("items", ["Parking A", "Parking B"]) == (
        "Parking A + Parking B"
    )
    assert entity_set_label("payees", ["A", "B", "C"]).startswith("3 payees · A")
    assert entity_set_label("mixed", ["A", "B", "C"]).startswith("3 entities · A")
    assert entity_set_label("entities", []) == "Entities"


def test_entities_phase_e3_cache_and_compare_limits() -> None:
    assert ENTITY_COMPARE_MAX == 4
    entity_cache_clear()
    key = entity_cache_key("entities", "items", "a,b", 2026, "year", None)
    assert entity_cache_get(key) is None
    entity_cache_set(key, {"ready": True, "label": "Parking A + Parking B"})
    hit = entity_cache_get(key)
    assert hit is not None
    assert hit["label"] == "Parking A + Parking B"
    # Mutating the returned copy must not corrupt the cache.
    hit["label"] = "mutated"
    assert entity_cache_get(key)["label"] == "Parking A + Parking B"
    entity_cache_clear()
    assert entity_cache_get(key) is None


def test_iter_year_months_and_labels() -> None:
    assert iter_year_months(date(2025, 11, 15), date(2026, 2, 10)) == [
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]
    assert month_trend_label(2026, 3, multi_year=False) == "Mar"
    assert month_trend_label(2025, 11, multi_year=True) == "Nov '25"


def test_build_data_table_month_trend_merges_and_clips() -> None:
    trend = build_data_table_month_trend(
        [
            {"year": 2026, "month": 1, "tzs": 100.0, "usd": 0.0, "zmw": 0.0},
            {"year": 2026, "month": 1, "tzs": 50.0, "usd": 10.0, "zmw": 0.0},
            {"year": 2026, "month": 2, "tzs": 0.0, "usd": 5.0, "zmw": 20.0},
            {"year": 2025, "month": 12, "tzs": 999.0, "usd": 0.0, "zmw": 0.0},
        ],
        date(2026, 1, 15),
        date(2026, 2, 28),
    )
    assert [row["label"] for row in trend] == ["Jan", "Feb"]
    assert trend[0]["tzs"] == 150.0
    assert trend[0]["usd"] == 10.0
    assert trend[1]["usd"] == 5.0
    assert trend[1]["zmw"] == 20.0


def test_build_data_table_month_trend_multi_year_labels() -> None:
    trend = build_data_table_month_trend(
        [{"year": 2025, "month": 12, "tzs": 1.0, "usd": 0.0, "zmw": 0.0}],
        date(2025, 12, 1),
        date(2026, 1, 31),
    )
    assert [row["label"] for row in trend] == ["Dec '25", "Jan '26"]
    assert trend[0]["tzs"] == 1.0
    assert trend[1]["tzs"] == 0.0


def test_build_data_table_rows_excludes_diesel_cash_and_totals() -> None:
    table = build_data_table_rows(
        [
            {
                "key": "master",
                "label": "Master Expenses",
                "group": "Master",
                "tzs": 500.0,
                "usd": 10.0,
                "zmw": 0.0,
                "count": 5,
            },
            {
                "key": "diesel_cash",
                "label": "Diesel Cash",
                "group": "Fuel",
                "tzs": 80.0,
                "usd": 0.0,
                "zmw": 0.0,
                "count": 2,
            },
            {
                "key": "toll_plaza",
                "label": "Toll Plaza",
                "group": "Separate Expenses",
                "tzs": 0.0,
                "usd": 0.0,
                "zmw": 30.0,
                "count": 3,
            },
            {
                "key": "infinity",
                "label": "Infinity",
                "group": "Fuel stations",
                "tzs": 200.0,
                "usd": 0.0,
                "zmw": 0.0,
                "count": 4,
            },
        ]
    )
    keys = [r["key"] for r in table["sources"]]
    assert "diesel_cash" not in keys
    assert keys == ["master", "toll_plaza", "infinity"]
    assert table["totals"]["tzs"] == 700.0
    assert table["totals"]["usd"] == 10.0
    assert table["totals"]["zmw"] == 30.0
    assert table["totals"]["count"] == 12
    assert table["source_count"] == 3


def test_truck_number_clause_space_insensitive() -> None:
    assert truck_number_clause("") == {}
    assert truck_number_clause("—") == {}
    assert truck_number_clause("-") == {}
    clause = truck_number_clause("T 832 DND")
    assert "truck_number" in clause
    rx = clause["truck_number"]
    assert rx["$options"] == "i"
    assert "$regex" in rx
    # Digits are spaced flexibly so "T832DND" / "T 832 DND" both match.
    assert r"8\s*3\s*2" in rx["$regex"]
    assert rx["$regex"].startswith("^")
    compact = truck_number_clause("T832DND")
    assert "truck_number" in compact
    assert compact["truck_number"]["$options"] == "i"


def test_build_truck_fleet_table_multi_currency_and_share() -> None:
    table = build_truck_fleet_table(
        [
            {"truck": "T 832 DND", "tzs": 100.0, "usd": 2.0, "zmw": 0.0, "count": 10},
            {"name": "T 104 FRE", "tzs": 50.0, "usd": 0.0, "zmw": 20.0, "count": 4},
            {"truck": "—", "tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0},
            # Blank / placeholder plates with real spend must not enter the fleet.
            {"truck": "—", "tzs": 9_999.0, "usd": 100.0, "zmw": 0.0, "count": 50},
            {"truck": "", "tzs": 500.0, "usd": 0.0, "zmw": 0.0, "count": 3},
        ],
        kind_lookup={
            "t832dnd": "truck",
            "t104fre": "trailer",
        },
    )
    assert table["truck_count"] == 2
    assert table["share_basis"] == "tzs"
    assert table["totals"]["tzs"] == 150.0
    assert table["totals"]["usd"] == 2.0
    assert table["totals"]["zmw"] == 20.0
    assert table["totals"]["count"] == 14
    assert table["trucks"][0]["truck"] == "T 832 DND"
    assert table["trucks"][0]["sn"] == 1
    assert table["trucks"][0]["vehicle_kind"] == "truck"
    assert table["trucks"][1]["sn"] == 2
    assert table["trucks"][1]["vehicle_kind"] == "trailer"
    assert abs(table["trucks"][0]["share"] - (100.0 / 150.0)) < 1e-9
    assert abs(table["trucks"][1]["share"] - (50.0 / 150.0)) < 1e-9
    assert all(r["truck"] not in {"—", ""} for r in table["trucks"])


def test_fleet_kind_filter_and_search() -> None:
    assert resolve_fleet_vehicle_kind("T 1", {"t1": "motor_vehicle"}) == "motor_vehicle"
    assert resolve_fleet_vehicle_kind("X", {}) == "unknown"
    rows = [
        {"truck": "T 832 DND", "vehicle_kind": "truck", "tzs": 100.0},
        {"truck": "T 900 TRL", "vehicle_kind": "trailer", "tzs": 40.0},
        {"truck": "MC 12 AB", "vehicle_kind": "motor_vehicle", "tzs": 10.0},
    ]
    trailers = filter_truck_fleet_rows(rows, fleet_kind="trailer")
    assert [r["truck"] for r in trailers] == ["T 900 TRL"]
    hit = filter_truck_fleet_rows(rows, q="832")
    assert [r["truck"] for r in hit] == ["T 832 DND"]
    rebuilt = attach_fleet_sn_and_share(trailers, share_basis="tzs")
    assert rebuilt["trucks"][0]["sn"] == 1
    assert abs(rebuilt["trucks"][0]["share"] - 1.0) < 1e-9


def test_build_truck_fleet_table_mom_and_usd_share_fallback() -> None:
    table = build_truck_fleet_table(
        [
            {"truck": "T 832 DND", "tzs": 0.0, "usd": 80.0, "zmw": 0.0, "count": 3},
            {"truck": "T832DND", "tzs": 0.0, "usd": 0.0, "zmw": 0.0, "count": 0},
            {"truck": "T 104 FRE", "tzs": 0.0, "usd": 20.0, "zmw": 0.0, "count": 1},
        ],
        prior_rows=[
            {"truck": "T832DND", "tzs": 50.0, "usd": 0.0, "zmw": 0.0, "count": 2},
            {"truck": "T 104 FRE", "tzs": 100.0, "usd": 0.0, "zmw": 0.0, "count": 4},
        ],
    )
    assert table["share_basis"] == "usd"
    assert abs(table["trucks"][0]["share"] - 0.8) < 1e-9
    # Current TZS is 0 vs prior 50 → MoM down 100%.
    lead = next(r for r in table["trucks"] if r["truck"] == "T 832 DND")
    assert lead["mom_direction"] == "down"
    assert abs(lead["mom_pct"] - (-1.0)) < 1e-9
    fre = next(r for r in table["trucks"] if r["truck"] == "T 104 FRE")
    assert fre["mom_direction"] == "down"


def test_truck_detail_cache_key_shape() -> None:
    entity_cache_clear()
    key = entity_cache_key("truck_detail", "t 832 dnd", 2026, "year", None)
    entity_cache_set(key, {"ready": True, "label": "T 832 DND", "sparse": False})
    hit = entity_cache_get(key)
    assert hit is not None
    assert hit["label"] == "T 832 DND"
    entity_cache_clear()


def test_trends_cache_and_compare_limits() -> None:
    assert TREND_COMPARE_MAX == ENTITY_COMPARE_MAX == 4
    assert len(TREND_COMPARE_COLORS) >= 4
    entity_cache_clear()
    key = entity_cache_key("trends", "master", 2026, "year", None)
    entity_cache_set(key, {"ready": True, "source": "master"})
    assert entity_cache_get(key)["source"] == "master"
    cmp_key = entity_cache_key(
        "trends_compare", "master,diesel_cash", 2026, "year", None
    )
    entity_cache_set(cmp_key, {"mode": "compare", "ready": True})
    assert entity_cache_get(cmp_key)["mode"] == "compare"
    entity_cache_clear()


def test_phase9_polish_helpers() -> None:
    assert humanize_dimension_name("sm_burhani", "table", "bonds") == "Bonds"
    assert (
        humanize_dimension_name("sm_burhani", "table", "rpa_schedule")
        == "RPA Schedule"
    )
    assert humanize_dimension_name("sm_burhani", "confirmed", "true") == "Confirmed"
    assert (
        humanize_dimension_name("sm_burhani", "confirmed", "false") == "Unconfirmed"
    )
    rows = polish_dimension_rows(
        "sm_burhani",
        "confirmed",
        [{"name": "true", "usd": 10.0}, {"name": "false", "usd": 5.0}],
    )
    assert [r["name"] for r in rows] == ["Confirmed", "Unconfirmed"]
    assert is_sparse_trends_payload(
        {"ready": True, "kpis": {"count": 0, "totals": {"tzs": 0, "usd": 0, "zmw": 0}}}
    )
    assert not is_sparse_trends_payload(
        {"ready": True, "kpis": {"count": 3, "totals": {"tzs": 0, "usd": 0, "zmw": 0}}}
    )
    assert not is_sparse_trends_payload({"ready": False, "kpis": {"count": 0}})
