"""Parse litre quantities out of diesel-cash description text."""

from tahmeed.services.diesel_liters import format_liters, parse_liters_from_description


def test_parses_number_stuck_to_ltrs() -> None:
    assert parse_liters_from_description("DIESEL 350LTRS") == 350
    assert parse_liters_from_description("DIESEL 110LTRS") == 110
    assert parse_liters_from_description("DIESEL 40LTRS") == 40


def test_parses_mixed_case_and_space_before_unit() -> None:
    assert parse_liters_from_description("DIESEL 350Ltrs") == 350
    assert parse_liters_from_description("DIESEL 560 LTRS") == 560
    assert parse_liters_from_description("diesel 520 litres") == 520
    assert parse_liters_from_description("DIESEL 40 ltr") == 40


def test_no_liters_left_blank() -> None:
    assert parse_liters_from_description("DIESEL NAKONDE") is None
    assert parse_liters_from_description("") is None
    assert parse_liters_from_description("DIESEL") is None


def test_fallback_finds_standalone_number_without_unit() -> None:
    assert parse_liters_from_description("DIESEL 350") == 350
    assert parse_liters_from_description("350") == 350


def test_fallback_ignores_truck_plate_and_year() -> None:
    assert parse_liters_from_description("DIESEL T615 ENG") is None
    assert parse_liters_from_description("DIESEL 2025 NAKONDE") is None
    assert parse_liters_from_description("DIESEL T615 350LTRS") == 350


def test_unit_match_wins_over_other_numbers() -> None:
    assert parse_liters_from_description("DIESEL 2ND 350LTRS") == 350


def test_format_liters() -> None:
    assert format_liters(None) == "—"
    assert format_liters(350.0) == "350"
    assert format_liters(40.5) == "40.5"
