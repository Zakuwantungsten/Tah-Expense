"""
CashierDashboard — Dark professional toolbar (FuelFlow branded).

Layout:
  ┌─ Toolbar (52 px, dark teal #0e2e2b) ─────────────────────────────────────┐
  │  ● FuelFlow                │ [Table][Form] │ [New][Save][Del][Import]     │
  │    Orders & Deliveries     │               │ [Browse]          Name/Role  │
  ├─ Stacked entry area ──────────────────────────────────────────────────────┤
  │  [0] DailyRegister  ← date-nav in footer                                  │
  │  [1] EntryForm                                                             │
  └───────────────────────────────────────────────────────────────────────────┘
"""

import asyncio
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
    QLabel, QStackedWidget, QFrame, QApplication,
)
from PySide6.QtCore import Qt, QSize, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

from tahmeed.models.user import User
from tahmeed.services.category_service import get_all_categories
from tahmeed.ui.cashier.excel_grid import DailyRegister
from tahmeed.ui.cashier.entry_form import EntryForm
from tahmeed.ui.cashier.overview import CashierOverview
from tahmeed.ui.cashier.transactions_table import TransactionBrowser


# ---------------------------------------------------------------------------
# SVG icon set  — Lucide-style 24×24 viewBox, coloured for dark background
# ---------------------------------------------------------------------------
_SVGS: dict = {
    "table": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        b'<rect x="3" y="3" width="7" height="7" rx="1.5" fill="#e2e8f0"/>'
        b'<rect x="14" y="3" width="7" height="7" rx="1.5" fill="#e2e8f0"/>'
        b'<rect x="3" y="14" width="7" height="7" rx="1.5" fill="#e2e8f0"/>'
        b'<rect x="14" y="14" width="7" height="7" rx="1.5" fill="#e2e8f0"/>'
        b'</svg>'
    ),
    "form": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#e2e8f0" stroke-width="1.8" stroke-linecap="round">'
        b'<rect x="4" y="2" width="16" height="20" rx="2"/>'
        b'<line x1="7" y1="8" x2="17" y2="8"/>'
        b'<line x1="7" y1="12" x2="17" y2="12"/>'
        b'<line x1="7" y1="16" x2="13" y2="16"/>'
        b'</svg>'
    ),
    "new": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#4ade80" stroke-width="2" stroke-linecap="round">'
        b'<circle cx="12" cy="12" r="9"/>'
        b'<line x1="12" y1="8" x2="12" y2="16"/>'
        b'<line x1="8" y1="12" x2="16" y2="12"/>'
        b'</svg>'
    ),
    "save": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#60a5fa" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/>'
        b'<polyline points="17,21 17,13 7,13 7,21"/>'
        b'<polyline points="7,3 7,8 15,8"/>'
        b'</svg>'
    ),
    "delete": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#f87171" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<polyline points="3,6 5,6 21,6"/>'
        b'<path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>'
        b'<path d="M10 11v6M14 11v6"/>'
        b'<path d="M9 6V4h6v2"/>'
        b'</svg>'
    ),
    "import": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#c084fc" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M12 3v13"/>'
        b'<polyline points="7,11 12,16 17,11"/>'
        b'<path d="M5 20h14"/>'
        b'</svg>'
    ),
    "browse": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#e2e8f0" stroke-width="1.8" stroke-linecap="round">'
        b'<circle cx="11" cy="11" r="7"/>'
        b'<line x1="16.5" y1="16.5" x2="21" y2="21" stroke-width="2.2"/>'
        b'</svg>'
    ),
    "fuel": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#4ade80" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M5 22V8a2 2 0 012-2h6a2 2 0 012 2v14"/>'
        b'<path d="M5 13h10"/>'
        b'<path d="M17 8l2-2 1 1v5a1 1 0 002 0V9l-3-4"/>'
        b'</svg>'
    ),
    "overview": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#e2e8f0" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<rect x="2" y="3" width="8" height="8" rx="1.5"/>'
        b'<rect x="14" y="3" width="8" height="8" rx="1.5"/>'
        b'<rect x="2" y="13" width="8" height="8" rx="1.5"/>'
        b'<rect x="14" y="13" width="8" height="8" rx="1.5"/>'
        b'<line x1="16" y1="16" x2="20" y2="16"/>'
        b'<line x1="18" y1="14" x2="18" y2="18"/>'
        b'</svg>'
    ),
    "chevron": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#64748b" stroke-width="2.5" stroke-linecap="round">'
        b'<polyline points="6,9 12,15 18,9"/>'
        b'</svg>'
    ),
    "user": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#4ade80" stroke-width="1.8" stroke-linecap="round">'
        b'<circle cx="12" cy="8" r="4"/>'
        b'<path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>'
        b'</svg>'
    ),
}


def _svg_icon(key: str, size: int = 16) -> QIcon:
    renderer = QSvgRenderer(QByteArray(_SVGS[key]))
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    renderer.render(p)
    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------------
# Button style — dark toolbar
# ---------------------------------------------------------------------------

_DARK_BTN = """
QToolButton {
    background: transparent;
    border: none;
    border-radius: 5px;
    padding: 5px 11px;
    color: #cbd5e1;
    font-size: 12px;
    min-width: 36px;
}
QToolButton:hover   { background: rgba(255,255,255,0.08); color: #f1f5f9; }
QToolButton:pressed { background: rgba(255,255,255,0.12); }
QToolButton:checked {
    background: rgba(255,255,255,0.14);
    color: #ffffff;
    font-weight: 600;
}
QToolButton:disabled { color: #475569; }
"""


# ---------------------------------------------------------------------------
# CashierDashboard
# ---------------------------------------------------------------------------

class CashierDashboard(QWidget):
    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user    = user
        self._browser: Optional[TransactionBrowser] = None
        self._build_ui()
        asyncio.ensure_future(self._load_categories())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.setStyleSheet("CashierDashboard { background: #ffffff; }")

        # ── Toolbar ────────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setFixedHeight(52)
        toolbar.setStyleSheet("background: #0e2e2b;")
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(12, 0, 12, 0)
        tl.setSpacing(0)

        # — Logo —
        logo_icon = QLabel()
        logo_icon.setFixedSize(32, 32)
        logo_icon.setAlignment(Qt.AlignCenter)
        logo_icon.setStyleSheet("background: #1b4f4a; border-radius: 16px;")
        logo_icon.setPixmap(_svg_icon("fuel", 18).pixmap(QSize(18, 18)))

        logo_text = QWidget()
        logo_text.setStyleSheet("background: transparent;")
        ltl = QVBoxLayout(logo_text)
        ltl.setContentsMargins(0, 0, 0, 0)
        ltl.setSpacing(0)
        _app_lbl = QLabel("FuelFlow")
        _app_lbl.setStyleSheet(
            "color: #ffffff; font-size: 13px; font-weight: 700; background: transparent;"
        )
        _sub_lbl = QLabel("Orders & Deliveries")
        _sub_lbl.setStyleSheet(
            "color: #6ee7b7; font-size: 9px; background: transparent;"
        )
        ltl.addWidget(_app_lbl)
        ltl.addWidget(_sub_lbl)

        tl.addWidget(logo_icon)
        tl.addSpacing(8)
        tl.addWidget(logo_text)
        tl.addSpacing(14)
        tl.addWidget(_vsep())
        tl.addSpacing(4)

        # — Button factory —
        def _dbtn(label: str, key: str, tip: str, checkable: bool = False) -> QToolButton:
            b = QToolButton()
            b.setText(f"  {label}")
            b.setIcon(_svg_icon(key, 16))
            b.setIconSize(QSize(16, 16))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setStyleSheet(_DARK_BTN)
            return b

        # — Mode toggle —
        self._overview_btn = _dbtn("Overview", "overview", "Overview dashboard", checkable=True)
        self._overview_btn.clicked.connect(lambda: self._set_mode("overview"))

        self._grid_btn = _dbtn("Table", "table", "Switch to table entry mode", checkable=True)
        self._grid_btn.clicked.connect(lambda: self._set_mode("grid"))

        self._form_btn = _dbtn("Form", "form", "Switch to form entry mode", checkable=True)
        self._form_btn.clicked.connect(lambda: self._set_mode("form"))

        tl.addWidget(self._overview_btn)
        tl.addWidget(self._grid_btn)
        tl.addWidget(self._form_btn)
        tl.addSpacing(4)
        tl.addWidget(_vsep())
        tl.addSpacing(4)

        # — Row actions —
        self._new_btn = _dbtn("New", "new", "Scroll to first empty row")
        self._new_btn.clicked.connect(lambda: self._register.go_to_new_row())

        self._save_btn = _dbtn("Save All", "save", "Save all filled rows to the database")
        self._save_btn.clicked.connect(self._on_save_all)

        self._delete_btn = _dbtn("Delete", "delete", "Delete selected row(s)")
        self._delete_btn.clicked.connect(lambda: self._register.delete_rows())

        self._import_btn = _dbtn("Import", "import", "Import rows from an Excel or CSV file")
        self._import_btn.clicked.connect(self._on_import)

        tl.addWidget(self._new_btn)
        tl.addWidget(self._save_btn)
        tl.addWidget(self._delete_btn)
        tl.addWidget(self._import_btn)
        tl.addSpacing(4)
        tl.addWidget(_vsep())
        tl.addSpacing(4)

        # — Browse —
        self._browse_btn = _dbtn("Browse", "browse", "Search and browse transactions")
        self._browse_btn.clicked.connect(self._on_browse)
        tl.addWidget(self._browse_btn)

        tl.addStretch()

        # — User profile —
        tl.addWidget(_vsep())
        tl.addSpacing(10)

        initials = "".join(p[0].upper() for p in self._user.full_name.split()[:2]) or "U"

        avatar = QLabel(initials)
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            "background: #1b4f4a; border-radius: 16px;"
            " color: #4ade80; font-size: 12px; font-weight: 700;"
        )

        user_info = QWidget()
        user_info.setStyleSheet("background: transparent;")
        uitl = QVBoxLayout(user_info)
        uitl.setContentsMargins(0, 0, 0, 0)
        uitl.setSpacing(0)
        _name_lbl = QLabel(self._user.full_name)
        _name_lbl.setStyleSheet(
            "color: #f1f5f9; font-size: 12px; font-weight: 600; background: transparent;"
        )
        _role_lbl = QLabel(self._user.role.title())
        _role_lbl.setStyleSheet(
            "color: #64748b; font-size: 10px; background: transparent;"
        )
        uitl.addWidget(_name_lbl)
        uitl.addWidget(_role_lbl)

        chev = QLabel()
        chev.setFixedSize(14, 14)
        chev.setAlignment(Qt.AlignCenter)
        chev.setPixmap(_svg_icon("chevron", 12).pixmap(QSize(12, 12)))

        tl.addWidget(avatar)
        tl.addSpacing(7)
        tl.addWidget(user_info)
        tl.addSpacing(4)
        tl.addWidget(chev)

        root.addWidget(toolbar)

        # ── Stacked entry area ─────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: #ffffff;")

        # Index 0 — Overview
        self._overview = CashierOverview(user=self._user)
        self._stack.addWidget(self._overview)

        # Index 1 — Daily Register (table mode)
        self._register = DailyRegister(user=self._user, categories=[])
        self._stack.addWidget(self._register)

        # Index 2 — Entry Form
        self._form = EntryForm(user=self._user)
        self._stack.addWidget(self._form)

        self._stack.setCurrentIndex(0)
        self._overview_btn.setChecked(True)
        root.addWidget(self._stack)

        # ── Signal wiring ──────────────────────────────────────────────
        self._form.transaction_saved.connect(lambda _: self._register.refresh())
        self._overview.go_to_register.connect(lambda: self._set_mode("grid"))
        self._overview.go_to_form.connect(lambda: self._set_mode("form"))
        self._overview.go_to_browse.connect(self._on_browse)
        self._overview.export_data.connect(self._on_overview_export)
        self._overview.import_data.connect(self._on_overview_import)

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        idx = {"overview": 0, "grid": 1, "form": 2}.get(mode, 0)
        self._stack.setCurrentIndex(idx)
        self._overview_btn.setChecked(mode == "overview")
        self._grid_btn.setChecked(mode == "grid")
        self._form_btn.setChecked(mode == "form")
        is_grid = mode == "grid"
        self._new_btn.setEnabled(is_grid)
        self._save_btn.setEnabled(is_grid)
        self._delete_btn.setEnabled(is_grid)
        self._import_btn.setEnabled(is_grid)
        if mode == "overview":
            self._overview.refresh()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_import(self) -> None:
        self._register.import_from_file()

    def _on_overview_import(self) -> None:
        self._set_mode("grid")
        self._register.import_from_file()

    def _on_overview_export(self) -> None:
        self._on_browse()

    def _on_save_all(self) -> None:
        self._register.save_rows()

    def _on_browse(self) -> None:
        if self._browser is None:
            self._browser = TransactionBrowser(parent=self)
            self._browser.go_to_date.connect(self._on_go_to_date)
            self._browser.finished.connect(lambda: setattr(self, "_browser", None))
        self._browser.show_and_search()

    def _on_go_to_date(self, d) -> None:
        self._set_mode("grid")
        self._register.navigate_to_date(d)

    # ------------------------------------------------------------------
    # Load categories
    # ------------------------------------------------------------------

    async def _load_categories(self) -> None:
        try:
            cats = await get_all_categories()
            self._register.update_categories(cats)
            self._form.update_categories(cats)
            self._overview.refresh()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.VLine)
    sep.setFixedHeight(22)
    sep.setStyleSheet("color: rgba(255,255,255,30); margin: 0 2px;")
    return sep
