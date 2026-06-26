"""
CashierDashboard — Sidebar-based navigation (matches Accountant visual style).

Layout:
  ┌─ Sidebar (220px, navy #1B2B4B, collapsible) ─┬─ Content Area ──────────────┐
  │  [T] Tahmeed / Expense Manager               │  [Stacked pages]            │
  │  ─────────────────────────────               │                             │
  │  Overview  ·  Browse                         │                             │
  │  ─────────────────────────────               │                             │
  │  ENTRY                                       │                             │
  │    Table  ·  Form                            │                             │
  │  CATEGORIES   (13 items)                     │                             │
  │  SEPARATE EXPENSES  (11 items)               │                             │
  │  FUEL CONSUMPTION   (5 items)                │                             │
  │  ─────────────────────────────               │                             │
  │  [user avatar + name/role]                   │                             │
  │  [Collapse]                                  │                             │
  └──────────────────────────────────────────────┴─────────────────────────────┘
  ── Status bar (24px) ─────────────────────────────────────────────────────────
"""

import asyncio
from datetime import date
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QFrame, QLabel, QLineEdit, QPushButton,
)
from PySide6.QtCore import Qt, Signal, QSize

import qtawesome as qta

from tahmeed.models.user import User
from tahmeed.services.category_service import get_all_categories
from tahmeed.ui.accountant.header_bar import HeaderBar
from tahmeed.ui.cashier.sidebar import CashierSidebarWidget
from tahmeed.ui.cashier.excel_grid import DailyRegister
from tahmeed.ui.cashier.entry_form import EntryForm
from tahmeed.ui.cashier.overview import CashierOverview
from tahmeed.ui.cashier.transactions_table import TransactionBrowser
from tahmeed.ui.cashier.cashier_category_view import CashierCategoryView

_APP_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_WHITE  = "#FFFFFF"
_BLUE   = "#0077C5"
_T1     = "#111827"
_T2     = "#6B7280"

# ── Button styles ────────────────────────────────────────────────────────────────
# Mirrors the filled / outlined / tonal hierarchy used by Material 3, Stripe and
# Microsoft Fluent: one high-emphasis filled action (Save), neutral outlined
# secondaries (Edit / Export), and a warm tonal "active" state for the toggle.
_BTN_BASE = (
    "border-radius:6px;font-size:13px;font-weight:600;"
    "font-family:'Segoe UI',sans-serif;padding:0 16px;"
)
_BTN_STYLES = {
    "primary": (
        f"QPushButton{{background:{_BLUE};color:#FFFFFF;border:1px solid {_BLUE};{_BTN_BASE}}}"
        "QPushButton:hover{background:#0369A1;border-color:#0369A1;}"
        "QPushButton:pressed{background:#075985;border-color:#075985;}"
        "QPushButton:disabled{background:#93C5FD;border-color:#93C5FD;color:#EFF6FF;}"
    ),
    "secondary": (
        f"QPushButton{{background:#FFFFFF;color:#374151;border:1px solid #D1D5DB;{_BTN_BASE}}}"
        "QPushButton:hover{background:#F9FAFB;border-color:#9CA3AF;}"
        "QPushButton:pressed{background:#F3F4F6;}"
        "QPushButton:disabled{color:#9CA3AF;border-color:#E5E7EB;}"
    ),
    "active": (
        "QPushButton{background:#D97706;color:#FFFFFF;border:1px solid #D97706;" + _BTN_BASE + "}"
        "QPushButton:hover{background:#B45309;border-color:#B45309;}"
        "QPushButton:pressed{background:#92400E;border-color:#92400E;}"
    ),
}


# ── QuickBooks-style document header ─────────────────────────────────────────────

class _QBDocHeader(QFrame):
    """QuickBooks-style document summary header with 4 KPI stat tiles."""

    search_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("qbDocHeader")
        self.setFixedHeight(68)
        self.setStyleSheet(
            "QFrame#qbDocHeader {"
            "  background: #ffffff;"
            "  border-bottom: 2px solid #0077C5;"
            "}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 20, 0)
        hl.setSpacing(0)

        # Left accent strip
        accent = QFrame()
        accent.setFixedWidth(5)
        accent.setStyleSheet("background: #0077C5; border: none;")
        hl.addWidget(accent)
        hl.addSpacing(14)

        # Title block
        title_vl = QVBoxLayout()
        title_vl.setSpacing(2)
        title_vl.setContentsMargins(0, 10, 0, 10)
        title_lbl = QLabel("Daily Register")
        title_lbl.setStyleSheet(
            "font-size: 18px; font-weight: 700; color: #1B2B4B;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        sub_lbl = QLabel("Cashier  ·  Expense Entry")
        sub_lbl.setStyleSheet(
            "font-size: 10px; color: #9CA3AF;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        title_vl.addWidget(title_lbl)
        title_vl.addWidget(sub_lbl)
        hl.addLayout(title_vl)
        hl.addStretch()

        # Search bar
        search = QLineEdit()
        search.setPlaceholderText("Search entries…")
        search.setFixedWidth(210)
        search.setFixedHeight(30)
        search.setStyleSheet(
            "QLineEdit {"
            "  border: 1px solid #D1D5DB; border-radius: 4px;"
            "  padding: 0 8px; font-size: 12px;"
            "  color: #111827; background: #F9FAFB;"
            "}"
            "QLineEdit:focus {"
            "  border-color: #0077C5; background: #ffffff;"
            "}"
        )
        search.textChanged.connect(self.search_changed)
        hl.addWidget(search)
        hl.addSpacing(12)

        # Separator before tile strip
        sep0 = QFrame()
        sep0.setFrameShape(QFrame.VLine)
        sep0.setStyleSheet("color: #E5E7EB;")
        hl.addWidget(sep0)

        # 4 KPI tiles: label, initial value, label color
        tiles_cfg = [
            ("ENTRIES",         "—",                              "#6B7280"),
            ("TODAY",           date.today().strftime("%d %b %Y"), "#6B7280"),
            ("REFUND TO FLOAT", "—",                              "#EA580C"),
            ("TOTAL",           "—",                              "#6B7280"),
        ]
        val_labels = []
        for i, (label, init_val, lbl_color) in enumerate(tiles_cfg):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet("color: #E5E7EB;")
                hl.addWidget(sep)

            tile = QWidget()
            tile.setFixedWidth(140)
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
            val.setStyleSheet(
                "font-size: 13px; color: #111827; font-weight: 700;"
                " font-family: 'Segoe UI', sans-serif; background: transparent;"
            )
            tile_vl.addWidget(lbl)
            tile_vl.addWidget(val)
            val_labels.append(val)
            hl.addWidget(tile)

        (
            self._val_entries,
            self._val_today,
            self._val_refund,
            self._val_total,
        ) = val_labels

    def update_stats(self, n_entries: int, total_tzs: float, refund_total: float) -> None:
        self._val_entries.setText(f"{n_entries} entr{'y' if n_entries == 1 else 'ies'}")
        self._val_total.setText(f"TZS {total_tzs:,.0f}" if total_tzs else "—")
        self._val_refund.setText(f"TZS {refund_total:,.0f}" if refund_total else "—")


# ── Action bar (Edit / Save / Export) ────────────────────────────────────────────

class _ActionBar(QFrame):
    """Thin professional action strip between the document header and the table."""

    edit_clicked   = Signal()
    save_clicked   = Signal()
    export_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("registerActionBar")
        self.setFixedHeight(52)
        self.setStyleSheet(
            "QFrame#registerActionBar {"
            f"  background: {_WHITE}; border-bottom: 1px solid {_BORDER};"
            "}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 8, 16, 8)
        hl.setSpacing(10)

        # Edit-mode status pill — hidden unless editing
        self._status = QLabel("")
        self._status.setStyleSheet(
            "QLabel{background:#FEF3C7;color:#92400E;border:1px solid #FDE68A;"
            "border-radius:5px;padding:4px 10px;font-size:12px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;}"
        )
        self._status.hide()
        hl.addWidget(self._status)

        hl.addStretch()

        self._export_btn = self._make_btn("Export", "mdi.tray-arrow-down", "secondary")
        self._export_btn.clicked.connect(self.export_clicked)
        hl.addWidget(self._export_btn)

        self._edit_btn = self._make_btn("Edit", "mdi.pencil-outline", "secondary")
        self._edit_btn.clicked.connect(self.edit_clicked)
        hl.addWidget(self._edit_btn)

        self._save_btn = self._make_btn("Save", "mdi.content-save-outline", "primary")
        self._save_btn.clicked.connect(self.save_clicked)
        hl.addWidget(self._save_btn)

    def _make_btn(self, text: str, icon: str, kind: str) -> QPushButton:
        b = QPushButton(f"  {text}")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(36)
        try:
            color = "#FFFFFF" if kind == "primary" else "#374151"
            b.setIcon(qta.icon(icon, color=color))
            b.setIconSize(QSize(16, 16))
        except Exception:
            pass
        b.setStyleSheet(_BTN_STYLES[kind])
        return b

    def set_edit_state(self, active: bool, dirty_count: int = 0) -> None:
        """Reflect the register's edit mode on the toggle + status pill."""
        if active:
            self._edit_btn.setText("  Cancel")
            try:
                self._edit_btn.setIcon(qta.icon("mdi.close", color="#FFFFFF"))
            except Exception:
                pass
            self._edit_btn.setStyleSheet(_BTN_STYLES["active"])
            plural = "" if dirty_count == 1 else "s"
            self._status.setText(f"Edit mode  ·  {dirty_count} unsaved change{plural}")
            self._status.show()
        else:
            self._edit_btn.setText("  Edit")
            try:
                self._edit_btn.setIcon(qta.icon("mdi.pencil-outline", color="#374151"))
            except Exception:
                pass
            self._edit_btn.setStyleSheet(_BTN_STYLES["secondary"])
            self._status.hide()


class _TablePage(QWidget):
    """_QBDocHeader + action bar + DailyRegister stacked vertically."""

    def __init__(self, register: DailyRegister, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._doc_header = _QBDocHeader()
        register.stats_updated.connect(self._doc_header.update_stats)
        self._doc_header.search_changed.connect(register.set_search)

        self._action_bar = _ActionBar()
        self._action_bar.edit_clicked.connect(register.toggle_edit_mode)
        self._action_bar.save_clicked.connect(register.save_rows)
        self._action_bar.export_clicked.connect(register.export_xlsx)
        register.edit_state_changed.connect(self._action_bar.set_edit_state)

        vl.addWidget(self._doc_header)
        vl.addWidget(self._action_bar)
        vl.addWidget(register, 1)


# ── Status bar ───────────────────────────────────────────────────────────────────

_NAVY_STATUS   = "#1B2B4B"
_NAVY_STATUS_T = "rgba(148,163,184,0.15)"

class _StatusBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cashierStatusBar")
        self.setFixedHeight(24)
        self.setStyleSheet(
            f"QFrame#cashierStatusBar {{"
            f"  background: {_NAVY_STATUS}; border-top: 1px solid {_NAVY_STATUS_T};"
            f"}}"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(0)

        dot = QLabel("●")
        dot.setStyleSheet("color: #16A34A; font-size: 9px; background: transparent;")
        hl.addWidget(dot)
        hl.addSpacing(5)

        status = QLabel(
            "Connected · MongoDB Atlas"
            "     |     FY 2025"
            "     |     Cashier Mode"
            "     |     v1.0.0"
        )
        status.setStyleSheet(
            "color: #94A3B8; font-size: 11px;"
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        hl.addWidget(status)
        hl.addStretch()


# ── CashierDashboard ─────────────────────────────────────────────────────────────

class CashierDashboard(QWidget):
    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user = user
        self._browser: Optional[TransactionBrowser] = None
        self._category_indices: dict[str, int] = {}
        self._build_ui()
        asyncio.ensure_future(self._load_categories())

    def _build_ui(self) -> None:
        self.setObjectName("cashierDashboard")
        self.setStyleSheet(
            f"QWidget#cashierDashboard {{ background: {_APP_BG}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Build sidebar first so its toggle fn can be passed to the header bar.
        self._sidebar = CashierSidebarWidget(user=self._user)
        self._sidebar.nav_selected.connect(self._on_nav)

        # ── Header bar ────────────────────────────────────────────────────────
        self._header = HeaderBar(
            user=self._user,
            sidebar_toggle_fn=self._sidebar.toggle_collapsed,
            dark=True,
            show_search=False,
        )
        root.addWidget(self._header)

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

        self._stack.setCurrentIndex(0)
        body_hl.addWidget(self._stack, 1)

        root.addWidget(body, 1)
        root.addWidget(_StatusBar())

        # ── Signal wiring ──────────────────────────────────────────────────────
        self._form.transaction_saved.connect(lambda _: self._register.refresh())
        self._overview.go_to_register.connect(lambda: self._on_nav("table"))
        self._overview.go_to_form.connect(lambda: self._on_nav("form"))
        self._overview.go_to_browse.connect(self._on_browse)
        self._overview.export_data.connect(self._on_browse)

    # ── Routing ───────────────────────────────────────────────────────────────────

    def _on_nav(self, key: str) -> None:
        if key == "browse":
            self._on_browse()
            return
        self._sidebar.select(key)
        if key == "overview":
            self._stack.setCurrentIndex(0)
            self._overview.refresh()
        elif key == "table":
            self._stack.setCurrentIndex(1)
            self._register.reload_settings()
        elif key == "form":
            self._stack.setCurrentIndex(2)
        elif self._sidebar.item_def(key) is not None:
            self._show_category(key)

    def _show_category(self, key: str) -> None:
        if key not in self._category_indices:
            d = self._sidebar.item_def(key)
            if d is None:
                return
            label, icon = d
            widget = CashierCategoryView(
                user=self._user,
                category_key=key,
                category_name=label,
                icon_name=icon,
            )
            self._category_indices[key] = self._stack.addWidget(widget)
        idx = self._category_indices[key]
        self._stack.setCurrentIndex(idx)
        self._stack.widget(idx).refresh()

    # ── Actions ───────────────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        if self._browser is None:
            self._browser = TransactionBrowser(parent=self)
            self._browser.go_to_date.connect(self._on_go_to_date)
            self._browser.finished.connect(lambda: setattr(self, "_browser", None))
        self._browser.show_and_search()

    def _on_go_to_date(self, d) -> None:
        self._on_nav("table")
        self._register.navigate_to_date(d)

    # ── Load categories ───────────────────────────────────────────────────────────

    async def _load_categories(self) -> None:
        try:
            cats = await get_all_categories()
            self._register.update_categories(cats)
            self._form.update_categories(cats)
            self._overview.refresh()
        except Exception:
            pass
