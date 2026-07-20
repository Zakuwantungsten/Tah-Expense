"""AccountantDashboard — Main shell: header + sidebar + content stack + status bar."""

from __future__ import annotations
import asyncio
from typing import Optional

from qasync import asyncSlot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QFrame, QDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QTimer

from tahmeed.models.user import User
from tahmeed.ui.dialogs.change_password_dialog import ChangePasswordDialog
from tahmeed.ui.accountant.header_bar import HeaderBar
from tahmeed.ui.accountant.sidebar import SidebarWidget
from tahmeed.ui.accountant.overview import OverviewWidget
from tahmeed.ui.accountant.truck_overview import TruckOverviewWidget
from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget
from tahmeed.ui.accountant.master_expenses import MasterExpensesWidget
from tahmeed.ui.accountant.separate_expenses import (
    TollPlazaWidget,
    ParkingCongoWidget,
    CongoExpensesWidget,
    AhmedKimviWidget,
    ZambiaParkingWidget,
    AfritrackWidget,
    ThirdPartyWidget,
    ComesaWidget,
    RahnTechWidget,
)
from tahmeed.ui.accountant.category_tables import CategoryTableWidget
from tahmeed.ui.accountant.reconciliation import RPAScheduleWidget, BondsWidget
from tahmeed.ui.accountant.fuel_consumption import (
    InfinityWidget, LakeZambiaWidget, LakeTundumaWidget, GBPDieselWidget,
)
from tahmeed.ui.accountant.diesel_cash import DieselCashWidget
from tahmeed.ui.accountant.fleet_registry import TrucksRegistryWidget, TrailersRegistryWidget
from tahmeed.ui.accountant.manage_items import ManageItemsWidget
from tahmeed.ui.accountant.backup import BackupWidget
from tahmeed.ui.admin.users_tab import UsersTab

_APP_BG = "#F4F6F8"


class AccountantDashboard(QWidget):
    logout_requested = Signal()

    def __init__(self, user: User, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._user = user
        self._notification_poll_in_flight = False
        self._build()
        self._notification_timer = QTimer(self)
        self._notification_timer.setInterval(5_000)
        self._notification_timer.timeout.connect(self._poll_notification_counts)
        self._notification_timer.start()
        self._poll_notification_counts()

    def _build(self) -> None:
        self.setObjectName("accountantDashboard")
        self.setStyleSheet(
            f"QWidget#accountantDashboard {{ background: {_APP_BG}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ─────────────────────────────────────────────────────
        self._header = HeaderBar(
            user=self._user,
            sidebar_toggle_fn=self._toggle_sidebar,
            show_search=False,
        )
        self._header.logout_requested.connect(self.logout_requested)
        self._header.change_password_requested.connect(self._on_change_password)
        root.addWidget(self._header)

        # ── Body = sidebar + content ───────────────────────────────────────
        body = QWidget()
        body.setObjectName("accountantBody")
        body.setStyleSheet(
            f"QWidget#accountantBody {{ background: {_APP_BG}; }}"
        )
        body_hl = QHBoxLayout(body)
        body_hl.setContentsMargins(0, 0, 0, 0)
        body_hl.setSpacing(0)

        self._sidebar = SidebarWidget()
        self._sidebar.nav_selected.connect(self._on_nav)
        self._sidebar.subtable_selected.connect(self._show_subtable)
        body_hl.addWidget(self._sidebar)

        # 1-px vertical divider
        vline = QFrame()
        vline.setFrameShape(QFrame.VLine)
        vline.setFixedWidth(1)
        vline.setStyleSheet("background: #E5E7EB;")
        body_hl.addWidget(vline)

        # Content stack
        self._stack = QStackedWidget()
        self._stack.setObjectName("contentStack")
        self._stack.setStyleSheet(
            f"QStackedWidget#contentStack {{ background: {_APP_BG}; }}"
        )
        self._overview = OverviewWidget()
        self._stack.addWidget(self._overview)         # index 0 — Overview

        self._truck_overview = TruckOverviewWidget()
        self._stack.addWidget(self._truck_overview)   # index 1 — Truck Overview

        self._verify_inbox = VerifyInboxWidget(user=self._user)
        self._verify_inbox.badge_updated.connect(self._on_badge_updated)
        self._stack.addWidget(self._verify_inbox)     # index 2 — Verify Inbox

        self._master_expenses = MasterExpensesWidget(user=self._user)
        self._stack.addWidget(self._master_expenses)  # index 3 — Master Expenses

        # ── Separate Expenses ─────────────────────────────────────────────────
        self._toll_plaza       = TollPlazaWidget()
        self._stack.addWidget(self._toll_plaza)        # index 4

        self._parking_congo    = ParkingCongoWidget()
        self._stack.addWidget(self._parking_congo)     # index 5

        self._congo_exp        = CongoExpensesWidget()
        self._stack.addWidget(self._congo_exp)         # index 6

        self._ahmed_kimvi      = AhmedKimviWidget()
        self._stack.addWidget(self._ahmed_kimvi)       # index 7

        self._zambia_parking   = ZambiaParkingWidget()
        self._stack.addWidget(self._zambia_parking)    # index 8

        self._afritrack        = AfritrackWidget()
        self._stack.addWidget(self._afritrack)         # index 9

        self._third_party      = ThirdPartyWidget()
        self._stack.addWidget(self._third_party)       # index 10

        self._comesa           = ComesaWidget()
        self._stack.addWidget(self._comesa)            # index 11

        self._stack.addWidget(_PlaceholderPage())      # index 12 — other sections

        # ── Fuel Consumption ──────────────────────────────────────────────────
        self._diesel_cash   = DieselCashWidget()
        self._stack.addWidget(self._diesel_cash)        # index 13

        self._infinity      = InfinityWidget()
        self._stack.addWidget(self._infinity)           # index 14

        self._lake_zambia   = LakeZambiaWidget()
        self._stack.addWidget(self._lake_zambia)        # index 15

        self._lake_tunduma  = LakeTundumaWidget()
        self._stack.addWidget(self._lake_tunduma)       # index 16

        self._gbp_diesel    = GBPDieselWidget()
        self._stack.addWidget(self._gbp_diesel)         # index 17

        # ── RahnTech ──────────────────────────────────────────────────────────
        self._rahntech = RahnTechWidget()
        self._stack.addWidget(self._rahntech)           # index 18

        # ── Fleet Registry ────────────────────────────────────────────────────
        self._trucks_registry = TrucksRegistryWidget()
        self._stack.addWidget(self._trucks_registry)    # index 19

        self._trailers_registry = TrailersRegistryWidget()
        self._stack.addWidget(self._trailers_registry)  # index 20

        self._manage_items = ManageItemsWidget()
        self._stack.addWidget(self._manage_items)        # index 21
        # Rebuild the sidebar's dynamic ITEMS whenever the accountant changes them.
        self._manage_items.items_changed.connect(self._sidebar.refresh_items)

        self._users_tab = UsersTab()
        self._stack.addWidget(self._users_tab)           # index 22

        self._backup = BackupWidget()
        self._stack.addWidget(self._backup)              # index 23

        # Live-refresh a single item's sub-item strip when its sub-items change.
        self._manage_items.subitems_changed.connect(self._sidebar.refresh_subitems)

        # Category tables (one per ITEMS sidebar key) and user-created
        # sub-tables are created lazily and cached here as key -> stack index.
        self._category_indices: dict[str, int] = {}
        self._subtable_indices: dict[str, int] = {}
        self._recon_indices: dict[str, int] = {}   # "rpa_schedule" | "bonds" -> stack idx

        self._stack.setCurrentIndex(0)
        body_hl.addWidget(self._stack, 1)

        root.addWidget(body, 1)

        # ── Status bar ──────────────────────────────────────────────────────
        root.addWidget(_StatusBar())

    # ── Slot handlers ───────────────────────────────────────────────────────

    def _toggle_sidebar(self) -> None:
        self._sidebar.toggle_collapsed()

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

    def _on_badge_updated(self, count: int) -> None:
        self._sidebar.set_verify_badge(count)

    @asyncSlot()
    async def _poll_notification_counts(self) -> None:
        if self._notification_poll_in_flight:
            return
        self._notification_poll_in_flight = True
        try:
            from tahmeed.services.notification_service import (
                get_verify_notification_count,
            )

            count = await get_verify_notification_count()
            self._sidebar.set_verify_badge(count)
        except Exception:
            # A transient API failure must not make a known count disappear.
            pass
        finally:
            self._notification_poll_in_flight = False

    def _on_nav(self, key: str) -> None:
        _routes = {
            "overview":       (0,  self._overview),
            "truck_overview": (1,  self._truck_overview),
            "verify":         (2,  self._verify_inbox),
            "master_expenses":(3,  self._master_expenses),
            "toll_plaza":     (4,  self._toll_plaza),
            "parking_congo":  (5,  self._parking_congo),
            "congo_exp":      (6,  self._congo_exp),
            "ahmed_kimvi":    (7,  self._ahmed_kimvi),
            "zambia_parking": (8,  self._zambia_parking),
            "afritrack":      (9,  self._afritrack),
            "third_party":    (10, self._third_party),
            "comesa":         (11, self._comesa),
            "diesel_cash":    (13, self._diesel_cash),
            "infinity":       (14, self._infinity),
            "lake_zambia":    (15, self._lake_zambia),
            "lake_tunduma":   (16, self._lake_tunduma),
            "gbp_diesel":     (17, self._gbp_diesel),
            "rahntech":         (18, self._rahntech),
            "manage_trucks":    (19, self._trucks_registry),
            "manage_trailers":  (20, self._trailers_registry),
            "manage_categories":(21, self._manage_items),
            "manage_users":     (22, self._users_tab),
            "backup":           (23, self._backup),
        }
        if key in _routes:
            idx, widget = _routes[key]
            self._stack.setCurrentIndex(idx)
            refresh = getattr(widget, "refresh", None)
            if callable(refresh):
                refresh()
        elif self._sidebar.item_def(key) is not None:
            self._show_category(key)
        elif key == "sm_burhani":
            # Parent click is handled by the sidebar (expand + first sub-item).
            pass
        else:
            self._stack.setCurrentIndex(12)

    def _show_category(self, key: str) -> None:
        """Lazily create (and cache) the item table for this dynamic sidebar key."""
        if key not in self._category_indices:
            d = self._sidebar.item_def(key)
            if d is None:
                self._stack.setCurrentIndex(11)
                return
            title, icon = d
            widget = CategoryTableWidget(category_name=title, title=title, icon_name=icon)
            self._category_indices[key] = self._stack.addWidget(widget)
        idx = self._category_indices[key]
        self._stack.setCurrentIndex(idx)
        self._stack.widget(idx).refresh()

    def _show_recon(self, match: str) -> None:
        """Lazily create (and cache) an SM Burhani reconciliation view."""
        if match not in self._recon_indices:
            widget = BondsWidget() if match == "bonds" else RPAScheduleWidget()
            self._recon_indices[match] = self._stack.addWidget(widget)
        idx = self._recon_indices[match]
        self._stack.setCurrentIndex(idx)
        self._stack.widget(idx).refresh()

    def _show_subtable(self, parent_key: str, parent_category: str,
                       name: str, match: str) -> None:
        """Lazily create (and cache) a sub-table view for a parent category."""
        if parent_key == "sm_burhani":
            self._show_recon(match)
            return
        cache_key = f"{parent_key}::{name}"
        if cache_key not in self._subtable_indices:
            d = self._sidebar.item_def(parent_key)
            icon = d[1] if d else "mdi.tag-outline"
            widget = CategoryTableWidget(
                category_name=parent_category,
                title=f"{parent_category} · {name}",
                icon_name=icon,
                description_filter=match or name,
            )
            self._subtable_indices[cache_key] = self._stack.addWidget(widget)
        idx = self._subtable_indices[cache_key]
        self._stack.setCurrentIndex(idx)
        self._stack.widget(idx).refresh()


# ── Placeholder ────────────────────────────────────────────────────────────────

class _PlaceholderPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        vl = QVBoxLayout(self)
        vl.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent;")
        try:
            import qtawesome as qta
            icon_lbl.setPixmap(
                qta.icon("mdi.view-dashboard-outline", color="#D1D5DB").pixmap(64, 64)
            )
        except Exception:
            pass
        vl.addWidget(icon_lbl)
        vl.addSpacing(12)

        hint = QLabel("Select a section from the sidebar to get started")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            "color: #9CA3AF; font-size: 14px;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        vl.addWidget(hint)


# ── Status bar ─────────────────────────────────────────────────────────────────

class _StatusBar(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("accountantStatusBar")
        self.setFixedHeight(24)
        self.setStyleSheet(
            "QFrame#accountantStatusBar {"
            "  background: #FFFFFF;"
            "  border-top: 1px solid #E5E7EB;"
            "}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(0)

        dot = QLabel("●")
        dot.setStyleSheet(
            "color: #16A34A; font-size: 9px; background: transparent;"
        )
        hl.addWidget(dot)
        hl.addSpacing(5)

        status = QLabel(
            "Connected · MongoDB Atlas"
            "     |     Last refresh: —"
            "     |     FY 2025"
            "     |     v1.0.0"
        )
        status.setStyleSheet(
            "color: #6B7280; font-size: 11px;"
            " font-family:'Segoe UI'; background: transparent;"
        )
        hl.addWidget(status)
        hl.addStretch()
