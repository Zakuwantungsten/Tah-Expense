from tahmeed.services.truck_format import (
    is_allowed_place_label,
    is_place_label_candidate,
    normalize_truck_number,
    truck_sort_key,
    try_match_fleet,
)


def test_normalize_canonical_ok() -> None:
    result = normalize_truck_number("T688 EAF")
    assert result.status == "ok"
    assert result.value == "T688 EAF"


def test_normalize_compact_inserts_space() -> None:
    result = normalize_truck_number("T688EAF")
    assert result.status == "normalized"
    assert result.value == "T688 EAF"


def test_normalize_lowercase_mixed() -> None:
    result = normalize_truck_number("T586dre")
    assert result.status == "normalized"
    assert result.value == "T586 DRE"


def test_normalize_trailing_period() -> None:
    result = normalize_truck_number("T542DRF.")
    assert result.status == "normalized"
    assert result.value == "T542 DRF"


def test_normalize_lowercase_and_extra_spaces() -> None:
    result = normalize_truck_number("  t688   eaf ")
    assert result.status in ("ok", "normalized")
    assert result.value == "T688 EAF"


def test_normalize_missing_t_prefix() -> None:
    result = normalize_truck_number("688 EAF")
    assert result.status == "normalized"
    assert result.value == "T688 EAF"


def test_normalize_invalid() -> None:
    result = normalize_truck_number("NOT-A-TRUCK", allowed_labels=())
    assert result.status == "invalid"


def test_normalize_empty() -> None:
    result = normalize_truck_number("   ")
    assert result.status == "empty"
    assert result.value == ""


def test_normalize_yard_garage_defaults() -> None:
    assert normalize_truck_number("yard").status == "place_label"
    assert normalize_truck_number("yard").value == "YARD"
    assert normalize_truck_number("GARAGE").status == "place_label"
    assert normalize_truck_number("GARAGE").value == "GARAGE"


def test_place_label_candidate() -> None:
    assert is_place_label_candidate("YARD")
    assert is_place_label_candidate("garage")
    assert not is_place_label_candidate("T688 EAF")
    assert not is_place_label_candidate("T586dre")


def test_allowed_place_label() -> None:
    assert is_allowed_place_label("yard", {"YARD", "GARAGE"})
    assert not is_allowed_place_label("DEPOT", {"YARD", "GARAGE"})


def test_try_match_fleet_exact_and_compact() -> None:
    fleet = {"T688 EAF", "T880CUL"}
    assert try_match_fleet("T688EAF", fleet) == "T688 EAF"
    assert try_match_fleet("t880 cul", fleet) == "T880 CUL"
    assert try_match_fleet("XYZ 999", fleet) is None


def test_truck_sort_key_numeric_order() -> None:
    keys = [
        truck_sort_key("T2 EAF"),
        truck_sort_key("T10 EAF"),
        truck_sort_key("T688 EAF"),
    ]
    assert keys == sorted(keys)


def test_truck_sort_key_plates_before_places() -> None:
    assert truck_sort_key("T2 EAF") < truck_sort_key("YARD")
    assert truck_sort_key("GARAGE") > truck_sort_key("T999 ZZZ")


def test_truck_sort_key_empty_last() -> None:
    assert truck_sort_key("") > truck_sort_key("YARD")
    assert truck_sort_key("") > truck_sort_key("T1 AAA")
