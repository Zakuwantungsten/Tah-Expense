"""Header column filter cascading — options only from table rows + chaining."""

from tahmeed.ui.cashier.excel_grid import cascade_column_values

# Synthetic cols matching register intent
COL_ITEM, COL_DESC, COL_TRUCK, COL_OWN = 3, 4, 5, 10


def _rows():
    return [
        {COL_ITEM: "Fuel", COL_DESC: "Diesel", COL_TRUCK: "T100", COL_OWN: "Aisha"},
        {COL_ITEM: "Fuel", COL_DESC: "Petrol", COL_TRUCK: "T100", COL_OWN: "John"},
        {COL_ITEM: "Parts", COL_DESC: "Filter", COL_TRUCK: "T200", COL_OWN: "Aisha"},
        {COL_ITEM: "Parts", COL_DESC: "Belt", COL_TRUCK: "T200", COL_OWN: "John"},
    ]


def test_no_filters_lists_all_distinct_values_present():
    rows = _rows()
    assert cascade_column_values(rows, target_col=COL_TRUCK, active_filters={}) == {
        "T100", "T200"
    }
    assert cascade_column_values(rows, target_col=COL_DESC, active_filters={}) == {
        "Diesel", "Petrol", "Filter", "Belt"
    }


def test_truck_filter_narrows_description_and_ownership():
    rows = _rows()
    filters = {COL_TRUCK: {"T100"}}
    assert cascade_column_values(
        rows, target_col=COL_DESC, active_filters=filters
    ) == {"Diesel", "Petrol"}
    assert cascade_column_values(
        rows, target_col=COL_OWN, active_filters=filters
    ) == {"Aisha", "John"}
    assert cascade_column_values(
        rows, target_col=COL_ITEM, active_filters=filters
    ) == {"Fuel"}


def test_chained_truck_and_owner_narrows_description():
    rows = _rows()
    filters = {COL_TRUCK: {"T200"}, COL_OWN: {"Aisha"}}
    assert cascade_column_values(
        rows, target_col=COL_DESC, active_filters=filters
    ) == {"Filter"}
    assert cascade_column_values(
        rows, target_col=COL_ITEM, active_filters=filters
    ) == {"Parts"}


def test_target_columns_own_filter_is_ignored_when_listing_its_options():
    """Excel-style: opening Truck ▾ still shows both trucks even if T100 is selected."""
    rows = _rows()
    filters = {COL_TRUCK: {"T100"}, COL_OWN: {"Aisha"}}
    assert cascade_column_values(
        rows, target_col=COL_TRUCK, active_filters=filters
    ) == {"T100", "T200"}
