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
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QFrame, QLabel, QPushButton,
)
from PySide6.QtCore import Qt, QSize

import qtawesome as qta

from tahmeed.models.user import User
from tahmeed.services.category_service import get_all_categories
from tahmeed.ui.accountant.header_bar import HeaderBar
from tahmeed.ui.cashier.sidebar import CashierSidebarWidget, CATEGORY_LABELS, CATEGORY_ICONS
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


# ── Table action bar ──────────────────────────────────────────────────────────────

class _TableHeader(QFrame):
    """Compact action bar sitting above DailyRegister."""

    def __init__(self, register: DailyRegister, parent=None):
        super().__init__(parent)
        self._register = register
        self.setObjectName("tableHeader")
        self.setFixedHeight(48)
        self.setStyleSheet(
            f"QFrame#tableHeader {{"
            f"  background: {_WHITE}; border-bottom: 1px solid {_BORDER};"
            f"}}"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(6)

        def _btn(label: str, icon: str, color: str, tip: str) -> QPushButton:
            b = QPushButton(f"  {label}")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(32)
            b.setStyleSheet(
                f"QPushButton {{ background: {_WHITE}; color: {color};"
                f" border: 1px solid {_BORDER}; border-radius: 5px;"
                f" font-size: 12px; font-weight: 600;"
                f" font-family: 'Segoe UI', sans-serif; padding: 0 10px; }}"
                f"QPushButton:hover {{ background: {_APP_BG}; }}"
            )
            try:
                b.setIcon(qta.icon(icon, color=color))
                b.setIconSize(QSize(14, 14))
            except Exception:
                pass
            return b

        new_btn = _btn("New",      "mdi.plus-circle-outline",      "#E85D04", "Go to first empty row")
        save_btn = _btn("Save All", "mdi.content-save-all-outline", _BLUE,    "Save all filled rows")
        del_btn  = _btn("Delete",  "mdi.delete-outline",            "#DC2626", "Delete selected row(s)")
        imp_btn  = _btn("Import",  "mdi.file-import-outline",       "#7C3AED", "Import from Excel/CSV")

        new_btn.clicked.connect(lambda: self._register.go_to_new_row())
        save_btn.clicked.connect(self._register.save_rows)
        del_btn.clicked.connect(self._register.delete_rows)
        imp_btn.clicked.connect(self._register.import_from_file)

        hl.addWidget(new_btn)
        hl.addWidget(save_btn)
        hl.addWidget(del_btn)
        hl.addWidget(imp_btn)
        hl.addStretch()


class _TablePage(QWidget):
    """_TableHeader + DailyRegister stacked vertically."""

    def __init__(self, register: DailyRegister, parent=None):
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        vl.addWidget(_TableHeader(register))
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
        self._overview.import_data.connect(self._on_overview_import)

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
        elif key == "form":
            self._stack.setCurrentIndex(2)
        elif key in CATEGORY_LABELS:
            self._show_category(key)

    def _show_category(self, key: str) -> None:
        if key not in self._category_indices:
            label = CATEGORY_LABELS[key]
            icon = CATEGORY_ICONS.get(key, "mdi.tag-outline")
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

    def _on_overview_import(self) -> None:
        self._on_nav("table")
        self._register.import_from_file()

    # ── Load categories ───────────────────────────────────────────────────────────

    async def _load_categories(self) -> None:
        try:
            cats = await get_all_categories()
            self._register.update_categories(cats)
            self._form.update_categories(cats)
            self._overview.refresh()
        except Exception:
            pass
