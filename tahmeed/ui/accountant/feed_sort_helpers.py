"""Column sort specs for Separate Expenses and Fuel item ledgers."""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtWidgets import QTableWidget

from tahmeed.ui.widgets.sortable_ledger_header import (
    ColumnSpec,
    LedgerSortState,
    attach_sortable_header,
)

# Toll Plaza detail / all entries
TOLL_DETAIL_SORT: List[ColumnSpec] = [
    ("TOLL DATE", "toll_date", "date"),
    ("TOLL PLAZA", "toll_plaza", "text"),
    ("CLIENT NAME", "client_name", "text"),
    ("CARD NO", "card_no", "text"),
    ("VEHICLE REG", "vehicle_reg", "truck"),
    ("CLASS", "vehicle_class", "text"),
    ("TENDER (ZMW)", "tender_amount", "number"),
    ("RECEIPT NO", "receipt_no", "text"),
    ("DEVICE", "device", "text"),
    ("LANE", "lane", "text"),
    ("CASHIER", "cashier_name", "text"),
]

PARKING_CONGO_DETAIL_SORT: List[ColumnSpec] = [
    ("#", "sn", "number"),
    ("LEDGER ID", "ledger_id", "text"),
    ("PAYMENT DATE", "payment_date", "date"),
    ("TYPE", "transaction_type", "text"),
    ("AMOUNT", "amount", "number"),
    ("RUNNING BAL", "running_balance", "number"),
    ("CASHIER", "cashier", "text"),
    ("VEHICLE #", "vehicle_no", "truck"),
    ("DIRECTION", "direction", "text"),
    ("GATE IN", "gate_in", "text"),
    ("TRANSACTION DETAILS", "transaction_details", "text"),
]

KIMVI_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("DATE", "expense_date", "date"),
    ("TRUCK NO", "truck_no", "truck"),
    ("PARTICULARS", "description", "text"),
    ("AMOUNT (USD)", "amount_usd", "number"),
]

CONGO_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("DATE", "expense_date", "date"),
    ("LPO NO", "lpo_no", "text"),
    ("TRUCK NO", "truck_no", "truck"),
    ("DESCRIPTION", "description", "text"),
    ("AMOUNT (USD)", "amount_usd", "number"),
]

ZAMBIA_PARKING_SORT: List[ColumnSpec] = [
    ("DATE", "date", "date"),
    ("TYPE", "transaction_type", "text"),
    ("PLATE NUM.", "plate_num", "truck"),
    ("TICKET NO.", "ticket_no", "text"),
    ("DEBIT", "debit", "number"),
    ("CREDIT", "credit", "number"),
    ("BALANCE", "balance", "number"),
    ("HEADING TO", "heading_to", "text"),
]

AFRITRACK_DETAIL_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("TRUCK", "truck", "truck"),
    ("DAYS", "days", "number"),
    ("NON-TRANS", "non_trans_days", "number"),
    ("TRANS", "trans_days", "number"),
    ("RATE/DAY", "rate_per_day", "number"),
    ("TOTAL TAHMEED", "total_tahmeed", "number"),
    ("TOTAL INVOICE", "total_invoice", "number"),
    ("VARIANCE", "variance", "number"),
    ("REMARKS", "remarks", "text"),
]

RAHNTECH_SORT: List[ColumnSpec] = [
    ("S/N", None, "text"),
    ("SALES DATE", "sales_date", "date"),
    ("TRIP NUMBER", "trip_number", "text"),
    ("DEVICE NUMBER", "device_number", "text"),
    ("TRUCK NUMBER", "truck_number", "truck"),
    ("DRIVER NAME", "driver_name", "text"),
    ("DO", "do_number", "text"),
]

COMESA_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("NAME", "name", "text"),
    ("CARD NO.", "card_no", "text"),
    ("VALID FROM", "valid_from", "text"),
    ("VALID TO", "valid_to", "text"),
    ("TRUCK REG", "truck_reg", "truck"),
    ("PREMIUM", "premium", "number"),
    ("MONTH", "month", "text"),
]

THIRD_PARTY_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("NAME", "name", "text"),
    ("REG. NO.", "reg_no", "truck"),
    ("PREMIUM", "premium", "number"),
    ("VAT 18%", "vat", "number"),
    ("TOTAL PREMIUM", "total_premium", "number"),
    ("MONTH", "month", "text"),
    ("STATUS", "status", "text"),
]

CATEGORY_TABLE_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("DATE", "date", "date"),
    ("ITEM", "category_name", "text"),
    ("DESCRIPTION", "description", "text"),
    ("TRUCK NO.", "truck_number", "truck"),
    ("MEMO", "memo", "text"),
    ("NOTES", None, "text"),
    ("TZS", "amount", "number"),
    ("RECEIPT", "receipt_status", "text"),
    ("OWNERSHIP", "ownership", "text"),
    ("APR BY", "approver", "text"),
]

DIESEL_CASH_SORT: List[ColumnSpec] = [
    ("S/NO", None, "text"),
    ("DATE", "date", "date"),
    ("ITEM", "category_name", "text"),
    ("DESCRIPTION", "description", "text"),
    ("LTRS", None, "number"),
    ("TRUCK NO.", "truck_number", "truck"),
    ("MEMO", "memo", "text"),
    ("NOTES", None, "text"),
    ("TZS", "amount", "number"),
    ("RECEIPT", "receipt_status", "text"),
    ("OWNERSHIP", "ownership", "text"),
    ("APR BY", "approver", "text"),
]


def wire_feed_table_sort(
    table: QTableWidget,
    columns: List[ColumnSpec],
    *,
    default_field: str,
    default_asc: bool = False,
    on_sort_changed: Optional[Callable[[str, bool], None]] = None,
) -> LedgerSortState:
    return attach_sortable_header(
        table,
        columns,
        default_field=default_field,
        default_asc=default_asc,
        on_sort_changed=on_sort_changed,
    )


def sort_kw(state: LedgerSortState) -> dict:
    return {"sort_field": state.sort_field, "sort_asc": state.sort_asc}


def reset_feed_sort(state: Optional[LedgerSortState]) -> None:
    """Restore default column sort (used by Clear filters)."""
    if state is not None:
        state.reset()


def clear_upload_detail_filters(widget) -> None:
    """Clear search + default column sort on an upload-detail feed table."""
    widget._search_edit.blockSignals(True)
    widget._search_edit.setText("")
    widget._search_edit.blockSignals(False)
    widget._search = ""
    reset_feed_sort(getattr(widget, "_sort_state", None))
    widget._reset_and_load()
