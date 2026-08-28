"""QuickBooks-style menu bar for the accountant shell."""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QMenuBar, QToolButton, QWidget,
)

_BG = "#F0F0F0"
_BORDER = "#D0D0D0"
_TEXT = "#1B2B4B"
_HOVER = "#E5E7EB"
_BLUE = "#0077C5"


def _style_menu_bar(bar: QMenuBar) -> None:
    bar.setStyleSheet(
        f"""
        QMenuBar {{
            background: {_BG};
            border-bottom: 1px solid {_BORDER};
            padding: 1px 4px;
            font-size: 12px;
            font-family: 'Segoe UI';
            color: {_TEXT};
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 4px 8px;
            border-radius: 2px;
        }}
        QMenuBar::item:selected {{
            background: {_HOVER};
        }}
        QMenuBar::item:pressed {{
            background: #DCE3EC;
        }}
        QMenu {{
            background: #FFFFFF;
            border: 1px solid {_BORDER};
            padding: 4px;
            font-size: 12px;
            font-family: 'Segoe UI';
            color: {_TEXT};
        }}
        QMenu::item {{
            padding: 5px 28px 5px 12px;
            border-radius: 2px;
        }}
        QMenu::item:selected {{
            background: #EFF6FF;
            color: #1D4ED8;
        }}
        QMenu::separator {{
            height: 1px;
            background: {_BORDER};
            margin: 4px 8px;
        }}
        """
    )


class AccountantMenuBar(QMenuBar):
    """Classic desktop menus that jump into existing accountant pages."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        user_display_name: str = "",
    ) -> None:
        super().__init__(parent)
        self._nav_cb = None
        self._sub_cb = None
        self._toggle_sidebar_cb = None
        self._refresh_cb = None
        self._change_password_cb = None
        self._logout_cb = None
        self._exit_cb = None
        self._about_cb = None
        self._find_cb = None
        _style_menu_bar(self)
        self._build_menus()
        self._build_profile_corner(user_display_name)

    def bind(
        self,
        *,
        navigate,
        navigate_sub=None,
        toggle_sidebar=None,
        refresh=None,
        change_password=None,
        logout=None,
        exit_app=None,
        about=None,
        find=None,
    ) -> None:
        """Wire callbacks after the dashboard shell is ready."""
        self._nav_cb = navigate
        self._sub_cb = navigate_sub
        self._toggle_sidebar_cb = toggle_sidebar
        self._refresh_cb = refresh
        self._change_password_cb = change_password
        self._logout_cb = logout
        self._exit_cb = exit_app
        self._about_cb = about
        self._find_cb = find

    def _nav(self, key: str) -> None:
        if self._nav_cb:
            self._nav_cb(key)

    def _sub(self, parent_key: str, match: str) -> None:
        if self._sub_cb:
            self._sub_cb(parent_key, match)

    def _build_profile_corner(self, display_name: str) -> None:
        """Compact avatar on the right — Log Out without the old header bar."""
        initials = "".join(p[0].upper() for p in display_name.split()[:2]) or "AC"

        wrap = QFrame(self)
        wrap.setObjectName("accountantProfileCorner")
        wrap.setStyleSheet(
            "QFrame#accountantProfileCorner { background: transparent; border: none; }"
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
        self._lists_menu()
        self._manage_menu()
        self._suppliers_menu()
        self._import_menu()
        self._accountant_menu()
        self._company_menu()
        self._expenses_menu()
        self._fuel_menu()
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
        self._add(m, "&Backups…", lambda: self._nav("backup"))
        m.addSeparator()
        self._add(m, "Change &Password…", lambda: self._change_password_cb and self._change_password_cb())
        self._add(m, "&Log Out", lambda: self._logout_cb and self._logout_cb())
        m.addSeparator()
        self._add(m, "E&xit", lambda: self._exit_cb and self._exit_cb(), "Alt+F4")

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

    def _lists_menu(self) -> None:
        m = self.addMenu("&Lists")
        self._add(m, "&Items", lambda: self._nav("manage_categories"))
        self._add(m, "&Description Maps", lambda: self._nav("manage_description_maps"))
        self._add(m, "&People", lambda: self._nav("manage_people"))
        self._add(m, "&Trucks", lambda: self._nav("manage_trucks"))
        self._add(m, "T&railers", lambda: self._nav("manage_trailers"))
        self._add(m, "&Motorcycles & Cars", lambda: self._nav("manage_motor_vehicles"))
        m.addSeparator()
        self._add(m, "&Users", lambda: self._nav("manage_users"))

    def _manage_menu(self) -> None:
        m = self.addMenu("&Manage")
        self._add(m, "Manage &Items…", lambda: self._nav("manage_categories"))
        self._add(m, "Manage &Suppliers…", lambda: self._nav("manage_suppliers"))
        m.addSeparator()
        self._add(m, "&Description Maps…", lambda: self._nav("manage_description_maps"))
        self._add(m, "&People…", lambda: self._nav("manage_people"))
        m.addSeparator()
        self._add(m, "&Trucks…", lambda: self._nav("manage_trucks"))
        self._add(m, "T&railers…", lambda: self._nav("manage_trailers"))
        self._add(m, "&Motorcycles & Cars…", lambda: self._nav("manage_motor_vehicles"))
        m.addSeparator()
        self._add(m, "&Users…", lambda: self._nav("manage_users"))
        self._add(m, "&Backups…", lambda: self._nav("backup"))

    def _suppliers_menu(self) -> None:
        m = self.addMenu("&Suppliers")
        self._add(m, "&Manage Suppliers", lambda: self._nav("manage_suppliers"))

    def _import_menu(self) -> None:
        """Shortcuts to every import surface in the app."""
        m = self.addMenu("&Import")
        self._add(
            m,
            "&Daily Register → Master…",
            lambda: self._nav("import_daily"),
        )
        self._add(
            m,
            "&Master Expenses Excel…",
            lambda: self._nav("master_expenses"),
        )
        m.addSeparator()

        expenses = m.addMenu("&Expenses")
        for label, key in [
            ("&Toll Plaza", "toll_plaza"),
            ("&Parking Congo", "parking_congo"),
            ("Congo &Expenses", "congo_exp"),
            ("&Ahmed Kimvi (Klesa)", "ahmed_kimvi"),
            ("&Zambia Parking", "zambia_parking"),
            ("Afri&track", "afritrack"),
            ("Third &Party Covers", "third_party"),
            ("CO&MESA Covers", "comesa"),
            ("&RahnTech", "rahntech"),
        ]:
            self._add(expenses, label, lambda k=key: self._nav(k))
        burhani = expenses.addMenu("&SM Burhani")
        self._add(
            burhani,
            "&RPA Schedule",
            lambda: self._sub("sm_burhani", "rpa_schedule"),
        )
        self._add(
            burhani,
            "&Bonds",
            lambda: self._sub("sm_burhani", "bonds"),
        )

        fuel = m.addMenu("&Fuel")
        for label, key in [
            ("&Infinity", "infinity"),
            ("Lake &Zambia", "lake_zambia"),
            ("Lake &Tunduma", "lake_tunduma"),
            ("&GBP Diesel", "gbp_diesel"),
        ]:
            self._add(fuel, label, lambda k=key: self._nav(k))

        m.addSeparator()
        lists = m.addMenu("&Lists / Setup")
        self._add(lists, "&Items (Chart of Accounts)", lambda: self._nav("manage_categories"))
        self._add(lists, "&Suppliers", lambda: self._nav("manage_suppliers"))
        self._add(lists, "&Trucks", lambda: self._nav("manage_trucks"))
        self._add(lists, "T&railers", lambda: self._nav("manage_trailers"))
        self._add(lists, "&Motorcycles & Cars", lambda: self._nav("manage_motor_vehicles"))

    def _accountant_menu(self) -> None:
        m = self.addMenu("&Accountant")
        self._add(m, "&Overview", lambda: self._nav("overview"))
        self._add(m, "&Truck Overview", lambda: self._nav("truck_overview"))
        self._add(m, "&Fuel Overview", lambda: self._nav("fuel_overview"))
        m.addSeparator()
        self._add(m, "&Verify", lambda: self._nav("verify"))
        self._add(m, "&Table", lambda: self._nav("table"))
        self._add(m, "&Browse", lambda: self._nav("browse"))
        self._add(m, "&Master Expenses", lambda: self._nav("master_expenses"))
        self._add(m, "Tras&h", lambda: self._nav("trash"))
        self._add(m, "&Import Daily → Master…", lambda: self._nav("import_daily"))

    def _company_menu(self) -> None:
        m = self.addMenu("&Company")
        self._add(m, "&Backups…", lambda: self._nav("backup"))
        self._add(m, "&Users", lambda: self._nav("manage_users"))

    def _expenses_menu(self) -> None:
        m = self.addMenu("E&xpenses")
        entries = [
            ("&Toll Plaza", "toll_plaza"),
            ("&Parking Congo", "parking_congo"),
            ("Congo &Expenses", "congo_exp"),
            ("&Ahmed Kimvi (Klesa)", "ahmed_kimvi"),
            ("&Zambia Parking", "zambia_parking"),
            ("Afri&track", "afritrack"),
            ("Third &Party Covers", "third_party"),
            ("CO&MESA Covers", "comesa"),
            ("&RahnTech", "rahntech"),
        ]
        for label, key in entries:
            self._add(m, label, lambda k=key: self._nav(k))
        m.addSeparator()
        burhani = m.addMenu("&SM Burhani")
        self._add(
            burhani,
            "&RPA Schedule",
            lambda: self._sub("sm_burhani", "rpa_schedule"),
        )
        self._add(
            burhani,
            "&Bonds",
            lambda: self._sub("sm_burhani", "bonds"),
        )

    def _fuel_menu(self) -> None:
        m = self.addMenu("F&uel")
        for label, key in [
            ("Fuel &Overview", "fuel_overview"),
            ("&Diesel Cash", "diesel_cash"),
            ("&Infinity", "infinity"),
            ("Lake &Zambia", "lake_zambia"),
            ("Lake &Tunduma", "lake_tunduma"),
            ("&GBP Diesel", "gbp_diesel"),
        ]:
            self._add(m, label, lambda k=key: self._nav(k))

    def _reports_menu(self) -> None:
        m = self.addMenu("&Reports")
        self._add(m, "&Overview Dashboard", lambda: self._nav("overview"))
        self._add(m, "&Truck Overview", lambda: self._nav("truck_overview"))
        self._add(m, "&Fuel Overview", lambda: self._nav("fuel_overview"))
        self._add(m, "&Master Expenses", lambda: self._nav("master_expenses"))

    def _window_menu(self) -> None:
        m = self.addMenu("&Window")
        self._add(m, "&Home (Overview)", lambda: self._nav("overview"))

    def _help_menu(self) -> None:
        m = self.addMenu("&Help")
        self._add(m, "&About Tahmeed Expense…", lambda: self._about_cb and self._about_cb())
