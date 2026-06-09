"""
TransactionBrowser — QuickBooks-style "Find" dialog.

Shows a searchable, filterable list of transactions across all dates.
Clicking a row and pressing "Go To" navigates the register to that date.
"""

import asyncio
from datetime import date, timedelta
from typing import List

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QDateEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QFrame, QWidget,
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QFont

from tahmeed.models.transaction import Transaction
from tahmeed.services.cashier_service import search_transactions


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
    background: #2563eb;
    border: 1px solid #1d4ed8;
    border-radius: 5px;
    padding: 5px 18px;
    color: #ffffff;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover   { background: #1d4ed8; }
QPushButton:pressed { background: #1e40af; }
QPushButton:disabled { background: #93c5fd; border-color: #93c5fd; }
"""


class TransactionBrowser(QDialog):
    """
    Modeless dialog for searching and browsing transactions across dates.
    Emits go_to_date(date) when the user clicks 'Go To'.
    """

    go_to_date = Signal(object)   # passes a Python date object

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transaction Browser")
        self.setMinimumSize(960, 620)
        self.setWindowFlags(
            Qt.Dialog | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
        )
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self._results: List[Transaction] = []
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
        header.setStyleSheet(
            "background: #1e3a5f; border-bottom: 1px solid #1e3a5f;"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("Transaction Browser")
        title.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: 700;")
        hl.addWidget(title)
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

        # Description keyword
        desc_col = QVBoxLayout()
        desc_col.setSpacing(3)
        desc_col.addWidget(QLabel("Description"))
        self._kw_edit = QLineEdit()
        self._kw_edit.setPlaceholderText("Search keyword…")
        self._kw_edit.setFixedHeight(30)
        self._kw_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 4px; padding: 0 8px; }"
        )
        self._kw_edit.returnPressed.connect(self._do_find)
        desc_col.addWidget(self._kw_edit)

        # Truck
        truck_col = QVBoxLayout()
        truck_col.setSpacing(3)
        truck_col.addWidget(QLabel("Truck No."))
        self._truck_edit = QLineEdit()
        self._truck_edit.setPlaceholderText("e.g. T572 EQF")
        self._truck_edit.setFixedWidth(140)
        self._truck_edit.setFixedHeight(30)
        self._truck_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #d1d5db; border-radius: 4px; padding: 0 8px; }"
        )
        self._truck_edit.returnPressed.connect(self._do_find)
        truck_col.addWidget(self._truck_edit)

        # Date From
        from_col = QVBoxLayout()
        from_col.setSpacing(3)
        from_col.addWidget(QLabel("Date From"))
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
        to_col.addWidget(QLabel("Date To"))
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

        # Buttons (stacked vertically aligned to bottom)
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

        fl.addLayout(desc_col, 2)
        fl.addLayout(truck_col)
        fl.addLayout(from_col)
        fl.addLayout(to_col)
        fl.addLayout(btn_col)

        root.addWidget(filter_panel)

        # ── Results table ──────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels(
            ["Date", "Description", "Truck", "LPO / DO", "Memo", "TZS", "USD", "Rcpt"]
        )
        self._table.setStyleSheet("""
            QTableWidget {
                background: #ffffff;
                gridline-color: #e5e7eb;
                border: none;
                selection-background-color: #dbeafe;
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
        """)
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        for i in range(8):
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
        self._table.setColumnWidth(0, 90)    # Date
        self._table.setColumnWidth(1, 340)   # Description
        self._table.setColumnWidth(2, 84)    # Truck
        self._table.setColumnWidth(3, 88)    # LPO/DO
        self._table.setColumnWidth(4, 110)   # Memo
        self._table.setColumnWidth(5, 110)   # TZS
        self._table.setColumnWidth(6, 90)    # USD
        self._table.setColumnWidth(7, 50)    # Rcpt
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.doubleClicked.connect(self._on_go_to)
        root.addWidget(self._table)

        # ── Status / action bar ────────────────────────────────────────
        action_bar = QWidget()
        action_bar.setFixedHeight(44)
        action_bar.setStyleSheet(
            "background: #f9fafb; border-top: 1px solid #e5e7eb;"
        )
        al = QHBoxLayout(action_bar)
        al.setContentsMargins(16, 0, 16, 0)
        al.setSpacing(8)

        self._count_label = QLabel("Number of matches: —")
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

        # Enable Go To when selection changes
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

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

            self._results = await search_transactions(
                date_from=d_from,
                date_to=d_to,
                keyword=self._kw_edit.text().strip(),
                truck=self._truck_edit.text().strip(),
                limit=500,
            )
            self._populate_results(self._results)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Search Error", str(exc))
        finally:
            self._find_btn.setEnabled(True)

    def _populate_results(self, txs: List[Transaction]) -> None:
        self._table.setRowCount(len(txs))
        for i, tx in enumerate(txs):
            date_str = tx.date.strftime("%d/%m/%Y") if tx.date else ""
            self._table.setItem(i, 0, _ro(date_str))
            self._table.setItem(i, 1, _ro(tx.description))
            self._table.setItem(i, 2, _ro(tx.truck_number or ""))
            self._table.setItem(i, 3, _ro(tx.lpo_do or ""))
            self._table.setItem(i, 4, _ro(tx.memo or ""))

            tzs_str = f"{tx.amount:,.2f}" if tx.currency == "TZS" else ""
            usd_str = f"{tx.amount:,.2f}" if tx.currency == "USD" else ""

            tzs_it = _ro(tzs_str, Qt.AlignRight | Qt.AlignVCenter)
            if tx.amount < 0 and tx.currency == "TZS":
                tzs_it.setForeground(QColor("#dc2626"))
            self._table.setItem(i, 5, tzs_it)

            usd_it = _ro(usd_str, Qt.AlignRight | Qt.AlignVCenter)
            if tx.amount < 0 and tx.currency == "USD":
                usd_it.setForeground(QColor("#dc2626"))
            self._table.setItem(i, 6, usd_it)

            rcpt = "✓" if tx.receipt_status == "received" else ""
            rcpt_it = _ro(rcpt, Qt.AlignCenter)
            if rcpt:
                rcpt_it.setForeground(QColor("#16a34a"))
            self._table.setItem(i, 7, rcpt_it)

        self._count_label.setText(f"Number of matches: {len(txs)}")
        self._export_btn.setEnabled(len(txs) > 0)

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
        self._count_label.setText("Number of matches: —")
        self._goto_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

    def _on_selection_changed(self) -> None:
        self._goto_btn.setEnabled(bool(self._table.selectedItems()))

    def _on_go_to(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._results):
            return
        tx = self._results[row]
        tx_date = tx.date.date() if hasattr(tx.date, "date") else tx.date
        self.go_to_date.emit(tx_date)
        self.close()

    def _on_export(self) -> None:
        """Copy results to clipboard as TSV (paste into Excel)."""
        lines = ["Date\tDescription\tTruck\tLPO/DO\tMemo\tTZS\tUSD\tReceipt"]
        for tx in self._results:
            tzs = f"{tx.amount:.2f}" if tx.currency == "TZS" else ""
            usd = f"{tx.amount:.2f}" if tx.currency == "USD" else ""
            rcpt = "received" if tx.receipt_status == "received" else ""
            lines.append("\t".join([
                tx.date.strftime("%d/%m/%Y") if tx.date else "",
                tx.description,
                tx.truck_number or "",
                tx.lpo_do or "",
                tx.memo or "",
                tzs, usd, rcpt,
            ]))
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(lines))
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Exported",
            f"{len(self._results)} rows copied to clipboard.\nPaste directly into Excel.",
        )

    # ------------------------------------------------------------------
    # Public: show and auto-search
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


# Backward-compat alias
TransactionsTable = TransactionBrowser
