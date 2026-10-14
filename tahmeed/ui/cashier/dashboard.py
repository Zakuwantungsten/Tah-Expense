"""
CashierDashboard — Accountant-style chrome + sidebar navigation.

Layout:
  ┌─ Title bar (TAHMEED COACH — Cashier Edition) ──────────────────────────────┐
  ├─ Menu bar (File / Edit / View / Cashier / Reports / Window / Help) ────────┤
  ├─ Connectivity banner ──────────────────────────────────────────────────────┤
  ┌─ Sidebar (220px, navy #1B2B4B, collapsible) ─┬─ Content Area ──────────────┐
  │  Overview  ·  Browse                         │  [Stacked pages]            │
  │  ENTRY / INBOX / ITEMS                       │                             │
  │  [Collapse]                                  │                             │
  └──────────────────────────────────────────────┴─────────────────────────────┘
  ── Status bar (Cashier Mode) ─────────────────────────────────────────────────
"""

import asyncio
from datetime import date
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QFrame, QLabel, QLineEdit, QPushButton, QDialog, QMessageBox,
    QSizePolicy, QDateEdit,
)
from PySide6.QtCore import Qt, Signal, QDate, QTimer

from tahmeed.config import APP_NAME, APP_VERSION, COMPANY_NAME
from tahmeed.models.user import User
from tahmeed.services.category_service import get_payment_target_categories
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.accountant.title_bar import AccountantTitleBar
from tahmeed.ui.dialogs.change_password_dialog import ChangePasswordDialog
from tahmeed.ui.cashier.menu_bar import CashierMenuBar
from tahmeed.ui.cashier.sidebar import CashierSidebarWidget
from tahmeed.ui.cashier.excel_grid import DailyRegister
from tahmeed.ui.cashier.entry_form import EntryForm
from tahmeed.ui.cashier.overview import CashierOverview
from tahmeed.ui.cashier.transactions_table import TransactionBrowser
from tahmeed.ui.cashier.cashier_category_view import CashierCategoryView
from tahmeed.ui.cashier.rejected_view import RejectedView
from tahmeed.ui.cashier.drafts_view import DraftsView, fetch_draft_badge_count
from tahmeed.ui.cashier.saved_view import SavedView
from tahmeed.ui.cashier.day_issues import DayIssuesWidget
from tahmeed.ui.cashier.trash import CashierTrashWidget

_APP_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_WHITE  = "#FFFFFF"
_BLUE   = "#0077C5"
_NAVY   = "#1B2B4B"
_GOLD   = "#B18E5E"
_T1     = "#111827"
_T2     = "#6B7280"
_BTN_H  = 30  # slim professional control height

# ── Button styles ────────────────────────────────────────────────────────────────
# Filled primary (Save), outlined secondaries, warm cancel, gold submit.
# Selectors cover QPushButton (Search) and QToolButton (Export…Submit).
_BTN_BASE = (
    f"border-radius:5px;font-size:12px;font-weight:600;"
    f"font-family:'Segoe UI',sans-serif;padding:0 12px;min-height:{_BTN_H}px;"
)


def _btn_style(kind: str) -> str:
    if kind == "primary":
        bg, border, hover, pressed, disabled = (
            _BLUE, _BLUE, "#0369A1", "#075985",
            "background:#93C5FD;border-color:#93C5FD;color:#EFF6FF;",
        )
        color = "#FFFFFF"
    elif kind == "submit":
        bg, border, hover, pressed, disabled = (
            _GOLD, _GOLD, "#9A784C", "#84653F",
            "background:#D6C4A8;border-color:#D6C4A8;color:#FFF8EF;",
        )
        color = "#FFFFFF"
    elif kind == "active":
        bg, border, hover, pressed, disabled = (
            "#D97706", "#D97706", "#B45309", "#92400E",
            "color:#9CA3AF;border-color:#E5E7EB;",
        )
        color = "#FFFFFF"
    else:  # secondary
        bg, border, hover, pressed, disabled = (
            "#FFFFFF", "#D1D5DB", "#F9FAFB", "#F3F4F6",
            "color:#9CA3AF;border-color:#E5E7EB;",
        )
        color = "#374151"
        hover = "#F9FAFB"
        # secondary hover also tweaks border
        return (
            f"QPushButton, QToolButton{{background:{bg};color:{color};"
            f"border:1px solid {border};{_BTN_BASE}}}"
            f"QPushButton:hover, QToolButton:hover{{background:{hover};border-color:#9CA3AF;}}"
            f"QPushButton:pressed, QToolButton:pressed{{background:{pressed};}}"
            f"QPushButton:disabled, QToolButton:disabled{{{disabled}}}"
        )
    return (
        f"QPushButton, QToolButton{{background:{bg};color:{color};"
        f"border:1px solid {border};{_BTN_BASE}}}"
        f"QPushButton:hover, QToolButton:hover{{background:{hover};border-color:{hover};}}"
        f"QPushButton:pressed, QToolButton:pressed{{background:{pressed};border-color:{pressed};}}"
        f"QPushButton:disabled, QToolButton:disabled{{{disabled}}}"
    )


_BTN_STYLES = {
    "primary": _btn_style("primary"),
    "secondary": _btn_style("secondary"),
    "active": _btn_style("active"),
    "submit": _btn_style("submit"),
}


# ── QuickBooks-style document header ─────────────────────────────────────────────

class _QBDocHeader(QFrame):
    """Document summary header: title + Merged badge + 4 KPI tiles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("qbDocHeader")
        self.setFixedHeight(72)
        self.setStyleSheet(
            "QFrame#qbDocHeader {"
            "  background: #ffffff;"
            "  border-bottom: 2px solid #0077C5;"
            "}"
        )
        self._view_date: Optional[date] = date.today()

        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 16, 0)
        hl.setSpacing(0)

        accent = QFrame()
        accent.setFixedWidth(5)
        accent.setStyleSheet("background: #0077C5; border: none;")
        hl.addWidget(accent)
        hl.addSpacing(14)

        # Title block + optional Merged badge
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.setContentsMargins(0, 0, 0, 0)

        title_vl = QVBoxLayout()
        title_vl.setSpacing(2)
        title_vl.setContentsMargins(0, 10, 0, 10)
        title_lbl = QLabel("Daily Register")
        title_lbl.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {_NAVY};"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        sub_lbl = QLabel("Cashier  ·  Expense Entry")
        sub_lbl.setStyleSheet(
            "font-size: 10px; color: #9CA3AF;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        title_vl.addWidget(title_lbl)
        title_vl.addWidget(sub_lbl)

        title_row.addLayout(title_vl)

        self._merged_badge = QLabel("MERGED VIEW")
        self._merged_badge.setAlignment(Qt.AlignCenter)
        self._merged_badge.setFixedHeight(22)
        self._merged_badge.setStyleSheet(
            f"QLabel{{background:transparent;color:{_GOLD};border:1.5px solid {_GOLD};"
            f"border-radius:11px;padding:0 10px;font-size:10px;font-weight:700;"
            f"letter-spacing:0.6px;font-family:'Segoe UI',sans-serif;}}"
        )
        self._merged_badge.hide()
        title_row.addWidget(self._merged_badge, 0, Qt.AlignVCenter)

        self._draft_status = QLabel("")
        self._draft_status.setAlignment(Qt.AlignCenter)
        self._draft_status.setFixedHeight(22)
        self._draft_status.setStyleSheet(
            "QLabel{background:transparent;color:#B45309;border:1.5px solid #FCD34D;"
            "border-radius:11px;padding:0 10px;font-size:10px;font-weight:700;"
            "letter-spacing:0.4px;font-family:'Segoe UI',sans-serif;}"
        )
        self._draft_status.hide()
        title_row.addWidget(self._draft_status, 0, Qt.AlignVCenter)

        title_row.addStretch()

        hl.addLayout(title_row, 1)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.VLine)
        sep0.setStyleSheet("color: #E5E7EB;")
        hl.addWidget(sep0)

        # 5 KPI tiles — TOTAL USD sits immediately before TOTAL (TZS)
        tiles_cfg = [
            ("ENTRIES",         "—",                              "#6B7280", "bold"),
            ("TODAY",           date.today().strftime("%d %b %Y"), "#6B7280", "bold"),
            ("REFUND TO FLOAT", "—",                              "#EA580C", "normal"),
            ("TOTAL USD",       "—",                              "#6B7280", "bold"),
            ("TOTAL",           "—",                              "#6B7280", "bold"),
        ]
        self._lbl_date = None
        val_labels = []
        for i, (label, init_val, lbl_color, weight) in enumerate(tiles_cfg):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #E5E7EB;")
                hl.addWidget(sep)

            tile = QWidget()
            tile.setFixedWidth(148)
            tile_vl = QVBoxLayout(tile)
            tile_vl.setContentsMargins(14, 10, 14, 10)
            tile_vl.setSpacing(3)

            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"font-size: 9px; color: {lbl_color}; font-weight: 600;"
                " letter-spacing: 0.6px; font-family: 'Segoe UI', sans-serif;"
                " background: transparent;"
            )
            val = QLabel(init_val)
            fw = "700" if weight == "bold" else "500"
            val_color = "#EA580C" if label == "REFUND TO FLOAT" else "#111827"
            val.setStyleSheet(
                f"font-size: 13px; color: {val_color}; font-weight: {fw};"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
            tile_vl.addWidget(lbl)
            tile_vl.addWidget(val)
            if label == "TODAY":
                self._lbl_date = lbl
            val_labels.append(val)
            hl.addWidget(tile)

        (
            self._val_entries,
            self._val_today,
            self._val_refund,
            self._val_total_usd,
            self._val_total,
        ) = val_labels

    def set_merged(self, merged: bool) -> None:
        self._merged_badge.setVisible(bool(merged))

    def set_register_status(self, draft_count: int, submitted_count: int) -> None:
        parts = []
        if draft_count:
            parts.append(f"{draft_count} draft")
        if submitted_count:
            parts.append(f"{submitted_count} submitted")
        if parts:
            self._draft_status.setText(" · ".join(parts))
            self._draft_status.show()
        else:
            self._draft_status.hide()

    def update_stats(
        self,
        n_entries: int,
        total_tzs: float,
        total_usd: float,
        refund_total: float,
        register_date=None,
    ) -> None:
        self._val_entries.setText(f"{n_entries} entr{'y' if n_entries == 1 else 'ies'}")
        self._val_total_usd.setText(f"USD {total_usd:,.2f}" if total_usd else "—")
        self._val_total.setText(f"TZS {total_tzs:,.0f}" if total_tzs else "—")
        self._val_refund.setText(f"TZS {refund_total:,.0f}" if refund_total else "—")
        if register_date is None:
            self.clear_view_date()
        else:
            self.set_view_date(register_date)

    def clear_view_date(self) -> None:
        """Blank New table — no reconciled day yet."""
        self._view_date = None
        self._val_today.setText("—")
        if self._lbl_date is not None:
            self._lbl_date.setText("DATE")

    def set_view_date(self, d: date) -> None:
        """Show the register's calendar day — label is TODAY only when it is today."""
        if d is None:
            self.clear_view_date()
            return
        if hasattr(d, "date"):
            d = d.date()
        self._view_date = d
        self._val_today.setText(d.strftime("%d %b %Y"))
        if self._lbl_date is not None:
            if d == date.today():
                self._lbl_date.setText("TODAY")
            else:
                # Weekday + DATE so past/future days never read as "today"
                self._lbl_date.setText(d.strftime("%a").upper())


# ── Cheque inline label + field helpers ──────────────────────────────────────

def _qb_field_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "QLabel{"
        "  font-size: 11px; font-weight: 600; color: #6B7280;"
        "  font-family: 'Segoe UI', sans-serif;"
        "  background: transparent;"
        "}"
    )
    return lbl


def _qb_field_input(placeholder: str = "", *, width: int | None = None) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setFixedHeight(_BTN_H)
    if width is not None:
        edit.setFixedWidth(width)
    edit.setStyleSheet(
        "QLineEdit {"
        "  border: 1px solid #D1D5DB; border-radius: 5px;"
        "  padding: 0 8px; font-size: 12px;"
        "  color: #111827; background: #F3F4F6;"
        "  font-family: 'Segoe UI', sans-serif;"
        "}"
        "QLineEdit:focus {"
        "  border-color: #0077C5; background: #FFFFFF;"
        "}"
        "QLineEdit:read-only {"
        "  color: #6B7280; background: #F9FAFB;"
        "}"
    )
    return edit


def _qb_inline_field(label: str, placeholder: str, width: int) -> tuple[QWidget, QLineEdit]:
    """Label and input packed tight: Cheque [____]"""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    wrap.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    lbl = _qb_field_label(label)
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    row.addWidget(lbl, 0, Qt.AlignVCenter)
    edit = _qb_field_input(placeholder, width=width)
    edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    row.addWidget(edit, 0, Qt.AlignVCenter)
    return wrap, edit


# Sentinel so QDateEdit can show "-" via specialValueText (date == minimumDate).
_RECONCILED_DATE_UNSET = QDate(1000, 1, 1)


def _qb_inline_date_field(label: str) -> tuple[QWidget, QDateEdit]:
    """Label + calendar: Reconciled Date [____] (shows "-" when unset)."""
    wrap = QWidget()
    wrap.setStyleSheet("background: transparent;")
    wrap.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    lbl = _qb_field_label(label)
    lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    row.addWidget(lbl, 0, Qt.AlignVCenter)
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd MMM yyyy")
    edit.setMinimumDate(_RECONCILED_DATE_UNSET)
    edit.setSpecialValueText("-")
    edit.setDate(QDate.currentDate())
    edit.setFixedHeight(_BTN_H)
    edit.setFixedWidth(132)
    edit.setStyleSheet(
        "QDateEdit {"
        "  border: 1px solid #D1D5DB; border-radius: 5px;"
        "  padding: 0 6px; font-size: 12px;"
        "  color: #111827; background: #F3F4F6;"
        "  font-family: 'Segoe UI', sans-serif;"
        "}"
        "QDateEdit:focus {"
        "  border-color: #0077C5; background: #FFFFFF;"
        "}"
        "QDateEdit:read-only {"
        "  color: #6B7280; background: #F9FAFB;"
        "}"
    )
    style_calendar_popup(edit)
    edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    row.addWidget(edit, 0, Qt.AlignVCenter)
    return wrap, edit


# ── Action bar (mode / search / cheque) ─────────────────────────────────────

class _ActionBar(QFrame):
    """My entries / Merged / Search on the left; Reconciled Date / Cheque on the right."""

    search_changed = Signal(str)
    mode_changed   = Signal(bool)  # True = Merged
    cheque_edited  = Signal(str)
    reconciled_date_changed = Signal(object)  # date

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("registerActionBar")
        self.setFixedHeight(44)
        self.setStyleSheet(
            "QFrame#registerActionBar {"
            f"  background: {_WHITE}; border-bottom: 1px solid {_BORDER};"
            "}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 6, 16, 6)
        hl.setSpacing(8)

        # My / Merged segmented toggle
        toggle = QFrame()
        toggle.setObjectName("modeToggle")
        toggle.setStyleSheet(
            "QFrame#modeToggle{background:transparent;border:none;}"
        )
        tg = QHBoxLayout(toggle)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setSpacing(0)

        self._my_btn = QPushButton("My entries")
        self._merged_btn = QPushButton("Merged")
        _toggle_ss = (
            "QPushButton{"
            " background:#FFFFFF;border:1px solid #D1D5DB;"
            " padding:0 14px;font-size:12px;font-weight:600;color:#6B7280;"
            f" min-height:{_BTN_H}px;"
            "}"
            f"QPushButton:checked{{background:{_NAVY};border-color:{_NAVY};color:#FFFFFF;}}"
            "QPushButton:hover:!checked{background:#F9FAFB;color:#374151;}"
        )
        self._my_btn.setCheckable(True)
        self._merged_btn.setCheckable(True)
        self._my_btn.setCursor(Qt.PointingHandCursor)
        self._merged_btn.setCursor(Qt.PointingHandCursor)
        self._my_btn.setFixedHeight(_BTN_H)
        self._merged_btn.setFixedHeight(_BTN_H)
        self._my_btn.setStyleSheet(
            _toggle_ss
            + "QPushButton{"
            " border-top-left-radius:5px;border-bottom-left-radius:5px;"
            " border-top-right-radius:0;border-bottom-right-radius:0;"
            "}"
        )
        self._merged_btn.setStyleSheet(
            _toggle_ss
            + "QPushButton{"
            " border-top-left-radius:0;border-bottom-left-radius:0;"
            " border-top-right-radius:5px;border-bottom-right-radius:5px;"
            " border-left:none;"
            "}"
        )
        self._my_btn.setChecked(True)
        self._my_btn.clicked.connect(lambda: self._set_mode(False))
        self._merged_btn.clicked.connect(lambda: self._set_mode(True))
        tg.addWidget(self._my_btn)
        tg.addWidget(self._merged_btn)
        hl.addWidget(toggle, 0, Qt.AlignVCenter)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search entries…")
        self._search.setFixedWidth(220)
        self._search.setFixedHeight(_BTN_H)
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            "QLineEdit {"
            "  border: 1px solid #D1D5DB; border-radius: 5px;"
            "  padding: 0 10px; font-size: 12px;"
            "  color: #111827; background: #F9FAFB;"
            "}"
            "QLineEdit:focus {"
            "  border-color: #0077C5; background: #ffffff;"
            "}"
        )
        hl.addWidget(self._search, 0, Qt.AlignVCenter)

        self._search_clear_btn = QPushButton("Search")
        self._search_clear_btn.setFixedHeight(_BTN_H)
        self._search_clear_btn.setFixedWidth(68)
        self._search_clear_btn.setCursor(Qt.PointingHandCursor)
        self._search_clear_btn.setStyleSheet(_BTN_STYLES["secondary"])
        self._search_clear_btn.setToolTip("Clear the table search filter")

        self._search.textChanged.connect(self._on_search_text_changed)
        self._search_clear_btn.clicked.connect(self._on_search_clear_clicked)
        hl.addWidget(self._search_clear_btn, 0, Qt.AlignVCenter)

        self._status = QLabel("")
        self._status.setStyleSheet(
            "QLabel{background:#FEF3C7;color:#92400E;border:1px solid #FDE68A;"
            "border-radius:5px;padding:2px 10px;font-size:11px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;}"
        )
        self._status.hide()
        hl.addWidget(self._status, 0, Qt.AlignVCenter)

        hl.addStretch(1)

        # Compact group so labels stay next to their inputs on the right
        fields = QWidget()
        fields.setStyleSheet("background: transparent;")
        fields.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        fields_hl = QHBoxLayout(fields)
        fields_hl.setContentsMargins(0, 0, 0, 0)
        fields_hl.setSpacing(14)

        date_wrap, self._reconciled = _qb_inline_date_field("Reconciled Date")
        self._reconciled.dateChanged.connect(self._on_reconciled_date)
        fields_hl.addWidget(date_wrap)

        cheque_wrap, self._cheque = _qb_inline_field("Cheque", "Cheque no.…", 100)
        self._cheque.textEdited.connect(self.cheque_edited.emit)
        fields_hl.addWidget(cheque_wrap)

        hl.addWidget(fields, 0, Qt.AlignVCenter)

    def _on_search_text_changed(self, text: str) -> None:
        self.search_changed.emit(text)
        has_text = bool((text or "").strip())
        self._search_clear_btn.setText("Clear" if has_text else "Search")
        self._search_clear_btn.setStyleSheet(
            _BTN_STYLES["active"] if has_text else _BTN_STYLES["secondary"]
        )

    def _on_search_clear_clicked(self) -> None:
        if self._search.text():
            # clear() emits textChanged → set_search("") on the register.
            self._search.clear()
        else:
            self._search.setFocus()

    def _on_reconciled_date(self, qd: QDate) -> None:
        if not qd.isValid() or qd == _RECONCILED_DATE_UNSET:
            return
        self.reconciled_date_changed.emit(date(qd.year(), qd.month(), qd.day()))

    def set_reconciled_date(self, d) -> None:
        """Sync the header calendar with the open Simple day without navigating.

        ``None`` shows "-" (unset reconciled date after New table).
        """
        if d is None:
            if self._reconciled.date() == _RECONCILED_DATE_UNSET:
                return
            self._reconciled.blockSignals(True)
            self._reconciled.setDate(_RECONCILED_DATE_UNSET)
            self._reconciled.blockSignals(False)
            return
        if hasattr(d, "date"):
            d = d.date()
        qd = QDate(d.year, d.month, d.day)
        if self._reconciled.date() == qd:
            return
        self._reconciled.blockSignals(True)
        self._reconciled.setDate(qd)
        self._reconciled.blockSignals(False)

    def set_cheque_value(self, cheque: str, editable: bool) -> None:
        """Refresh day-level Cheque header field without emitting edits."""
        self._cheque.blockSignals(True)
        if self._cheque.text() != (cheque or ""):
            self._cheque.setText(cheque or "")
        self._cheque.setReadOnly(not editable)
        self._cheque.blockSignals(False)

    def _set_mode(self, merged: bool) -> None:
        self._my_btn.setChecked(not merged)
        self._merged_btn.setChecked(merged)
        self.mode_changed.emit(merged)

    def sync_mode(self, merged: bool) -> None:
        """Reflect register mode without emitting (e.g. after cancel)."""
        self._my_btn.blockSignals(True)
        self._merged_btn.blockSignals(True)
        self._my_btn.setChecked(not merged)
        self._merged_btn.setChecked(merged)
        self._my_btn.blockSignals(False)
        self._merged_btn.blockSignals(False)

    def set_edit_state(self, active: bool, dirty_count: int = 0) -> None:
        """Show edit-mode status pill next to search."""
        if active:
            plural = "" if dirty_count == 1 else "s"
            self._status.setText(f"Edit mode  ·  {dirty_count} unsaved change{plural}")
            self._status.show()
        else:
            self._status.hide()


class _TablePage(QWidget):
    """QB icon toolbar → Daily Register totals → My/Merged/Search/Cheque → grid."""

    def __init__(self, register: DailyRegister, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._doc_header = _QBDocHeader()
        register.stats_updated.connect(self._doc_header.update_stats)
        register.register_status_updated.connect(self._doc_header.set_register_status)

        from tahmeed.ui.widgets.qb_txn_toolbar import QbTxnToolbar
        self._qb_toolbar = QbTxnToolbar(register_actions=True)
        self._qb_toolbar.undo_clicked.connect(register.toolbar_undo)
        self._qb_toolbar.redo_clicked.connect(register.toolbar_redo)
        self._qb_toolbar.find_prev.connect(lambda: register.toolbar_find(-1))
        self._qb_toolbar.find_next.connect(lambda: register.toolbar_find(1))
        self._qb_toolbar.new_clicked.connect(register.toolbar_new_table)
        self._qb_toolbar.save_clicked.connect(register.save_rows)
        self._qb_toolbar.delete_clicked.connect(register.toolbar_delete)
        self._qb_toolbar.clear_table_clicked.connect(register.toolbar_clear_table)
        self._qb_toolbar.copy_clicked.connect(register.toolbar_copy_row)
        self._qb_toolbar.print_clicked.connect(register.toolbar_print)
        self._qb_toolbar.attach_clicked.connect(register.toolbar_attach)
        self._qb_toolbar.export_clicked.connect(register.export_as)
        self._qb_toolbar.import_clicked.connect(register.import_from_file)
        self._qb_toolbar.day_issues_clicked.connect(
            lambda: register.day_issues_requested.emit(register.current_date())
        )
        self._qb_toolbar.today_clicked.connect(
            lambda: register.navigate_to_date(date.today())
        )
        self._qb_toolbar.edit_clicked.connect(register.toggle_edit_mode)
        self._qb_toolbar.submit_clicked.connect(register.submit_for_verify)
        self._qb_toolbar.font_larger_clicked.connect(register.toolbar_font_larger)
        self._qb_toolbar.font_smaller_clicked.connect(register.toolbar_font_smaller)
        self._qb_toolbar.wrap_clicked.connect(register.toolbar_toggle_wrap)
        register.font_size_changed.connect(
            lambda larger, smaller: self._qb_toolbar.set_font_size_controls_enabled(
                can_larger=larger, can_smaller=smaller
            )
        )
        register.wrap_state_changed.connect(self._qb_toolbar.set_wrap_active)
        self._qb_toolbar.set_font_size_controls_enabled(
            can_larger=register.can_increase_font(),
            can_smaller=register.can_decrease_font(),
        )
        self._qb_toolbar.set_wrap_active(register.wrap_is_active())
        register.attachment_count_changed.connect(self._qb_toolbar.set_attachment_count)
        register.edit_state_changed.connect(self._qb_toolbar.set_edit_state)
        register.save_busy_changed.connect(self._qb_toolbar.set_mutation_busy)
        register.save_busy_changed.connect(
            lambda busy: (
                None if busy
                else self._qb_toolbar.set_undo_redo_enabled(
                    can_undo=bool(register._undo_stack),
                    can_redo=bool(register._redo_stack),
                )
            )
        )
        register.undo_redo_changed.connect(
            lambda u, r: self._qb_toolbar.set_undo_redo_enabled(can_undo=u, can_redo=r)
        )
        self._qb_toolbar.set_undo_redo_enabled(
            can_undo=bool(register._undo_stack),
            can_redo=bool(register._redo_stack),
        )

        self._register = register
        self._action_bar = _ActionBar()
        self._action_bar.search_changed.connect(register.set_search)
        self._action_bar.mode_changed.connect(register.set_merged_mode)
        self._action_bar.cheque_edited.connect(register.set_active_cheque)
        self._action_bar.reconciled_date_changed.connect(self._on_reconciled_date)
        register.edit_state_changed.connect(self._action_bar.set_edit_state)
        register.mode_changed.connect(self._action_bar.sync_mode)
        register.mode_changed.connect(self._doc_header.set_merged)
        register.active_cheque_changed.connect(self._action_bar.set_cheque_value)
        register.stats_updated.connect(self._sync_reconciled_date)
        self._action_bar.set_reconciled_date(register.current_date())

        # 1) Icon toolbar (incl. Export/Import/Today/Edit/Submit)
        # 2) Daily Register + totals
        # 3) My/Merged + Search  ···  Reconciled Date / Cheque
        vl.addWidget(self._qb_toolbar)
        vl.addWidget(self._doc_header)
        vl.addWidget(self._action_bar)
        vl.addWidget(register, 1)

    def _sync_reconciled_date(self, *_args) -> None:
        self._action_bar.set_reconciled_date(self._register.current_date())

    def _on_reconciled_date(self, d) -> None:
        if not self._register.navigate_to_date(d):
            self._action_bar.set_reconciled_date(self._register.current_date())

    def set_chrome_maximized(self, maximized: bool) -> None:
        """Show toolbar labels only when the host window is maximized."""
        self._qb_toolbar.set_compact(not bool(maximized))


# ── CashierDashboard ─────────────────────────────────────────────────────────────

class CashierDashboard(QWidget):
    logout_requested = Signal()

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user = user
        self._category_indices: dict[str, int] = {}
        self._day_issues_badge_timer = QTimer(self)
        self._day_issues_badge_timer.setSingleShot(True)
        self._day_issues_badge_timer.setInterval(350)
        self._day_issues_badge_timer.timeout.connect(self._run_debounced_day_issues_badge)
        self._build_ui()
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_categories())

    def _build_ui(self) -> None:
        self.setObjectName("cashierDashboard")
        self.setStyleSheet(
            f"QWidget#cashierDashboard {{ background: {_APP_BG}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = CashierSidebarWidget(user=self._user)
        self._sidebar.nav_selected.connect(self._on_nav)

        # ── QuickBooks-style chrome (matches Accountant Edition) ─────────────
        self._title_bar = AccountantTitleBar(
            user=self._user,
            edition="Cashier Edition",
        )
        self._title_bar.minimize_requested.connect(self._on_minimize)
        self._title_bar.maximize_requested.connect(self._on_maximize)
        self._title_bar.close_requested.connect(self._on_exit)
        root.addWidget(self._title_bar)

        self._menu_bar = CashierMenuBar(
            user_display_name=self._user.full_name or self._user.username,
        )
        self._menu_bar.bind(
            navigate=self._menu_navigate,
            toggle_sidebar=self._toggle_sidebar,
            refresh=self._refresh_current,
            change_password=self._on_change_password,
            logout=self.logout_requested.emit,
            exit_app=self._on_exit,
            about=self._on_about,
            find=self._on_find,
            register_help=self._on_register_save_help,
        )
        self._sidebar.items_changed.connect(self._menu_bar.set_item_entries)
        root.addWidget(self._menu_bar)

        from tahmeed.ui.widgets.connectivity_banner import ConnectivityBanner
        from tahmeed.ui.widgets.live_status_bar import LiveStatusBar

        self._connectivity_banner = ConnectivityBanner()
        root.addWidget(self._connectivity_banner)

        # ── Body = sidebar + content ───────────────────────────────────────────
        body = QWidget()
        body.setObjectName("cashierBody")
        body.setStyleSheet(f"QWidget#cashierBody {{ background: {_APP_BG}; }}")
        body_hl = QHBoxLayout(body)
        body_hl.setContentsMargins(0, 0, 0, 0)
        body_hl.setSpacing(0)

        body_hl.addWidget(self._sidebar)

        vline = QFrame()
        vline.setFrameShape(QFrame.VLine)
        vline.setFixedWidth(1)
        vline.setStyleSheet("background: #E5E7EB;")
        body_hl.addWidget(vline)

        # ── Stacked content ────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setObjectName("contentStack")
        self._stack.setStyleSheet(
            f"QStackedWidget#contentStack {{ background: {_APP_BG}; }}"
        )

        # index 0 — Overview
        self._overview = CashierOverview(user=self._user)
        self._stack.addWidget(self._overview)

        # index 1 — Table (action bar + DailyRegister)
        self._register = DailyRegister(user=self._user, categories=[])
        self._table_page = _TablePage(self._register)
        self._stack.addWidget(self._table_page)

        # index 2 — Entry Form
        self._form = EntryForm(user=self._user)
        self._stack.addWidget(self._form)

        # index 3 — Drafts inbox
        self._drafts_view = DraftsView(user=self._user, show_all_cashiers=False)
        self._stack.addWidget(self._drafts_view)

        # index 4 — Saved (flat saved-but-not-submitted list)
        self._saved_view = SavedView(user=self._user, show_all_cashiers=False)
        self._stack.addWidget(self._saved_view)

        # index 5 — Day Issues
        self._day_issues = DayIssuesWidget(
            user=self._user,
            row_source=self._register.day_issues_row_source,
            apply_live=self._register.apply_description_mapping_to_open_rows,
        )
        self._day_issues.open_register.connect(self._on_day_issues_open_register)
        self._day_issues.issues_changed.connect(self._on_day_issues_badge)
        self._day_issues.auto_fill_maps_changed.connect(
            self._register.set_auto_fill_from_maps
        )
        # Badge updates via issues_changed after map; do not re-collect.
        self._stack.addWidget(self._day_issues)

        # index 6 — Rejected entries
        self._rejected_view = RejectedView(user=self._user)
        self._stack.addWidget(self._rejected_view)

        # index 7 — Browse (embedded)
        self._browser = TransactionBrowser(can_manage_uploads=False)
        self._browser.go_to_date.connect(self._on_go_to_date)
        self._browser.go_to_upload.connect(self._on_go_to_upload)
        self._stack.addWidget(self._browser)

        # index 8 — Trash (own soft-trash + accountant-confirmed)
        self._trash_view = CashierTrashWidget(user=self._user)
        self._trash_view.count_changed.connect(self._on_trash_badge)
        self._stack.addWidget(self._trash_view)

        self._stack.setCurrentIndex(0)
        body_hl.addWidget(self._stack, 1)

        root.addWidget(body, 1)
        root.addWidget(
            LiveStatusBar(
                object_name="cashierStatusBar",
                mode_label="Cashier Mode",
                dark=False,
            )
        )

        # ── Signal wiring ──────────────────────────────────────────────────────
        self._form.transaction_saved.connect(lambda _: self._register.refresh())
        self._form.trash_changed.connect(self._on_register_trash_changed)
        self._drafts_view.open_register_date.connect(self._on_drafts_open_register)
        self._drafts_view.drafts_changed.connect(self._on_drafts_data_changed)
        self._saved_view.open_merged_date.connect(self._on_saved_open_merged)
        self._saved_view.drafts_changed.connect(self._on_drafts_data_changed)
        from tahmeed.ui.async_utils import schedule_coro

        self._register.rows_saved.connect(
            lambda _: schedule_coro(self._refresh_draft_badge())
        )
        self._register.drafts_changed.connect(
            lambda: schedule_coro(self._refresh_draft_badge())
        )
        self._register.rows_saved.connect(lambda _: self._saved_view.refresh())
        self._register.drafts_changed.connect(self._saved_view.refresh)
        self._register.day_issues_requested.connect(self._on_open_day_issues)
        self._register.rows_saved.connect(
            lambda _: self._on_day_issues_data_changed()
        )
        self._register.drafts_changed.connect(self._on_day_issues_data_changed)
        self._register.day_issues_source_changed.connect(self._on_day_issues_source_changed)
        self._register.trash_changed.connect(self._on_register_trash_changed)

        schedule_coro(self._refresh_draft_badge())
        schedule_coro(self._refresh_day_issues_badge())
        schedule_coro(self._refresh_trash_badge())


    def _on_import(self) -> None:
        self._on_nav("table")
        asyncio.ensure_future(self._register._run_daily_import())

    async def prepare_to_leave(self) -> bool:
        """Prompt to save/discard unsaved table entries before logout or exit."""
        return await self._register.confirm_leave()

    # ── Chrome helpers ──────────────────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        self._sidebar.toggle_collapsed()

    def _host_window(self):
        return self.window()

    def _on_minimize(self) -> None:
        win = self._host_window()
        if win is not None:
            win.showMinimized()

    def _on_maximize(self) -> None:
        win = self._host_window()
        if win is None:
            return
        if win.isMaximized():
            win.showNormal()
            self._title_bar.set_maximized(False)
        else:
            win.showMaximized()
            self._title_bar.set_maximized(True)

    def _on_exit(self) -> None:
        win = self._host_window()
        if win is not None:
            win.close()

    def _menu_navigate(self, key: str) -> None:
        self._on_nav(key)

    def _refresh_current(self) -> None:
        widget = self._stack.currentWidget()
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()

    def sync_chrome_maximized(self, maximized: bool) -> None:
        """Keep title-bar restore/maximize icon in sync with the host window."""
        self._title_bar.set_maximized(maximized)
        self._table_page.set_chrome_maximized(maximized)
        form_tb = getattr(self._form, "_qb_toolbar", None)
        if form_tb is not None and hasattr(form_tb, "set_compact"):
            form_tb.set_compact(not bool(maximized))

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            (
                f"<b>{APP_NAME}</b><br>"
                f"Cashier Edition<br><br>"
                f"Version {APP_VERSION}<br>"
                f"{COMPANY_NAME}"
            ),
        )

    def _on_find(self) -> None:
        QMessageBox.information(
            self,
            "Find",
            "Global search across trucks, descriptions, and amounts "
            "will be available in a future update.\n\n"
            "Use Browse or the page filters for now.",
        )

    def _on_register_save_help(self) -> None:
        from tahmeed.ui.cashier.register_save_help import show_register_save_help

        show_register_save_help(self)

    # ── Profile menu ────────────────────────────────────────────────────────────

    def _on_change_password(self) -> None:
        dlg = ChangePasswordDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_change_password(dlg.result_data))

    async def _do_change_password(self, data: dict) -> None:
        from tahmeed.services.auth import change_password
        try:
            ok = await change_password(
                self._user.username, data["current"], data["new"]
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to change password:\n{exc}")
            return
        if ok:
            QMessageBox.information(
                self, "Password Changed", "Your password has been updated."
            )
        else:
            QMessageBox.warning(
                self, "Incorrect Password",
                "Your current password is incorrect. Please try again.",
            )

    # ── Routing ───────────────────────────────────────────────────────────────────

    def _on_nav(self, key: str) -> None:
        self._sidebar.select(key)
        if key == "overview":
            self._stack.setCurrentIndex(0)
            self._overview.refresh()
        elif key == "table":
            self._stack.setCurrentIndex(1)
            self._register.reload_settings()
        elif key == "form":
            self._stack.setCurrentIndex(2)
            self._form.reload_settings()
        elif key == "drafts":
            self._stack.setCurrentIndex(3)
            self._drafts_view.refresh()
        elif key == "saved":
            self._stack.setCurrentIndex(4)
            self._saved_view.refresh()
        elif key == "day_issues":
            self._stack.setCurrentIndex(5)
            target = getattr(self._register, "_current_date", None) or date.today()
            self._day_issues.open_for_date(target)
        elif key == "rejected":
            self._stack.setCurrentIndex(6)
            self._rejected_view.refresh()
        elif key == "browse":
            self._stack.setCurrentIndex(7)
            self._browser.refresh()
        elif key == "trash":
            self._stack.setCurrentIndex(8)
            self._trash_view.refresh()
        elif self._sidebar.item_def(key) is not None:
            self._show_category(key)

    def _show_category(self, key: str) -> None:
        if key not in self._category_indices:
            d = self._sidebar.item_def(key)
            if d is None:
                return
            name, icon, label = d
            widget = CashierCategoryView(
                user=self._user,
                category_key=key,
                category_name=name,
                icon_name=icon,
                title=label,
            )
            self._category_indices[key] = self._stack.addWidget(widget)
        idx = self._category_indices[key]
        self._stack.setCurrentIndex(idx)
        self._stack.widget(idx).refresh()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_go_to_date(self, d, term: str = "") -> None:
        self._on_nav("table")
        self._register.navigate_to_date(d, highlight_term=term, merged=True)

    def _on_go_to_upload(self, upload_id: str, primary_date=None) -> None:
        self._on_nav("table")
        self._register.navigate_to_upload(upload_id, primary_date)

    def _on_drafts_open_register(self, d: date) -> None:
        self._on_nav("table")
        self._register.navigate_to_date(d, merged=False)

    def _on_saved_open_merged(self, d: date) -> None:
        self._on_nav("table")
        self._register.navigate_to_date(d, merged=True)

    def _on_drafts_data_changed(self, day=None) -> None:
        """Keep Drafts + Saved + badges + open register aligned after discard/submit."""
        from tahmeed.ui.async_utils import schedule_coro

        schedule_coro(self._refresh_draft_badge())
        # Drop ghost rows on the Daily Register when that day was discarded/submitted.
        self._register.invalidate_after_server_draft_change(day)
        # Refresh the sibling inbox so discard/submit stays in sync.
        if self._stack.currentWidget() is not self._drafts_view:
            self._drafts_view.refresh()
        if self._stack.currentWidget() is not self._saved_view:
            self._saved_view.refresh()

    async def _refresh_draft_badge(self) -> None:
        try:
            count = await fetch_draft_badge_count(self._user, all_cashiers=False)
        except Exception:
            count = 0
        if hasattr(self._sidebar, "set_drafts_badge"):
            self._sidebar.set_drafts_badge(count)
        if hasattr(self._sidebar, "set_saved_badge"):
            self._sidebar.set_saved_badge(count)

    def _on_day_issues_badge(self, count: int) -> None:
        if hasattr(self._sidebar, "set_day_issues_badge"):
            self._sidebar.set_day_issues_badge(count)

    def _on_trash_badge(self, count: int) -> None:
        if hasattr(self._sidebar, "set_trash_badge"):
            self._sidebar.set_trash_badge(count)

    async def _refresh_trash_badge(self) -> None:
        from tahmeed.services.cashier_service import count_cashier_trash

        try:
            count = await count_cashier_trash()
        except Exception:
            count = 0
        self._on_trash_badge(count)

    def _on_register_trash_changed(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro

        schedule_coro(self._refresh_trash_badge())
        # Refresh trash page if it's currently visible (stack index 7).
        if self._stack.currentIndex() == 7:
            self._trash_view.refresh()

    def _day_issues_page_visible(self) -> bool:
        return self._stack.currentIndex() == 4

    def _schedule_day_issues_badge_refresh(self) -> None:
        self._day_issues_badge_timer.start()

    def _run_debounced_day_issues_badge(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._refresh_day_issues_badge())

    def _on_day_issues_data_changed(self) -> None:
        """Save/draft changes — mark Day Issues dirty; badge only if page hidden."""
        visible = self._day_issues_page_visible()
        self._day_issues.on_register_source_changed(visible=visible)
        if not visible:
            self._schedule_day_issues_badge_refresh()

    async def _refresh_day_issues_badge(self) -> None:
        from tahmeed.services.day_issues_service import count_day_issues

        # When Day Issues is open, badge comes from issues_changed — skip duplicate collect.
        if self._day_issues_page_visible():
            return
        target = getattr(self._register, "_current_date", None) or date.today()
        try:
            replace_txs, extra_txs = self._register.day_issues_row_source(target)
            count = await count_day_issues(
                target,
                transactions=replace_txs,
                extra_transactions=extra_txs,
                cashier_id=self._user._id,
            )
        except Exception:
            count = 0
        self._on_day_issues_badge(count)

    def _on_day_issues_source_changed(self) -> None:
        visible = self._day_issues_page_visible()
        self._day_issues.on_register_source_changed(visible=visible)
        if not visible:
            self._schedule_day_issues_badge_refresh()

    def _on_open_day_issues(self, target=None) -> None:
        self._sidebar.select("day_issues")
        self._stack.setCurrentIndex(5)
        day = target
        if day is None:
            day = getattr(self._register, "_current_date", None) or date.today()
        self._day_issues.open_for_date(day)

    def _on_day_issues_open_register(self, d: date) -> None:
        self._on_nav("table")
        self._register.navigate_to_date(d, merged=True)

    # ── Load categories ───────────────────────────────────────────────────────────

    async def _load_categories(self) -> None:
        try:
            cats = await get_payment_target_categories()
            self._register.update_categories(cats)
            self._form.update_categories(cats)
            self._overview.refresh()
        except Exception:
            pass
        try:
            from tahmeed.services.people_service import get_people_names
            names = await get_people_names()
            self._register.update_people(names)
            self._form.update_people(names)
        except Exception:
            pass
        try:
            from tahmeed.services.payee_service import get_payee_names
            self._register.update_payees(await get_payee_names())
        except Exception:
            pass
