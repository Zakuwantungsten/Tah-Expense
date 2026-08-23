"""Fuel station export row mapping."""

from __future__ import annotations

from tahmeed.ui.accountant.fuel_consumption import (
    _FUEL_SCHEMAS,
    _diesel_export_row,
    _display_columns,
)


def test_diesel_export_row_maps_schema_columns() -> None:
    schema = _FUEL_SCHEMAS["diesel_infinity"]
    columns = _display_columns(schema)
    row = _diesel_export_row(
        {
            "date": "2026-01-15",
            "lpo_no": "LPO-1",
            "truck_no": "T123",
            "ltrs": 100,
            "price_per_ltr": 2500,
            "upload_label": "March batch",
        },
        columns,
        sn=1,
    )
    assert row[0] == 1
    assert "LPO-1" in row
    assert "T123" in row
    assert row[-1] == "March batch"
    assert any(v == 250000.0 for v in row)
