"""QuickBooks-style menu bar for the cashier shell.

Menus mirror the cashier sidebar destinations, plus Reports / Window / Help
chrome (same shape as Accountant Edition, without accountant-only pages).
"""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QMenuBar, QToolButton, QWidget,
)

from tahmeed.config import APP_NAME
from tahmeed.ui.accountant.menu_bar import _style_menu_bar
from tahmeed.ui.cashier.sidebar import _SECTIONS

_BLUE = "#0077C5"


class CashierMenuBar(QMenuBar):
    """Classic desktop menus scoped to cashier sidebar pages + chrome."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        user_display_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._nav_cb = None
        self._toggle_sidebar_cb = None
        self._refresh_cb = None
        self._change_password_cb = None
        self._logout_cb = None
        self._exit_cb = None
        self._about_cb = None
        self._find_cb = None
        self._register_help_cb = None
        self._item_entries: list[tuple[str, str]] = []
        self._items_menu: Optional[QMenu] = None
        _style_menu_bar(self)
        self._build_menus()
        self._build_profile_corner(user_display_name)

    def bind(
        self,
        *,
        navigate,
        toggle_sidebar=None,
        refresh=None,
        change_password=None,
        logout=None,
        exit_app=None,
        about=None,
        find=None,
        register_help=None,
    ) -> None:
        """Wire callbacks after the dashboard shell is ready."""
        self._nav_cb = navigate
        self._toggle_sidebar_cb = toggle_sidebar
        self._refresh_cb = refresh
        self._change_password_cb = change_password
        self._logout_cb = logout
        self._exit_cb = exit_app
        self._about_cb = about
        self._find_cb = find
        self._register_help_cb = register_help

    def set_item_entries(self, entries: list[tuple[str, str]]) -> None:
        """Refresh the Cashier → Items submenu from sidebar dynamic rows."""
        self._item_entries = list(entries)
        self._rebuild_items_menu()

    def _nav(self, key: str) -> None:
        if self._nav_cb:
            self._nav_cb(key)

    def _build_profile_corner(self, display_name: str) -> None:
        initials = "".join(p[0].upper() for p in display_name.split()[:2]) or "CA"

        wrap = QFrame(self)
        wrap.setObjectName("cashierProfileCorner")
        wrap.setStyleSheet(
            "QFrame#cashierProfileCorner { background: transparent; border: none; }"
        )
        hl = QHBoxLayout(wrap)
        hl.setContentsMargins(4, 0, 8, 0)
        hl.setSpacing(4)

        menu = QMenu(wrap)
        change_pw = menu.addAction(
            qta.icon("mdi.lock-reset", color="#6B7280"), "Change Password…"
        )
        change_pw.triggered.connect(
            lambda: self._change_password_cb and self._change_password_cb()
        )
        menu.addSeparator()
        logout = menu.addAction(
            qta.icon("mdi.logout", color="#EF4444"), "Log Out"
        )
        logout.triggered.connect(
            lambda: self._logout_cb and self._logout_cb()
        )

        btn = QToolButton(wrap)
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setMenu(menu)
        btn.setToolTip(display_name or "Account")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setText(initials)
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(
            "QToolButton {"
            f"  background: {_BLUE}; color: #ffffff;"
            "  font-size: 11px; font-weight: 700; font-family: 'Segoe UI';"
            "  border: none; border-radius: 14px;"
            "}"
            "QToolButton::menu-indicator { image: none; width: 0; }"
            f"QToolButton:hover {{ background: #0066A8; }}"
        )
        hl.addWidget(btn)

        chevron = QLabel(wrap)
        chevron.setFixedSize(12, 12)
        chevron.setPixmap(
            qta.icon("mdi.chevron-down", color="#6B7280").pixmap(12, 12)
        )
        chevron.setStyleSheet("background: transparent;")
        hl.addWidget(chevron)

        self.setCornerWidget(wrap, Qt.Corner.TopRightCorner)

    def _build_menus(self) -> None:
        self._file_menu()
        self._edit_menu()
        self._view_menu()
        self._cashier_menu()
        self._reports_menu()
        self._window_menu()
        self._help_menu()

    def _add(self, menu: QMenu, label: str, slot, shortcut: str | None = None) -> QAction:
        act = menu.addAction(label)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        return act

    def _file_menu(self) -> None:
        m = self.addMenu("&File")
        self._add(
            m,
            "Change &Password…",
            lambda: self._change_password_cb and self._change_password_cb(),
        )
        self._add(
            m,
            "&Log Out",
            lambda: self._logout_cb and self._logout_cb(),
        )
        m.addSeparator()
        self._add(
            m,
            "E&xit",
            lambda: self._exit_cb and self._exit_cb(),
            "Alt+F4",
        )

    def _edit_menu(self) -> None:
        m = self.addMenu("&Edit")
        self._add(
            m,
            "&Find…",
            lambda: self._find_cb and self._find_cb(),
            "Ctrl+F",
        )
        m.addSeparator()
        prefs = m.addAction("Preferences…")
        prefs.setEnabled(False)
        prefs.setToolTip("Coming soon")

    def _view_menu(self) -> None:
        m = self.addMenu("&View")
        self._add(
            m,
            "Toggle &Sidebar",
            lambda: self._toggle_sidebar_cb and self._toggle_sidebar_cb(),
            "Ctrl+B",
        )
        self._add(
            m,
            "&Refresh",
            lambda: self._refresh_cb and self._refresh_cb(),
            "F5",
        )

    def _cashier_menu(self) -> None:
        m = self.addMenu("&Cashier")
        for section_label, items in _SECTIONS:
            if section_label == "ITEMS":
                if m.actions():
                    m.addSeparator()
                self._items_menu = m.addMenu("&Items")
                self._rebuild_items_menu()
                continue
            if section_label is not None and m.actions():
                m.addSeparator()
            for key, label, _icon, _opts in items:
                amp = "&" if not label.startswith("&") else ""
                # Ignore QAction.triggered(bool) so it cannot overwrite k.
                self._add(
                    m,
                    f"{amp}{label}",
                    lambda _checked=False, k=key: self._nav(k),
                )

    def _rebuild_items_menu(self) -> None:
        if self._items_menu is None:
            return
        self._items_menu.clear()
        if not self._item_entries:
            empty = self._items_menu.addAction("(No items)")
            empty.setEnabled(False)
            return
        for key, label in self._item_entries:
            self._add(
                self._items_menu,
                label,
                lambda _checked=False, k=key: self._nav(k),
            )

    def _reports_menu(self) -> None:
        m = self.addMenu("&Reports")
        self._add(m, "&Overview Dashboard", lambda: self._nav("overview"))
        self._add(m, "&Browse", lambda: self._nav("browse"))

    def _window_menu(self) -> None:
        m = self.addMenu("&Window")
        self._add(m, "&Home (Overview)", lambda: self._nav("overview"))

    def _help_menu(self) -> None:
        m = self.addMenu("&Help")
        self._add(
            m,
            "&Register Save && Recovery…",
            lambda: self._register_help_cb and self._register_help_cb(),
        )
        m.addSeparator()
        self._add(
            m,
            f"&About {APP_NAME}…",
            lambda: self._about_cb and self._about_cb(),
        )
