"""
TransactionBrowser — QuickBooks-style "Find" dialog.

Shows a daily-summary list: one row per calendar day.
Columns: Date | Transaction ID | Entries | Refund to Float | Total Amount
Clicking a row and pressing "Go To" (or double-clicking) navigates the
register to that date.
"""

import asyncio
from datetime import date, timedelta
from typing import List

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QDateEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFrame, QWidget,
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QFont

from tahmeed.services.cashier_service import get_daily_summaries


_BTN_STYLE = """
QPushButton {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 5px;
    padding: 5px 16px;
    color: #374151;
    font-size: 12px;
}
QPushButton:hover   { background: #f9fafb; }
QPushButton:pressed { background: #e5e7eb; }
"""

_PRIMARY_BTN = """
QPushButton {
    background: #E85D04;
    border: 1px solid #F48C06;
    border-radius: 5px;
    padding: 5px 18px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover   { background: #F48C06; }
QPushButton:pressed { background: #DC2F02; }
QPushButton:disabled { background: #fdba74; border-color: #fdba74; }
"""

_TABLE_SS = """
QTableWidget {
    background: #ffffff;
    gridline-color: #e5e7eb;
    border: none;
    selection-background-color: #fff3e8;
    selection-color: #111827;
}
QHeaderView::section {
    background: #f1f5f9;
    color: #334155;
    font-weight: 600;
    font-size: 11px;
    padding: 5px 8px;
    border: none;
    border-right: 1px solid #cbd5e1;
    border-bottom: 2px solid #cbd5e1;
}
QTableWidget::item { padding: 2px 8px; color: #111827; }
QTableWidget::item:alternate { background: #f8fafc; }
"""

# Column indices
_COL_DATE    = 0
_COL_TXN_ID  = 1
_COL_ENTRIES = 2
_COL_REFUND  = 3
_COL_TOTAL   = 4


class TransactionBrowser(QDialog):
    """
    Modeless dialog — one row per calendar day with aggregated totals.
    Emits go_to_date(date) when the user navigates to a day.
    """

    go_to_date = Signal(object)   # passes a Python date object

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transaction Browser")
        self.setMinimumSize(820, 580)
        self.setWindowFlags(
            Qt.Dialog
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self._results: List[dict] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ─────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background: #1c1917; border-bottom: 1px solid #1c1917;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("Transaction Browser")
        title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: 700;")
        hl.addWidget(title)
        sub = QLabel("Daily summary — one row per day")
        sub.setStyleSheet("color: #a8a29e; font-size: 11px; margin-left: 12px;")
        hl.addWidget(sub)
        hl.addStretch()
        root.addWidget(header)

        # ── Filter panel ───────────────────────────────────────────────
        filter_panel = QWidget()
        filter_panel.setStyleSheet(
            "background: #f9fafb; border-bottom: 1px solid #e5e7eb;"
        )
        fl = QHBoxLayout(filter_panel)
        fl.setContentsMargins(16, 10, 16, 10)
        fl.setSpacing(16)

        # Keyword (description contains)
        kw_col = QVBoxLayout()
        kw_col.setSpacing(3)
        kw_col.addWidget(_lbl("Description contains"))
        self._kw_edit = QLineEdit()
        self._kw_edit.setPlaceholderText("Search keyword…")
        self._kw_edit.setFixedHeight(30)
        self._kw_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 4px; padding: 0 8px; }"
        )
        self._kw_edit.returnPressed.connect(self._do_find)
        kw_col.addWidget(self._kw_edit)

        # Truck
        truck_col = QVBoxLayout()
        truck_col.setSpacing(3)
        truck_col.addWidget(_lbl("Truck No."))
        self._truck_edit = QLineEdit()
        self._truck_edit.setPlaceholderText("e.g. T572 EQF")
        self._truck_edit.setFixedWidth(130)
        self._truck_edit.setFixedHeight(30)
        self._truck_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 4px; padding: 0 8px; }"
        )
        self._truck_edit.returnPressed.connect(self._do_find)
        truck_col.addWidget(self._truck_edit)

        # Date From
        from_col = QVBoxLayout()
        from_col.setSpacing(3)
        from_col.addWidget(_lbl("Date From"))
        self._from_edit = QDateEdit()
        self._from_edit.setCalendarPopup(True)
        self._from_edit.setDisplayFormat("dd/MM/yyyy")
        self._from_edit.setFixedWidth(120)
        self._from_edit.setFixedHeight(30)
        one_month_ago = date.today() - timedelta(days=30)
        self._from_edit.setDate(
            QDate(one_month_ago.year, one_month_ago.month, one_month_ago.day)
        )
        self._from_edit.setStyleSheet(
            "QDateEdit { border: 1px solid #d1d5db; border-radius: 4px; padding: 0 8px; }"
        )
        from_col.addWidget(self._from_edit)

        # Date To
        to_col = QVBoxLayout()
        to_col.setSpacing(3)
        to_col.addWidget(_lbl("Date To"))
        self._to_edit = QDateEdit()
        self._to_edit.setCalendarPopup(True)
        self._to_edit.setDisplayFormat("dd/MM/yyyy")
        self._to_edit.setFixedWidth(120)
        self._to_edit.setFixedHeight(30)
        self._to_edit.setDate(QDate.currentDate())
        self._to_edit.setStyleSheet(
            "QDateEdit { border: 1px solid #d1d5db; border-radius: 4px; padding: 0 8px; }"
        )
        to_col.addWidget(self._to_edit)

        # Buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.addStretch()

        self._find_btn = QPushButton("Find")
        self._find_btn.setFixedWidth(80)
        self._find_btn.setStyleSheet(_PRIMARY_BTN)
        self._find_btn.clicked.connect(self._do_find)

        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setFixedWidth(80)
        self._reset_btn.setStyleSheet(_BTN_STYLE)
        self._reset_btn.clicked.connect(self._reset_filters)

        btn_col.addWidget(self._find_btn)
        btn_col.addWidget(self._reset_btn)

        fl.addLayout(kw_col, 2)
        fl.addLayout(truck_col)
        fl.addLayout(from_col)
        fl.addLayout(to_col)
        fl.addLayout(btn_col)

        root.addWidget(filter_panel)

        # ── Results table ──────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Transaction ID", "Entries", "Refund to Float", "Total Amount"]
        )
        self._table.setStyleSheet(_TABLE_SS)
        self._table.setAlternatingRowColors(True)

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(True)
        for i in range(5):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        self._table.setColumnWidth(_COL_DATE,    110)
        self._table.setColumnWidth(_COL_TXN_ID,  150)
        self._table.setColumnWidth(_COL_ENTRIES,  80)
        self._table.setColumnWidth(_COL_REFUND,  160)
        self._table.setColumnWidth(_COL_TOTAL,   160)

        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.doubleClicked.connect(self._on_go_to)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        root.addWidget(self._table)

        # ── Status / action bar ────────────────────────────────────────
        action_bar = QWidget()
        action_bar.setFixedHeight(44)
        action_bar.setStyleSheet("background: #f9fafb; border-top: 1px solid #e5e7eb;")
        al = QHBoxLayout(action_bar)
        al.setContentsMargins(16, 0, 16, 0)
        al.setSpacing(8)

        self._count_label = QLabel("Days shown: —")
        self._count_label.setStyleSheet("color: #6b7280; font-size: 12px;")

        self._goto_btn = QPushButton("Go To Date")
        self._goto_btn.setFixedWidth(110)
        self._goto_btn.setStyleSheet(_PRIMARY_BTN)
        self._goto_btn.setEnabled(False)
        self._goto_btn.clicked.connect(self._on_go_to)

        self._export_btn = QPushButton("Export…")
        self._export_btn.setFixedWidth(90)
        self._export_btn.setStyleSheet(_BTN_STYLE)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export)

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.setStyleSheet(_BTN_STYLE)
        close_btn.clicked.connect(self.close)

        al.addWidget(self._count_label)
        al.addStretch()
        al.addWidget(self._goto_btn)
        al.addWidget(self._export_btn)
        al.addWidget(close_btn)

        root.addWidget(action_bar)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _do_find(self) -> None:
        self._find_btn.setEnabled(False)
        asyncio.ensure_future(self._async_find())

    async def _async_find(self) -> None:
        try:
            qfrom = self._from_edit.date()
            qto   = self._to_edit.date()
            d_from = date(qfrom.year(), qfrom.month(), qfrom.day())
            d_to   = date(qto.year(),   qto.month(),   qto.day())

            self._results = await get_daily_summaries(
                date_from=d_from,
                date_to=d_to,
                keyword=self._kw_edit.text().strip(),
                truck=self._truck_edit.text().strip(),
            )
            self._populate_results(self._results)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            self._find_btn.setEnabled(True)

    def _populate_results(self, summaries: List[dict]) -> None:
        self._table.setRowCount(len(summaries))
        for i, s in enumerate(summaries):
            d = s["date"]

            # Date column — full weekday for readability
            date_str = d.strftime("%a, %d %b %Y")
            self._table.setItem(i, _COL_DATE, _ro(date_str))

            # Transaction ID — TXN-YYYYMMDD
            txn_id = f"TXN-{d.strftime('%Y%m%d')}"
            txn_it = _ro(txn_id)
            txn_it.setFont(QFont("Consolas", 10))
            txn_it.setForeground(QColor("#0077C5"))
            self._table.setItem(i, _COL_TXN_ID, txn_it)

            # Entries count
            count_it = _ro(str(s["entries_count"]), Qt.AlignCenter)
            self._table.setItem(i, _COL_ENTRIES, count_it)

            # Refund to Float
            refund = s["total_refund"]
            refund_str = f"TZS {refund:,.0f}" if refund else "—"
            refund_it = _ro(refund_str, Qt.AlignRight | Qt.AlignVCenter)
            if refund:
                refund_it.setForeground(QColor("#EA580C"))
            self._table.setItem(i, _COL_REFUND, refund_it)

            # Total Amount
            total = s["total_tzs"]
            total_str = f"TZS {total:,.0f}" if total else "—"
            total_it = _ro(total_str, Qt.AlignRight | Qt.AlignVCenter)
            if total and total < 0:
                total_it.setForeground(QColor("#dc2626"))
            self._table.setItem(i, _COL_TOTAL, total_it)

        n = len(summaries)
        self._count_label.setText(f"Days shown: {n}")
        self._export_btn.setEnabled(n > 0)

    def _reset_filters(self) -> None:
        self._kw_edit.clear()
        self._truck_edit.clear()
        one_month_ago = date.today() - timedelta(days=30)
        self._from_edit.setDate(
            QDate(one_month_ago.year, one_month_ago.month, one_month_ago.day)
        )
        self._to_edit.setDate(QDate.currentDate())
        self._table.setRowCount(0)
        self._results = []
        self._count_label.setText("Days shown: —")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        self._goto_btn.setEnabled(bool(self._table.selectedItems()))

    def _on_go_to(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._results):
            return
        self.go_to_date.emit(self._results[row]["date"])
        self.close()

    def _on_export(self) -> None:
        """Copy daily summary results to clipboard as TSV."""
        lines = ["Date\tTransaction ID\tEntries\tRefund to Float (TZS)\tTotal Amount (TZS)"]
        for s in self._results:
            d = s["date"]
            lines.append("\t".join([
                d.strftime("%d/%m/%Y"),
                f"TXN-{d.strftime('%Y%m%d')}",
                str(s["entries_count"]),
                f"{s['total_refund']:.0f}" if s["total_refund"] else "0",
                f"{s['total_tzs']:.0f}"    if s["total_tzs"]    else "0",
            ]))
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(lines))
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Exported",
            f"{len(self._results)} days copied to clipboard.\nPaste directly into Excel.",
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def show_and_search(self) -> None:
        """Open the dialog and immediately run a search for the last 30 days."""
        self.show()
        self.raise_()
        self._do_find()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ro(text: str, align=Qt.AlignVCenter | Qt.AlignLeft) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    it.setTextAlignment(align)
    return it


def _lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size: 11px; color: #374151; font-weight: 500;")
    return lbl


# Backward-compat alias
TransactionsTable = TransactionBrowser
