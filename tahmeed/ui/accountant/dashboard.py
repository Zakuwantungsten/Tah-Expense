"""AccountantDashboard — Main shell: title + menu + header + sidebar + content."""

from __future__ import annotations
import asyncio
import time
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QFrame, QDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QTimer

from tahmeed.config import APP_NAME, APP_VERSION, COMPANY_NAME
from tahmeed.models.user import User
from tahmeed.services.category_service import get_payment_target_categories
from tahmeed.ui.dialogs.change_password_dialog import ChangePasswordDialog
from tahmeed.ui.accountant.menu_bar import AccountantMenuBar
from tahmeed.ui.accountant.sidebar import SidebarWidget
from tahmeed.ui.accountant.title_bar import AccountantTitleBar
from tahmeed.ui.accountant.overview import OverviewWidget
from tahmeed.ui.widgets.circuit_geometry import wrap_circuit_page

_APP_BG = "#F4F6F8"

# Sidebar keys that map to dedicated pages (created on first visit).
_LAZY_PAGE_KEYS = frozenset({
    "truck_overview",
    "verify",
    "drafts",
    "saved",
    "day_issues",
    "master_expenses",
    "trash",
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
    "fuel_overview",
    "infinity",
    "lake_zambia",
    "lake_tunduma",
    "gbp_diesel",
    "rahntech",
    "feeds_trash",
    "manage_trucks",
    "manage_trailers",
    "manage_motor_vehicles",
    "manage_categories",
    "manage_suppliers",
    "manage_description_maps",
    "manage_people",
    "manage_payees",
    "manage_users",
    "receipt_disk",
    "receipts",
    "receipt_browse",
    "table",
    "browse",
})

# Separate Expenses feeds (subset of lazy pages) — warm-imported after login.
_SEPARATE_EXPENSE_KEYS = frozenset({
    "toll_plaza",
    "parking_congo",
    "congo_exp",
    "ahmed_kimvi",
    "zambia_parking",
    "afritrack",
    "third_party",
    "comesa",
    "rahntech",
})

# Every first-open lazy tab uses an Opening… shell so the stack flips before
# heavy __init__ (Verify, Master, Table, Separate Expenses, manage pages, …).
_SHELL_FIRST_KEYS = _LAZY_PAGE_KEYS

# Long-poll wait; reconnects immediately when system_counters change.
_NOTIFICATION_WAIT_S = 25.0

# Opening the register after one of these pages repeats the catalog snapshot.
_CATALOG_EDITORS = frozenset({
    "manage_categories",
    "manage_suppliers",
    "manage_trucks",
    "manage_trailers",
    "manage_motor_vehicles",
    "manage_payees",
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
        self._pending_payees: list | None = None
        self._day_issues_badge_timer = QTimer(self)
        self._day_issues_badge_timer.setSingleShot(True)
        self._day_issues_badge_timer.setInterval(2_000)
        self._day_issues_badge_timer.timeout.connect(self._run_debounced_day_issues_badge)
        self._build()
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_categories())
        from tahmeed.services.notification_service import (
            reset_verify_notification_cache,
        )

        reset_verify_notification_cache()
        self._notification_live = True
        self._notification_poll_in_flight = False
        self._protect_verify_badge_until = 0.0
        self._notification_poll_task: asyncio.Task | None = None
        # Keep a timer only as a watchdog to restart a dead long-poll loop.
        self._notification_timer = QTimer(self)
        self._notification_timer.setInterval(int(_NOTIFICATION_WAIT_S * 1000) + 5_000)
        self._notification_timer.timeout.connect(self._ensure_notification_stream)
        self._notification_timer.start()
        self._start_notification_stream()
        schedule_coro(self._refresh_day_issues_badge())
        # Preload heavy modules after the UI is up so first sidebar clicks
        # spend time on widget build, not cold import.
        QTimer.singleShot(0, self._warm_import_heavy_pages)

    @staticmethod
    def _warm_import_separate_expenses() -> None:
        """Compat shim — prefer ``_warm_import_heavy_pages``."""
        AccountantDashboard._warm_import_heavy_pages()

    @staticmethod
    def _warm_import_heavy_pages() -> None:
        try:
            import tahmeed.ui.accountant.separate_expenses  # noqa: F401
            import tahmeed.ui.accountant.verify_inbox  # noqa: F401
            import tahmeed.ui.accountant.master_expenses  # noqa: F401
            import tahmeed.ui.cashier.excel_grid  # noqa: F401
        except Exception:
            pass

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

        # Bottom-right toast — click feedback while tab chrome / data catches up.
        from tahmeed.ui.widgets.nav_toast import NavToast

        self._nav_toast = NavToast(self)

    # ── Nav toast helpers ───────────────────────────────────────────────────

    def _nav_toast_label(self, key: str) -> str:
        """Human label for a sidebar / stack key."""
        from tahmeed.ui.accountant.sidebar import _SECTIONS

        for _section, items in _SECTIONS:
            for item_key, label, *_rest in items:
                if item_key == key:
                    return str(label)
        d = self._sidebar.item_def(key)
        if d is not None:
            # Dynamic ITEMS: (name, icon, sidebar_label)
            if len(d) >= 3 and d[2]:
                return str(d[2])
            if d[0]:
                return str(d[0])
        if "::" in key:
            return key.split("::")[-1].replace("_", " ").strip().title() or key
        return key.replace("_", " ").strip().title() or "page"

    def _toast_opening(self, key: str) -> None:
        toast = getattr(self, "_nav_toast", None)
        if toast is None:
            return
        toast.show_opening(AccountantDashboard._nav_toast_label(self, key))

    def _toast_loading(self, key: str) -> None:
        toast = getattr(self, "_nav_toast", None)
        if toast is None:
            return
        toast.show_loading(AccountantDashboard._nav_toast_label(self, key))

    def _toast_dismiss(self) -> None:
        toast = getattr(self, "_nav_toast", None)
        if toast is not None:
            toast.dismiss()

    # ── Lazy page factories ─────────────────────────────────────────────────

    def _mount_page(self, key: str, widget: QWidget) -> QWidget:
        """Wrap non-overview pages in the seeded circuit canvas host."""
        return wrap_circuit_page(widget, seed=key, bg=_APP_BG)

    def _ensure_page(self, key: str) -> QWidget:
        """Create and cache a dedicated page the first time it is opened."""
        if key in self._pages:
            return self._pages[key]
        widget = self._mount_page(key, self._create_page(key))
        self._page_indices[key] = self._stack.addWidget(widget)
        self._pages[key] = widget
        return widget

    def _activate_page(self, key: str) -> QWidget:
        """Show a cached page by widget identity — stack indices shift when shells are removed."""
        widget = self._pages[key]
        self._stack.setCurrentWidget(widget)
        self._page_indices[key] = self._stack.indexOf(widget)
        return widget

    def _after_lazy_activated(self, key: str, widget: QWidget) -> None:
        """Kick page-specific load after the stack already shows ``widget``."""
        from tahmeed.ui.async_utils import schedule_call

        # Page is visible — toast shifts to Loading… (covers network lag).
        AccountantDashboard._toast_loading(self, key)

        if key == "table":
            assert self._register is not None
            self._register.reload_settings()
            return
        if key == "day_issues":
            from datetime import date as _date

            target = _date.today()
            if self._register is not None and getattr(
                self._register, "_current_date", None
            ):
                target = self._register._current_date
            # Keep Verify VALUE filter / All-days / date on tab return.
            if hasattr(widget, "on_page_shown"):
                widget.on_page_shown(target)
                return
            if hasattr(widget, "open_for_date"):
                widget.open_for_date(target)
                return
            if hasattr(widget, "set_date"):
                widget.set_date(target)
                return
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            schedule_call(refresh)

    def _open_lazy_first(self, key: str) -> None:
        """Switch to a loader shell now; build the real page on next tick.

        Used for every heavy first-open (Separate Expenses, Verify, Master,
        Daily Register, manage pages, …) so sidebar clicks never sit on the
        previous page with no feedback.
        """
        from tahmeed.ui.widgets.loading_overlay import show_widget_loading

        if key in self._pages:
            widget = self._activate_page(key)
            self._after_lazy_activated(key, widget)
            return

        # First open — toast + Opening… shell while __init__ is deferred.
        AccountantDashboard._toast_opening(self, key)

        # Re-show the existing Opening… shell if this page is already building.
        opening = getattr(self, "_lazy_opening", None)
        if opening is None:
            # Keep legacy attr name in sync for any external peekers/tests.
            self._lazy_opening = {}
            self._separate_expense_opening = self._lazy_opening
            opening = self._lazy_opening
        pending = opening.get(key)
        if pending is not None:
            idx = self._stack.indexOf(pending)
            if idx >= 0:
                self._stack.setCurrentIndex(idx)
                pending.repaint()
                return
            opening.pop(key, None)

        host = QWidget()
        # separateExpenseOpening kept for Separate Expenses tests; others share
        # the same instant-shell behavior under lazyPageOpening.
        host.setObjectName(
            "separateExpenseOpening"
            if key in _SEPARATE_EXPENSE_KEYS
            else "lazyPageOpening"
        )
        host.setStyleSheet(f"background:{_APP_BG};")
        overlay = show_widget_loading(host, "Opening…")
        opening[key] = host
        opening_idx = self._stack.addWidget(host)
        self._stack.setCurrentIndex(opening_idx)
        # Paint the shell before returning from the click handler so the user
        # sees Opening… immediately (build still waits for the next tick).
        try:
            host.repaint()
            if overlay is not None:
                overlay.repaint()
            self._stack.repaint()
        except RuntimeError:
            pass

        def _build_and_swap(k: str = key, placeholder: QWidget = host) -> None:
            try:
                if k not in self._pages:
                    widget = self._mount_page(k, self._create_page(k))
                    # Replace Opening… in-place. addWidget+removeWidget left a hole and
                    # shifted every later stack index, so going back showed the wrong page.
                    ph_idx = self._stack.indexOf(placeholder)
                    if ph_idx >= 0:
                        self._stack.insertWidget(ph_idx, widget)
                        self._page_indices[k] = ph_idx
                    else:
                        self._page_indices[k] = self._stack.addWidget(widget)
                    self._pages[k] = widget
                else:
                    widget = self._pages[k]
                # Stale first-open must not steal the stack after the user left.
                if getattr(self, "_nav_key", None) == k:
                    self._activate_page(k)
                    self._after_lazy_activated(k, widget)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Unable to open page",
                    f"Failed to open this page:\n{exc}",
                )
            finally:
                opening.pop(k, None)
                try:
                    self._stack.removeWidget(placeholder)
                    placeholder.deleteLater()
                except RuntimeError:
                    pass
                # Keep cached indices honest if anything else shifted.
                for pk, pw in self._pages.items():
                    ix = self._stack.indexOf(pw)
                    if ix >= 0:
                        self._page_indices[pk] = ix

        # Always defer one tick so the Opening… shell paints before __init__.
        QTimer.singleShot(0, _build_and_swap)

    def _open_separate_expense_first(self, key: str) -> None:
        """Compat alias for tests — same instant-shell path as all lazy tabs."""
        AccountantDashboard._open_lazy_first(self, key)

    def _create_page(self, key: str) -> QWidget:
        # Imports are deferred so login does not pay for every heavy module.
        if key == "truck_overview":
            from tahmeed.ui.accountant.truck_overview import TruckOverviewWidget
            return TruckOverviewWidget()

        if key == "verify":
            from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget
            widget = VerifyInboxWidget(user=self._user)
            widget.badge_updated.connect(self._on_badge_updated)
            widget.verify_delta.connect(self._on_verify_delta)
            widget.open_day_issues.connect(self._on_open_day_issues)
            return widget

        if key == "drafts":
            from tahmeed.ui.cashier.drafts_view import DraftsView
            widget = DraftsView(user=self._user, show_all_cashiers=True)
            widget.open_register_date.connect(self._on_drafts_open_register)
            widget.drafts_submitted.connect(self._note_submitted_drafts)
            widget.drafts_changed.connect(self._on_drafts_data_changed)
            return widget

        if key == "saved":
            from tahmeed.ui.cashier.saved_view import SavedView
            widget = SavedView(user=self._user, show_all_cashiers=True)
            widget.open_merged_date.connect(self._on_saved_open_merged)
            widget.drafts_submitted.connect(self._note_submitted_drafts)
            widget.drafts_changed.connect(self._on_drafts_data_changed)
            return widget

        if key == "day_issues":
            from tahmeed.ui.cashier.day_issues import DayIssuesWidget
            widget = DayIssuesWidget(
                user=self._user,
                row_source=self._day_issues_row_source,
                apply_live=self._day_issues_apply_live,
                local_days=self._day_issues_local_days,
            )
            widget.open_register.connect(self._on_day_issues_open_register)
            widget.issues_changed.connect(self._on_day_issues_badge)
            widget.mappings_applied.connect(self._on_day_issues_mapped)
            widget.auto_fill_maps_changed.connect(self._on_auto_fill_maps_changed)
            return widget

        if key == "master_expenses":
            from tahmeed.ui.accountant.master_expenses import MasterExpensesWidget
            return MasterExpensesWidget(user=self._user)

        if key == "trash":
            from tahmeed.ui.accountant.trash import TrashWidget
            return TrashWidget(user=self._user)

        if key == "import_daily":
            from tahmeed.ui.accountant.import_daily_master import ImportDailyMasterWidget
            widget = ImportDailyMasterWidget(user=self._user)
            widget.open_master_dates.connect(self._on_open_master_dates)
            return widget

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

        if key == "fuel_overview":
            from tahmeed.ui.accountant.fuel_overview import FuelOverviewWidget
            return FuelOverviewWidget()

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

        if key == "feeds_trash":
            from tahmeed.ui.accountant.feeds_trash import FeedsTrashWidget
            return FeedsTrashWidget()

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

        if key == "manage_suppliers":
            from tahmeed.ui.accountant.manage_suppliers import ManageSuppliersWidget
            widget = ManageSuppliersWidget()
            widget.suppliers_changed.connect(
                lambda: asyncio.ensure_future(self._load_categories())
            )
            return widget

        if key == "manage_description_maps":
            from tahmeed.ui.accountant.manage_description_maps import DescriptionMapsWidget
            return DescriptionMapsWidget()

        if key == "manage_people":
            from tahmeed.ui.accountant.manage_people import PeopleRegistryWidget
            return PeopleRegistryWidget()

        if key == "manage_payees":
            from tahmeed.ui.accountant.manage_payees import ManagePayeesWidget
            return ManagePayeesWidget()

        if key == "manage_users":
            from tahmeed.ui.admin.users_tab import UsersTab
            return UsersTab()

        if key == "receipt_disk":
            from tahmeed.ui.widgets.receipt_storage_panel import ReceiptStoragePanel
            return ReceiptStoragePanel()

        if key == "receipts":
            # Same desk as cashier (Requests / Chase / Inbox / Link / Library),
            # plus accountant Evidence pack for TRA/KRA handoff.
            from tahmeed.ui.widgets.receipt_chase_panel import ReceiptChasePanel
            return ReceiptChasePanel(can_export=True)

        if key == "receipt_browse":
            # Folder/bucket explorer + pack/download (not the chase desk).
            from tahmeed.ui.widgets.receipt_browser_panel import ReceiptBrowserPanel
            return ReceiptBrowserPanel()

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
            if self._pending_payees is not None:
                self._register.update_payees(self._pending_payees)
                self._pending_payees = None
            self._register.rows_saved.connect(
                lambda _: asyncio.ensure_future(self._refresh_drafts_badge())
            )
            self._register.drafts_submitted.connect(self._note_submitted_drafts)
            self._register.drafts_changed.connect(self._on_register_drafts_changed)
            self._register.rows_saved.connect(lambda _: self._refresh_saved_page())
            self._register.drafts_changed.connect(self._refresh_saved_page)
            self._register.day_issues_requested.connect(self._on_open_day_issues)
            self._register.rows_saved.connect(
                lambda _: self._on_day_issues_data_changed()
            )
            self._register.drafts_changed.connect(self._on_day_issues_data_changed)
            self._register.day_issues_source_changed.connect(
                self._on_day_issues_source_changed
            )
            page = _TablePage(self._register)
            # Match current window chrome (labels only when maximized).
            win = self.window()
            if win is not None and hasattr(page, "set_chrome_maximized"):
                page.set_chrome_maximized(win.isMaximized())
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

    def sync_chrome_maximized(self, maximized: bool) -> None:
        """Keep title-bar restore/maximize icon in sync with the host window."""
        self._title_bar.set_maximized(maximized)
        table_page = self._pages.get("table")
        sync_tb = getattr(table_page, "set_chrome_maximized", None)
        if callable(sync_tb):
            sync_tb(maximized)

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

    def _on_verify_delta(self, delta: int) -> None:
        current = self._sidebar.verify_badge() if hasattr(self._sidebar, "verify_badge") else 0
        self._sidebar.set_verify_badge(max(0, current + int(delta or 0)))

    def _note_submitted_drafts(self, updated: int) -> None:
        """Submit already returned the count — update badges without waiting on poll."""
        n = int(updated or 0)
        self._skip_draft_recount = True
        current = self._sidebar.drafts_badge() if hasattr(self._sidebar, "drafts_badge") else 0
        nxt = max(0, current - n)
        self._sidebar.set_drafts_badge(nxt)
        if hasattr(self._sidebar, "set_saved_badge"):
            self._sidebar.set_saved_badge(nxt)
        # Optimistic Verify bump so the sidebar moves even if /wait is down.
        # Submit runs inside pause_background_polls; a force GET can briefly
        # still see the pre-submit counter — don't let that wipe this bump.
        if n > 0 and hasattr(self._sidebar, "set_verify_badge"):
            verify = self._sidebar.verify_badge() if hasattr(self._sidebar, "verify_badge") else 0
            self._sidebar.set_verify_badge(verify + n)
            self._protect_verify_badge_until = time.monotonic() + 8.0
        # After the submit pause resumes (~next tick), pull stored counters.
        QTimer.singleShot(200, self._kick_notification_refresh)

    def _on_register_drafts_changed(self) -> None:
        if getattr(self, "_skip_draft_recount", False):
            self._skip_draft_recount = False
            return
        asyncio.ensure_future(self._refresh_drafts_badge())

    async def _refresh_drafts_badge(self) -> None:
        from tahmeed.ui.cashier.drafts_view import fetch_draft_badge_count
        try:
            count = await fetch_draft_badge_count(self._user, all_cashiers=True)
        except Exception:
            count = 0
        self._sidebar.set_drafts_badge(count)
        if hasattr(self._sidebar, "set_saved_badge"):
            self._sidebar.set_saved_badge(count)

    def _refresh_saved_page(self) -> None:
        widget = self._pages.get("saved")
        if widget is not None and hasattr(widget, "refresh"):
            widget.refresh()

    def _on_drafts_data_changed(self) -> None:
        """Keep Drafts + Saved + badges aligned after discard/submit."""
        if getattr(self, "_skip_draft_recount", False):
            self._skip_draft_recount = False
        else:
            asyncio.ensure_future(self._refresh_drafts_badge())
        drafts = self._pages.get("drafts")
        saved = self._pages.get("saved")
        current = self._stack.currentWidget()
        if drafts is not None and current is not drafts and hasattr(drafts, "refresh"):
            drafts.refresh()
        if saved is not None and current is not saved and hasattr(saved, "refresh"):
            saved.refresh()

    def _on_day_issues_badge(self, count: int) -> None:
        if hasattr(self._sidebar, "set_day_issues_badge"):
            self._sidebar.set_day_issues_badge(count)

    def _day_issues_row_source(self, target):
        if self._register is None:
            return None, None
        return self._register.day_issues_row_source(target)

    def _day_issues_local_days(self) -> list:
        if self._register is None:
            return []
        return self._register.day_issues_local_days()

    def _day_issues_apply_live(self, target, descriptions, **kwargs) -> int:
        if self._register is None:
            return 0
        return self._register.apply_description_mapping_to_open_rows(
            target, descriptions, **kwargs
        )

    def _day_issues_page_visible(self) -> bool:
        widget = self._pages.get("day_issues")
        return widget is not None and self._stack.currentWidget() is widget

    def _schedule_day_issues_badge_refresh(self) -> None:
        self._day_issues_badge_timer.start()

    def _run_debounced_day_issues_badge(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._refresh_day_issues_badge())

    def _on_day_issues_data_changed(self) -> None:
        widget = self._pages.get("day_issues")
        visible = self._day_issues_page_visible()
        if widget is not None:
            if visible:
                widget.on_register_source_changed(visible=True)
            else:
                # Drop only the open register day. mark_dirty() with no days
                # wipes the All-days cache and the next visit rescans every day.
                from datetime import date as _date

                day = _date.today()
                if self._register is not None and getattr(
                    self._register, "_current_date", None
                ):
                    day = self._register._current_date
                widget.mark_dirty(days=[day])
                self._schedule_day_issues_badge_refresh()
        else:
            self._schedule_day_issues_badge_refresh()

    async def _refresh_day_issues_badge(self) -> None:
        from datetime import date as _date
        from tahmeed.services.day_issues_service import count_day_issues

        # When Day Issues is open, badge comes from issues_changed — skip duplicate collect.
        if self._day_issues_page_visible():
            return
        target = _date.today()
        if self._register is not None and getattr(self._register, "_current_date", None):
            target = self._register._current_date
        try:
            replace_txs, extra_txs = self._day_issues_row_source(target)
            # Accountant badge: all cashiers (cashier_id=None).
            count = await count_day_issues(
                target,
                transactions=replace_txs,
                extra_transactions=extra_txs,
                cashier_id=None,
            )
        except Exception:
            count = 0
        self._on_day_issues_badge(count)

    def _on_day_issues_source_changed(self) -> None:
        widget = self._pages.get("day_issues")
        visible = self._day_issues_page_visible()
        if widget is not None:
            widget.on_register_source_changed(visible=visible)
        if not visible:
            self._schedule_day_issues_badge_refresh()

    def _on_open_day_issues(self, target=None) -> None:
        from datetime import date as _date
        from tahmeed.ui.async_utils import in_running_task, schedule_call
        from tahmeed.ui.cashier.day_issues import DayIssuesNavTarget

        self._sidebar.select("day_issues")

        def _go() -> None:
            widget = self._ensure_page("day_issues")
            self._stack.setCurrentIndex(self._page_indices["day_issues"])
            nav = target if isinstance(target, DayIssuesNavTarget) else None
            day = nav.date if nav is not None else target
            if day is None:
                day = _date.today()
                if self._register is not None and getattr(
                    self._register, "_current_date", None
                ):
                    day = self._register._current_date
            if nav is not None and hasattr(widget, "open_from_verify"):
                widget.open_from_verify(
                    day,
                    description_keys=nav.description_keys,
                    all_days=nav.all_days,
                    days=nav.days,
                )
            elif hasattr(widget, "open_for_date"):
                widget.open_for_date(day)
            elif hasattr(widget, "set_date"):
                widget.set_date(day)

        if in_running_task() and "day_issues" not in self._pages:
            schedule_call(_go)
        else:
            _go()

    def _on_open_master_dates(self, start, end) -> None:
        from tahmeed.ui.async_utils import in_running_task, schedule_call

        self._sidebar.select("master_expenses")
        self._nav_key = "master_expenses"

        def _go() -> None:
            widget = self._ensure_page("master_expenses")
            self._stack.setCurrentIndex(self._page_indices["master_expenses"])
            widget.show_dates(start, end)

        if in_running_task() and "master_expenses" not in self._pages:
            schedule_call(_go)
        else:
            _go()

    def _on_day_issues_open_register(self, d) -> None:
        self._on_drafts_open_register(d)

    def _on_day_issues_mapped(self) -> None:
        # issues_changed from the widget already updates the badge.
        pass

    def _on_auto_fill_maps_changed(self, on: bool) -> None:
        if self._register is not None and hasattr(
            self._register, "set_auto_fill_from_maps"
        ):
            self._register.set_auto_fill_from_maps(on)

    def _on_drafts_open_register(self, d) -> None:
        from tahmeed.ui.async_utils import in_running_task, schedule_call

        self._sidebar.select("table")

        def _go() -> None:
            self._ensure_page("table")
            self._stack.setCurrentIndex(self._page_indices["table"])
            assert self._register is not None
            self._register.reload_settings()
            # Accountant Drafts open Merged; parked still land in day cache / badge.
            self._register.navigate_to_date(
                d, merged=True, announce_parked=True
            )

        if in_running_task() and "table" not in self._pages:
            schedule_call(_go)
        else:
            _go()

    def _on_saved_open_merged(self, d) -> None:
        """Saved row click → Table Merged for that day (all cashiers)."""
        self._on_drafts_open_register(d)

    def _on_overview_nav(self, key: str) -> None:
        self._sidebar.select(key)
        self._on_nav(key)

    def pause_notification_polling(self) -> None:
        """Stop badge stream and connectivity polls during nested Qt dialogs.

        Soft-stop only: clear the live flag and leave the long-poll Task alone.
        Cancelling it (even deferred) while Save/Import runs nested Qt loops
        makes Python 3.14 + qasync raise ``task_wakeup`` / CancelScope errors.
        The loop exits itself after the current wait returns.
        """
        self._notification_live = False
        self._notification_timer.stop()
        self._notification_poll_in_flight = False
        from tahmeed.services.connectivity_service import connectivity_monitor

        connectivity_monitor.pause()

    def resume_notification_polling(self) -> None:
        self._notification_live = True
        if not self._notification_timer.isActive():
            self._notification_timer.start()
        self._start_notification_stream()
        # Pull once after dialogs so submit/edit badge bumps are confirmed.
        QTimer.singleShot(0, self._kick_notification_refresh)
        from tahmeed.services.connectivity_service import connectivity_monitor

        connectivity_monitor.resume()

    def _kick_notification_refresh(self) -> None:
        """Drop cached ETag and pull fresh counts (used by watchdog / resume)."""
        from tahmeed.ui.async_utils import schedule_coro

        schedule_coro(self._refresh_notification_counts_now())

    def _ensure_notification_stream(self) -> None:
        """Watchdog: restart the long-poll loop if it died."""
        if not getattr(self, "_notification_live", False):
            return
        prev = getattr(self, "_notification_poll_task", None)
        if prev is not None and not prev.done():
            return
        self._start_notification_stream()

    def _start_notification_stream(self) -> None:
        if not getattr(self, "_notification_live", False):
            return
        from tahmeed.ui.async_utils import create_task, in_running_task

        if in_running_task():
            QTimer.singleShot(0, self._ensure_notification_stream)
            return
        prev = getattr(self, "_notification_poll_task", None)
        if prev is not None and not prev.done():
            return
        loop = getattr(self, "_notification_stream_loop", None)
        if loop is None:
            loop = self._notification_stream_loop
        self._notification_poll_task = create_task(loop())

    def _apply_notification_counts(self, payload: dict) -> None:
        incoming = int(payload.get("verify") or 0)
        current = self._sidebar.verify_badge() if hasattr(self._sidebar, "verify_badge") else 0
        protect_until = float(getattr(self, "_protect_verify_badge_until", 0.0) or 0.0)
        if (
            incoming < current
            and protect_until
            and time.monotonic() < protect_until
        ):
            # Keep the post-submit bump until the stored counter catches up.
            incoming = current
        else:
            self._protect_verify_badge_until = 0.0
        self._sidebar.set_verify_badge(incoming)
        if "drafts" in payload:
            count = int(payload.get("drafts") or 0)
            self._sidebar.set_drafts_badge(count)
            if hasattr(self._sidebar, "set_saved_badge"):
                self._sidebar.set_saved_badge(count)
        page = self._pages.get("verify")
        apply = getattr(page, "apply_stored_counts", None) if page is not None else None
        if callable(apply):
            patched = dict(payload)
            patched["verify"] = incoming
            apply(patched)

    async def _refresh_notification_counts_now(self) -> None:
        """Force a GET of stored badges, then ensure the long-poll loop is alive."""
        from tahmeed.services.notification_service import (
            get_notification_counts,
            reset_verify_notification_cache,
        )

        reset_verify_notification_cache()
        try:
            payload = await get_notification_counts(force=True)
            if payload is not None:
                self._apply_notification_counts(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        if self._notification_live:
            self._ensure_notification_stream()

    async def _notification_stream_loop(self) -> None:
        """Near-realtime badges via /notification-counts/wait (GET fallback)."""
        from tahmeed.services.notification_service import (
            get_notification_counts,
            wait_notification_counts,
        )

        try:
            snap = await get_notification_counts(force=True)
            if snap is not None and self._notification_live:
                self._apply_notification_counts(snap)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        while self._notification_live:
            try:
                # Short waits so soft-pause (_notification_live=False) is noticed
                # quickly without cancelling the Task mid-flight.
                payload = await wait_notification_counts(
                    timeout=min(float(_NOTIFICATION_WAIT_S), 5.0)
                )
                if not self._notification_live:
                    break
                self._apply_notification_counts(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Wait failed (network / older API) — soft-poll once, then retry.
                try:
                    snap = await get_notification_counts(force=True)
                    if snap is not None and self._notification_live:
                        self._apply_notification_counts(snap)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await asyncio.sleep(2.0)

    async def _poll_notification_counts_async(self) -> None:
        """Compatibility wrapper for tests / one-shot refresh."""
        if self._notification_poll_in_flight:
            return
        self._notification_poll_in_flight = True
        try:
            from tahmeed.services.notification_service import get_notification_counts

            payload = await get_notification_counts()
            if payload is None:
                return
            self._apply_notification_counts(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._notification_poll_in_flight = False

    def _on_nav(self, key: str) -> None:
        from tahmeed.ui.async_utils import schedule_call

        previous = getattr(self, "_nav_key", None)
        self._nav_key = key
        if (
            key == "table"
            and previous in _CATALOG_EDITORS
            and self._register is not None
        ):
            self._register.note_catalog_return()

        if key == "overview":
            self._toast_loading("overview")
            self._stack.setCurrentIndex(self._page_indices["overview"])
            refresh = getattr(self._overview, "refresh", None)
            if callable(refresh):
                schedule_call(refresh)
            return

        if key in _SHELL_FIRST_KEYS:
            # Instant Opening… shell on first visit; cached pages activate now.
            self._open_lazy_first(key)
            return

        if self._sidebar.item_def(key) is not None:
            self._toast_opening(key)
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
            cats = await get_payment_target_categories()
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
        try:
            from tahmeed.services.payee_service import get_payee_names
            payees = await get_payee_names()
            if self._register is not None:
                self._register.update_payees(payees)
            else:
                self._pending_payees = payees
        except Exception:
            pass

    def _drop_stale_receipt_subtables(self) -> None:
        """Remove mistaken category shells cached before Chase/Storage routing."""
        stale = [k for k in self._subtable_indices if k.startswith("receipts::")]
        for cache_key in stale:
            idx = self._subtable_indices.pop(cache_key, None)
            if idx is None:
                continue
            w = self._stack.widget(idx)
            if w is not None:
                self._stack.removeWidget(w)
                w.deleteLater()

    def _open_dynamic_first(
        self,
        cache_key: str,
        index_map: dict,
        factory,
        *,
        nav_token: str,
    ) -> None:
        """Instant Opening… shell for first-open category / recon / sub-table."""
        from tahmeed.ui.async_utils import schedule_call
        from tahmeed.ui.widgets.loading_overlay import show_widget_loading

        if cache_key in index_map:
            idx = index_map[cache_key]
            self._stack.setCurrentIndex(idx)
            AccountantDashboard._toast_loading(self, nav_token)
            w = self._stack.widget(idx)
            if w is not None and callable(getattr(w, "refresh", None)):
                schedule_call(w.refresh)
            return

        AccountantDashboard._toast_opening(self, nav_token)

        opening = getattr(self, "_lazy_opening", None)
        if opening is None:
            self._lazy_opening = {}
            self._separate_expense_opening = self._lazy_opening
            opening = self._lazy_opening
        pending = opening.get(cache_key)
        if pending is not None:
            idx = self._stack.indexOf(pending)
            if idx >= 0:
                self._stack.setCurrentIndex(idx)
                pending.repaint()
                return
            opening.pop(cache_key, None)

        host = QWidget()
        host.setObjectName("lazyPageOpening")
        host.setStyleSheet(f"background:{_APP_BG};")
        overlay = show_widget_loading(host, "Opening…")
        opening[cache_key] = host
        opening_idx = self._stack.addWidget(host)
        self._stack.setCurrentIndex(opening_idx)
        try:
            host.repaint()
            if overlay is not None:
                overlay.repaint()
            self._stack.repaint()
        except RuntimeError:
            pass

        def _build_and_swap(
            ck: str = cache_key,
            placeholder: QWidget = host,
            token: str = nav_token,
        ) -> None:
            try:
                if ck not in index_map:
                    widget = factory()
                    ph_idx = self._stack.indexOf(placeholder)
                    if ph_idx >= 0:
                        self._stack.insertWidget(ph_idx, widget)
                    else:
                        self._stack.addWidget(widget)
                    index_map[ck] = self._stack.indexOf(widget)
                else:
                    widget = self._stack.widget(index_map[ck])
                    if widget is None:
                        return
                # Stale build must not override a newer sidebar click.
                if getattr(self, "_nav_key", None) == token:
                    self._stack.setCurrentWidget(widget)
                    index_map[ck] = self._stack.indexOf(widget)
                    AccountantDashboard._toast_loading(self, token)
                    if callable(getattr(widget, "refresh", None)):
                        schedule_call(widget.refresh)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Unable to open page",
                    f"Failed to open this page:\n{exc}",
                )
            finally:
                opening.pop(ck, None)
                try:
                    self._stack.removeWidget(placeholder)
                    placeholder.deleteLater()
                except RuntimeError:
                    pass

        QTimer.singleShot(0, _build_and_swap)

    def _show_category(self, key: str) -> None:
        """Lazily create (and cache) the item table for this dynamic sidebar key."""
        if key not in self._category_indices:
            d = self._sidebar.item_def(key)
            if d is None:
                self._stack.setCurrentIndex(self._placeholder_index)
                return
            title, icon, label = d

            def _factory(
                k: str = key,
                cat: str = title,
                ic: str = icon,
                lab: str = label,
            ) -> QWidget:
                from tahmeed.ui.accountant.category_tables import CategoryTableWidget

                return self._mount_page(
                    k,
                    CategoryTableWidget(
                        category_name=cat, title=lab, icon_name=ic
                    ),
                )

            self._open_dynamic_first(
                key, self._category_indices, _factory, nav_token=key
            )
            return
        idx = self._category_indices[key]
        self._stack.setCurrentIndex(idx)
        self._toast_loading(key)
        from tahmeed.ui.async_utils import schedule_call
        schedule_call(self._stack.widget(idx).refresh)

    def _show_recon(self, match: str) -> None:
        """Lazily create (and cache) an SM Burhani reconciliation view."""
        if match not in self._recon_indices:
            token = f"sm_burhani::{match}"

            def _factory(m: str = match) -> QWidget:
                from tahmeed.ui.accountant.reconciliation import (
                    RPAScheduleWidget, BondsWidget,
                )
                raw = BondsWidget() if m == "bonds" else RPAScheduleWidget()
                return self._mount_page(f"sm_burhani::{m}", raw)

            self._nav_key = token
            self._open_dynamic_first(
                match, self._recon_indices, _factory, nav_token=token
            )
            return
        idx = self._recon_indices[match]
        self._stack.setCurrentIndex(idx)
        self._toast_loading(f"sm_burhani::{match}")
        from tahmeed.ui.async_utils import schedule_call
        schedule_call(self._stack.widget(idx).refresh)

    def _show_subtable(self, parent_key: str, parent_category: str,
                       name: str, match: str) -> None:
        """Lazily create (and cache) a sub-table view for a parent category."""
        if parent_key == "sm_burhani":
            self._show_recon(match)
            return
        # Legacy Receipts→Chase/Storage children (removed from sidebar) → real pages.
        if parent_key == "receipts":
            route = (match or name or "").strip().lower()
            self._drop_stale_receipt_subtables()
            if route in ("receipt_browse", "storage"):
                self._on_nav("receipt_browse")
                return
            self._on_nav("receipts")
            return
        cache_key = f"{parent_key}::{name}"
        if cache_key not in self._subtable_indices:
            d = self._sidebar.item_def(parent_key)
            icon = d[1] if d else "mdi.tag-outline"
            parent_label = d[2] if d else parent_category

            def _factory(
                ck: str = cache_key,
                cat: str = parent_category,
                lab: str = f"{parent_label} · {name}",
                ic: str = icon,
                desc: str = match or name,
            ) -> QWidget:
                from tahmeed.ui.accountant.category_tables import CategoryTableWidget

                return self._mount_page(
                    ck,
                    CategoryTableWidget(
                        category_name=cat,
                        title=lab,
                        icon_name=ic,
                        description_filter=desc,
                    ),
                )

            self._nav_key = cache_key
            self._open_dynamic_first(
                cache_key, self._subtable_indices, _factory, nav_token=cache_key
            )
            return
        idx = self._subtable_indices[cache_key]
        self._stack.setCurrentIndex(idx)
        self._toast_loading(cache_key)
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

