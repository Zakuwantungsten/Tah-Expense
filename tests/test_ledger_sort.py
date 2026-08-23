from tahmeed.services.ledger_sort import TRUCK_SORT_FIELDS, ledger_sort_clauses


def test_ledger_sort_whitelist_fallback() -> None:
    allowed = {"date", "amount", "truck_number"}
    clauses = ledger_sort_clauses("bad_field", True, allowed, default="date")
    assert clauses == [("date", 1), ("created_at", -1)]


def test_ledger_sort_truck_field_prepends_sort_key() -> None:
    allowed = set(TRUCK_SORT_FIELDS) | {"date"}
    clauses = ledger_sort_clauses("truck_number", True, allowed)
    assert clauses[0] == ("truck_sort_key", 1)
    assert clauses[1] == ("truck_number", 1)


def test_ledger_sort_descending() -> None:
    clauses = ledger_sort_clauses("amount", False, {"amount", "date"}, default="date")
    assert clauses == [("amount", -1), ("created_at", -1)]
