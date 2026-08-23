"""Fuel Overview — truck-centric view of diesel cash and station imports.

Same interaction as Truck Overview (search a truck, filter, export) but only
the Fuel Consumption sources: Diesel Cash, Infinity, Lake Zambia, Lake Tunduma,
and GBP Diesel.
"""

from __future__ import annotations

from typing import List, Optional

from tahmeed.ui.accountant.truck_overview import (
    TruckOverviewWidget,
    _FUEL_OVERVIEW_COLS,
    _FUEL_OVERVIEW_STRETCH_COLS,
)

_FUEL_SOURCE_OPTIONS = [
    ("All Sources", "all"),
    ("Diesel Cash", "diesel_cash"),
    ("Infinity", "infinity"),
    ("Lake Zambia", "lake_zambia"),
    ("Lake Tunduma", "lake_tunduma"),
    ("GBP Diesel", "gbp_diesel"),
]

# (header label, mongo/display field, sort/filter kind)
_FUEL_OVERVIEW_FILTER_SPECS: List[tuple[str, Optional[str], str]] = [
    ("DATE", "date", "date"),
    ("SOURCE", "source", "text"),
    ("DESCRIPTION", "description", "text"),
    ("REFERENCE", "reference", "text"),
    ("TRUCK FIELD", "truck_value", "truck"),
    ("TZS", "tzs", "number"),
    ("USD", "usd", "number"),
    ("ZMW", "zmw", "number"),
    ("LTRS", "liters", "number"),
    ("RATE", "rate", "number"),
    ("STATION / OWNER", "station", "text"),
    ("UPLOAD DESCRIPTION", "upload_description", "text"),
    ("RECEIPT", "receipt_status", "text"),
]


class FuelOverviewWidget(TruckOverviewWidget):
    PAGE_TITLE = "Fuel Overview"
    PAGE_ICON = "mdi.gas-station"
    PAGE_OBJECT_NAME = "fuelOverview"
    SOURCE_OPTIONS = _FUEL_SOURCE_OPTIONS
    TABLE_COLS = _FUEL_OVERVIEW_COLS
    TABLE_STRETCH_COLS = _FUEL_OVERVIEW_STRETCH_COLS
    USE_EXCEL_COLUMN_FILTERS = True
    DEFAULT_SORT_FIELD = "date"
    DEFAULT_SORT_ASC = True
    SUBTITLE_EMPTY = "Select a truck to gather diesel cash and station fuel entries."
    LOADING_OVERLAY_TEXT = "Loading fuel overview…"
    STATUS_EMPTY = "No truck selected yet."
    ROW_NOUN = "fuel row"
    STATUS_HINT = ""
    LOADED_SUBTITLE = "Fuel view for {truck}  ·  {period}"
    LOAD_ERROR_LABEL = "fuel overview"
    HIGHLIGHT_SOURCE_GROUPS = ("diesel_cash",)
    EXPORT_PREFIX = "Fuel_Overview"
    EXCEL_DIALOG_TITLE = "Export Fuel Overview"
    PDF_DIALOG_TITLE = "Export Fuel Overview PDF"
    EXCEL_SHEET_TITLE = "Fuel Overview"
    EXCEL_BANNER = "FUEL OVERVIEW"
    PDF_EYEBROW = "FUEL CONSUMPTION REPORT"
    PDF_SUBTITLE = "Consolidated fuel record across diesel cash and all stations"

    def _column_filter_specs(self):
        return _FUEL_OVERVIEW_FILTER_SPECS

    def _overview_apis(self):
        from tahmeed.services.accountant_service import (
            get_fuel_overview_records,
            count_fuel_overview_records,
            get_fuel_overview_summary,
        )
        return (
            get_fuel_overview_records,
            count_fuel_overview_records,
            get_fuel_overview_summary,
        )
