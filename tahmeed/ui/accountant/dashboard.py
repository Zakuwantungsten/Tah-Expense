"""AccountantDashboard — Main shell: title + menu + header + sidebar + content."""

from __future__ import annotations
import asyncio
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QFrame, QDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QTimer

from tahmeed.config import APP_NAME, APP_VERSION
from tahmeed.models.user import User
from tahmeed.services.category_service import get_all_categories
from tahmeed.ui.dialogs.change_password_dialog import ChangePasswordDialog
from tahmeed.ui.accountant.menu_bar import AccountantMenuBar
from tahmeed.ui.accountant.sidebar import SidebarWidget
from tahmeed.ui.accountant.title_bar import AccountantTitleBar
from tahmeed.ui.accountant.overview import OverviewWidget

_APP_BG = "#F4F6F8"

# Sidebar keys that map to dedicated pages (created on first visit).
_LAZY_PAGE_KEYS = frozenset({
    "truck_overview",
    "verify",
    "master_expenses",
    "import_daily",
    "toll_plaza",
    "parking_congo",
    "congo_exp",
    "ahmed_kimvi",
    "zambia_parking",
    "afritrack",
    "third_party",
    "comesa",
    "diesel_cash",
    "infinity",
    "lake_zambia",
    "lake_tunduma",
    "gbp_diesel",
    "rahntech",
    "manage_trucks",
    "manage_trailers",
    "manage_motor_vehicles",
    "manage_categories",
    "manage_description_maps",
    "manage_people",
    "manage_users",
    "backup",
    "table",
    "browse",
})


class AccountantDashboard(QWidget):
    logout_requested = Signal()

    def __init__(self, user: User, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._user = user
        self._notification_poll_in_flight = False
        # Lazily created pages (except overview).
        self._pages: dict[str, QWidget] = {}
        self._page_indices: dict[str, int] = {}
        self._register = None  # DailyRegister, created with "table"
        self._pending_categories: list | None = None
        self._pending_people: list | None = None
        self._build()
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_categories())
        self._notification_timer = QTimer(self)
        self._notification_timer.setInterval(2_000)
        self._notification_timer.timeout.connect(self._poll_notification_counts)
        self._notification_poll_task: asyncio.Task | None = None
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

        # ── QuickBooks-style chrome ───────────────────────────────────────
        self._title_bar = AccountantTitleBar(user=self._user)
        self._title_bar.minimize_requested.connect(self._on_minimize)
        self._title_bar.maximize_requested.connect(self._on_maximize)
        self._title_bar.close_requested.connect(self._on_exit)
        root.addWidget(self._title_bar)

        self._menu_bar = AccountantMenuBar(
            user_display_name=self._user.full_name or self._user.username,
        )
        self._menu_bar.bind(
            navigate=self._menu_navigate,
            navigate_sub=self._menu_navigate_sub,
            toggle_sidebar=self._toggle_sidebar,
            refresh=self._refresh_current,
            change_password=self._on_change_password,
            logout=self.logout_requested.emit,
            exit_app=self._on_exit,
            about=self._on_about,
            find=self._on_find,
        )
        root.addWidget(self._menu_bar)

        from tahmeed.ui.widgets.connectivity_banner import ConnectivityBanner
        from tahmeed.ui.widgets.live_status_bar import LiveStatusBar

        self._connectivity_banner = ConnectivityBanner()
        root.addWidget(self._connectivity_banner)

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

        # Content stack — only Overview (+ placeholder) at login.
        # Heavy tabs are created on first navigation to keep login snappy.
        self._stack = QStackedWidget()
        self._stack.setObjectName("contentStack")
        self._stack.setStyleSheet(
            f"QStackedWidget#contentStack {{ background: {_APP_BG}; }}"
        )
        self._overview = OverviewWidget()
        self._overview.navigate.connect(self._on_overview_nav)
        self._page_indices["overview"] = self._stack.addWidget(self._overview)
        self._pages["overview"] = self._overview

        self._placeholder_index = self._stack.addWidget(_PlaceholderPage())

        # Category tables / recon views — already lazy; keep same caches.
        self._category_indices: dict[str, int] = {}
        self._subtable_indices: dict[str, int] = {}
        self._recon_indices: dict[str, int] = {}

        self._stack.setCurrentIndex(self._page_indices["overview"])
        body_hl.addWidget(self._stack, 1)

        root.addWidget(body, 1)
        root.addWidget(
            LiveStatusBar(
                object_name="accountantStatusBar",
                mode_label="FY 2025",
                dark=False,
            )
        )

    # ── Lazy page factories ─────────────────────────────────────────────────

    def _ensure_page(self, key: str) -> QWidget:
        """Create and cache a dedicated page the first time it is opened."""
        if key in self._pages:
            return self._pages[key]
        widget = self._create_page(key)
        self._page_indices[key] = self._stack.addWidget(widget)
        self._pages[key] = widget
        return widget

    def _create_page(self, key: str) -> QWidget:
        # Imports are deferred so login does not pay for every heavy module.
        if key == "truck_overview":
            from tahmeed.ui.accountant.truck_overview import TruckOverviewWidget
            return TruckOverviewWidget()

        if key == "verify":
            from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget
            widget = VerifyInboxWidget(user=self._user)
            widget.badge_updated.connect(self._on_badge_updated)
            return widget

        if key == "master_expenses":
            from tahmeed.ui.accountant.master_expenses import MasterExpensesWidget
            return MasterExpensesWidget(user=self._user)

        if key == "import_daily":
            from tahmeed.ui.accountant.import_daily_master import ImportDailyMasterWidget
            return ImportDailyMasterWidget(user=self._user)

        if key == "toll_plaza":
            from tahmeed.ui.accountant.separate_expenses import TollPlazaWidget
            return TollPlazaWidget()

        if key == "parking_congo":
            from tahmeed.ui.accountant.separate_expenses import ParkingCongoWidget
            return ParkingCongoWidget()

        if key == "congo_exp":
            from tahmeed.ui.accountant.separate_expenses import CongoExpensesWidget
            return CongoExpensesWidget()

        if key == "ahmed_kimvi":
            from tahmeed.ui.accountant.separate_expenses import AhmedKimviWidget
            return AhmedKimviWidget()

        if key == "zambia_parking":
            from tahmeed.ui.accountant.separate_expenses import ZambiaParkingWidget
            return ZambiaParkingWidget()

        if key == "afritrack":
            from tahmeed.ui.accountant.separate_expenses import AfritrackWidget
            return AfritrackWidget()

        if key == "third_party":
            from tahmeed.ui.accountant.separate_expenses import ThirdPartyWidget
            return ThirdPartyWidget()

        if key == "comesa":
            from tahmeed.ui.accountant.separate_expenses import ComesaWidget
            return ComesaWidget()

        if key == "diesel_cash":
            from tahmeed.ui.accountant.diesel_cash import DieselCashWidget
            return DieselCashWidget()

        if key == "infinity":
            from tahmeed.ui.accountant.fuel_consumption import InfinityWidget
            return InfinityWidget()

        if key == "lake_zambia":
            from tahmeed.ui.accountant.fuel_consumption import LakeZambiaWidget
            return LakeZambiaWidget()

        if key == "lake_tunduma":
            from tahmeed.ui.accountant.fuel_consumption import LakeTundumaWidget
            return LakeTundumaWidget()

        if key == "gbp_diesel":
            from tahmeed.ui.accountant.fuel_consumption import GBPDieselWidget
            return GBPDieselWidget()

        if key == "rahntech":
            from tahmeed.ui.accountant.separate_expenses import RahnTechWidget
            return RahnTechWidget()

        if key == "manage_trucks":
            from tahmeed.ui.accountant.fleet_registry import TrucksRegistryWidget
            return TrucksRegistryWidget()

        if key == "manage_trailers":
            from tahmeed.ui.accountant.fleet_registry import TrailersRegistryWidget
            return TrailersRegistryWidget()

        if key == "manage_motor_vehicles":
            from tahmeed.ui.accountant.fleet_registry import MotorVehiclesRegistryWidget
            return MotorVehiclesRegistryWidget()

        if key == "manage_categories":
            from tahmeed.ui.accountant.manage_items import ManageItemsWidget
            widget = ManageItemsWidget()
            widget.items_changed.connect(self._sidebar.refresh_items)
            widget.subitems_changed.connect(self._sidebar.refresh_subitems)
            return widget

        if key == "manage_description_maps":
            from tahmeed.ui.accountant.manage_description_maps import DescriptionMapsWidget
            return DescriptionMapsWidget()

        if key == "manage_people":
            from tahmeed.ui.accountant.manage_people import PeopleRegistryWidget
            return PeopleRegistryWidget()

        if key == "manage_users":
            from tahmeed.ui.admin.users_tab import UsersTab
            return UsersTab()

        if key == "backup":
            from tahmeed.ui.accountant.backup import BackupWidget
            widget = BackupWidget(allow_restore=True)
            widget.logout_requested.connect(self.logout_requested)
            return widget

        if key == "table":
            from tahmeed.ui.cashier.excel_grid import DailyRegister
            from tahmeed.ui.cashier.dashboard import _TablePage
            self._register = DailyRegister(user=self._user, categories=[])
            if self._pending_categories is not None:
                self._register.update_categories(self._pending_categories)
                self._pending_categories = None
            if self._pending_people is not None:
                self._register.update_people(self._pending_people)
                self._pending_people = None
            page = _TablePage(self._register)
            return page

        if key == "browse":
            from tahmeed.ui.cashier.transactions_table import TransactionBrowser
            widget = TransactionBrowser()
            widget.go_to_date.connect(self._on_go_to_date)
            widget.go_to_upload.connect(self._on_go_to_upload)
            return widget

        raise KeyError(f"Unknown lazy page key: {key}")

    # ── Slot handlers ───────────────────────────────────────────────────────

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
        self._sidebar.select(key)
        self._on_nav(key)

    def _menu_navigate_sub(self, parent_key: str, match: str) -> None:
        labels = {"rpa_schedule": "RPA Schedule", "bonds": "Bonds"}
        name = labels.get(match, match)
        self._sidebar.select(parent_key)
        expanded = getattr(self._sidebar, "_expanded", set())
        if parent_key not in expanded:
            toggle = getattr(self._sidebar, "_on_toggle", None)
            if callable(toggle):
                toggle(parent_key)
        self._show_subtable(parent_key, "SM Burhani", name, match)

    def _refresh_current(self) -> None:
        widget = self._stack.currentWidget()
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            (
                f"<b>{APP_NAME}</b><br>"
                f"Accountant Edition<br><br>"
                f"Version {APP_VERSION}<br>"
                f"TAHMEED TRANSPORTERS"
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

    def sync_chrome_maximized(self, maximized: bool) -> None:
        """Keep title-bar restore/maximize icon in sync with the host window."""
        self._title_bar.set_maximized(maximized)

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

    def _on_overview_nav(self, key: str) -> None:
        self._sidebar.select(key)
        self._on_nav(key)

    def pause_notification_polling(self) -> None:
        """Stop badge and connectivity polls during nested Qt dialogs (Py3.14)."""
        self._notification_timer.stop()
        task = getattr(self, "_notification_poll_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._notification_poll_task = None
        self._notification_poll_in_flight = False
        from tahmeed.services.connectivity_service import connectivity_monitor

        connectivity_monitor.pause()

    def resume_notification_polling(self) -> None:
        if not self._notification_timer.isActive():
            self._notification_timer.start()
        from tahmeed.services.connectivity_service import connectivity_monitor

        connectivity_monitor.resume()

    def _poll_notification_counts(self) -> None:
        """QTimer slot — schedule poll without nesting into a running task.

        Using ``@asyncSlot`` here conflicts with Python 3.12+/3.14 when another
        coroutine (e.g. truck Excel/PDF export) is mid-execution: qasync tries to
        enter the poll task while that other task is still current.

        Also skip this tick when ``asyncio.current_task()`` is set — that means we
        are inside another coroutine's nested Qt event loop (e.g. QFileDialog /
        QMessageBox). The next timer tick will retry.
        """
        if self._notification_poll_in_flight:
            return
        if not self._notification_timer.isActive():
            return
        from tahmeed.ui.async_utils import create_task, in_running_task

        if in_running_task():
            return
        # Drop a stale unfinished poll rather than stacking wakeups mid-modal.
        prev = self._notification_poll_task
        if prev is not None and not prev.done():
            return
        self._notification_poll_task = create_task(
            self._poll_notification_counts_async()
        )

    async def _poll_notification_counts_async(self) -> None:
        if self._notification_poll_in_flight:
            return
        if not self._notification_timer.isActive():
            return
        self._notification_poll_in_flight = True
        try:
            from tahmeed.services.notification_service import (
                get_verify_notification_count,
            )

            count = await get_verify_notification_count()
            self._sidebar.set_verify_badge(count)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient API failure must not make a known count disappear.
            pass
        finally:
            self._notification_poll_in_flight = False

    def _on_nav(self, key: str) -> None:
        from tahmeed.ui.async_utils import schedule_call

        if key == "overview":
            self._stack.setCurrentIndex(self._page_indices["overview"])
            refresh = getattr(self._overview, "refresh", None)
            if callable(refresh):
                schedule_call(refresh)
            return

        if key in _LAZY_PAGE_KEYS:
            from tahmeed.ui.async_utils import in_running_task

            def _open_lazy(k: str = key) -> None:
                try:
                    widget = self._ensure_page(k)
                    self._stack.setCurrentIndex(self._page_indices[k])
                    if k == "table":
                        assert self._register is not None
                        self._register.reload_settings()
                        return
                    refresh = getattr(widget, "refresh", None)
                    if callable(refresh):
                        schedule_call(refresh)
                except Exception as exc:
                    from PySide6.QtWidgets import QMessageBox

                    QMessageBox.critical(
                        self,
                        "Unable to open page",
                        f"Failed to open this page:\n{exc}",
                    )

            # First-time page __init__ may schedule coroutines; building while
            # another Task is current crashes under Python 3.14 + qasync.
            if in_running_task() and key not in self._pages:
                schedule_call(_open_lazy)
            else:
                _open_lazy()
            return

        if self._sidebar.item_def(key) is not None:
            self._show_category(key)
        elif key == "sm_burhani":
            # Parent click is handled by the sidebar (expand + first sub-item).
            pass
        else:
            self._stack.setCurrentIndex(self._placeholder_index)

    def _on_go_to_date(self, d, term: str = "") -> None:
        from tahmeed.ui.async_utils import in_running_task, schedule_call

        self._sidebar.select("table")

        def _go() -> None:
            self._ensure_page("table")
            self._stack.setCurrentIndex(self._page_indices["table"])
            assert self._register is not None
            self._register.reload_settings()
            self._register.navigate_to_date(d, highlight_term=term, merged=True)

        if in_running_task() and "table" not in self._pages:
            schedule_call(_go)
        else:
            _go()

    def _on_go_to_upload(self, upload_id: str, primary_date=None) -> None:
        from tahmeed.ui.async_utils import in_running_task, schedule_call

        self._sidebar.select("table")

        def _go() -> None:
            self._ensure_page("table")
            self._stack.setCurrentIndex(self._page_indices["table"])
            assert self._register is not None
            self._register.reload_settings()
            self._register.navigate_to_upload(upload_id, primary_date)

        if in_running_task() and "table" not in self._pages:
            schedule_call(_go)
        else:
            _go()

    async def prepare_to_leave(self) -> bool:
        """Prompt to save/discard unsaved table entries before logout or exit."""
        if self._register is None:
            return True
        return await self._register.confirm_leave()

    async def _load_categories(self) -> None:
        try:
            cats = await get_all_categories()
            if self._register is not None:
                self._register.update_categories(cats)
            else:
                self._pending_categories = cats
        except Exception:
            pass
        try:
            from tahmeed.services.people_service import get_people_names
            people = await get_people_names()
            if self._register is not None:
                self._register.update_people(people)
            else:
                self._pending_people = people
        except Exception:
            pass

    def _show_category(self, key: str) -> None:
        """Lazily create (and cache) the item table for this dynamic sidebar key."""
        if key not in self._category_indices:
            from tahmeed.ui.accountant.category_tables import CategoryTableWidget
            d = self._sidebar.item_def(key)
            if d is None:
                self._stack.setCurrentIndex(self._placeholder_index)
                return
            title, icon, label = d
            widget = CategoryTableWidget(category_name=title, title=label, icon_name=icon)
            self._category_indices[key] = self._stack.addWidget(widget)
        idx = self._category_indices[key]
        self._stack.setCurrentIndex(idx)
        from tahmeed.ui.async_utils import schedule_call
        schedule_call(self._stack.widget(idx).refresh)

    def _show_recon(self, match: str) -> None:
        """Lazily create (and cache) an SM Burhani reconciliation view."""
        if match not in self._recon_indices:
            from tahmeed.ui.accountant.reconciliation import (
                RPAScheduleWidget, BondsWidget,
            )
            widget = BondsWidget() if match == "bonds" else RPAScheduleWidget()
            self._recon_indices[match] = self._stack.addWidget(widget)
        idx = self._recon_indices[match]
        self._stack.setCurrentIndex(idx)
        from tahmeed.ui.async_utils import schedule_call
        schedule_call(self._stack.widget(idx).refresh)

    def _show_subtable(self, parent_key: str, parent_category: str,
                       name: str, match: str) -> None:
        """Lazily create (and cache) a sub-table view for a parent category."""
        if parent_key == "sm_burhani":
            self._show_recon(match)
            return
        cache_key = f"{parent_key}::{name}"
        if cache_key not in self._subtable_indices:
            from tahmeed.ui.accountant.category_tables import CategoryTableWidget
            d = self._sidebar.item_def(parent_key)
            icon = d[1] if d else "mdi.tag-outline"
            parent_label = d[2] if d else parent_category
            widget = CategoryTableWidget(
                category_name=parent_category,
                title=f"{parent_label} · {name}",
                icon_name=icon,
                description_filter=match or name,
            )
            self._subtable_indices[cache_key] = self._stack.addWidget(widget)
        idx = self._subtable_indices[cache_key]
        self._stack.setCurrentIndex(idx)
        from tahmeed.ui.async_utils import schedule_call
        schedule_call(self._stack.widget(idx).refresh)


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

