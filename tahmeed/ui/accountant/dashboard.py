"""AccountantDashboard — Main shell: header + sidebar + content stack + status bar."""

from __future__ import annotations
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QFrame,
)
from PySide6.QtCore import Qt

from tahmeed.models.user import User
from tahmeed.ui.accountant.header_bar import HeaderBar
from tahmeed.ui.accountant.sidebar import SidebarWidget
from tahmeed.ui.accountant.overview import OverviewWidget
from tahmeed.ui.accountant.verify_inbox import VerifyInboxWidget
from tahmeed.ui.accountant.master_expenses import MasterExpensesWidget
from tahmeed.ui.accountant.separate_expenses import (
    TollPlazaWidget,
    ParkingCongoWidget,
    CongoExpensesWidget,
    AhmedKimviWidget,
    ZambiaParkingWidget,
    HarrisonExpensesWidget,
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
from tahmeed.ui.accountant.fleet_registry import TrucksRegistryWidget, TrailersRegistryWidget
from tahmeed.ui.accountant.manage_items import ManageItemsWidget

_APP_BG = "#F4F6F8"


class AccountantDashboard(QWidget):
    def __init__(self, user: User, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._user = user
        self._build()

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
        )
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

        self._verify_inbox = VerifyInboxWidget(user=self._user)
        self._verify_inbox.badge_updated.connect(self._on_badge_updated)
        self._stack.addWidget(self._verify_inbox)     # index 1 — Verify Inbox

        self._master_expenses = MasterExpensesWidget()
        self._stack.addWidget(self._master_expenses)  # index 2 — Master Expenses

        # ── Separate Expenses ─────────────────────────────────────────────────
        self._toll_plaza       = TollPlazaWidget()
        self._stack.addWidget(self._toll_plaza)        # index 3

        self._parking_congo    = ParkingCongoWidget()
        self._stack.addWidget(self._parking_congo)     # index 4

        self._congo_exp        = CongoExpensesWidget()
        self._stack.addWidget(self._congo_exp)         # index 5

        self._ahmed_kimvi      = AhmedKimviWidget()
        self._stack.addWidget(self._ahmed_kimvi)       # index 6

        self._zambia_parking   = ZambiaParkingWidget()
        self._stack.addWidget(self._zambia_parking)    # index 7

        self._harrison         = HarrisonExpensesWidget()
        self._stack.addWidget(self._harrison)          # index 8

        self._afritrack        = AfritrackWidget()
        self._stack.addWidget(self._afritrack)         # index 9

        self._third_party      = ThirdPartyWidget()
        self._stack.addWidget(self._third_party)       # index 10

        self._comesa           = ComesaWidget()
        self._stack.addWidget(self._comesa)            # index 11

        self._stack.addWidget(_PlaceholderPage())      # index 12 — other sections

        # ── Fuel Consumption ──────────────────────────────────────────────────
        self._infinity      = InfinityWidget()
        self._stack.addWidget(self._infinity)           # index 13

        self._lake_zambia   = LakeZambiaWidget()
        self._stack.addWidget(self._lake_zambia)        # index 14

        self._lake_tunduma  = LakeTundumaWidget()
        self._stack.addWidget(self._lake_tunduma)       # index 15

        self._gbp_diesel    = GBPDieselWidget()
        self._stack.addWidget(self._gbp_diesel)         # index 16

        # ── RahnTech ──────────────────────────────────────────────────────────
        self._rahntech = RahnTechWidget()
        self._stack.addWidget(self._rahntech)           # index 17

        # ── Fleet Registry ────────────────────────────────────────────────────
        self._trucks_registry = TrucksRegistryWidget()
        self._stack.addWidget(self._trucks_registry)    # index 18

        self._trailers_registry = TrailersRegistryWidget()
        self._stack.addWidget(self._trailers_registry)  # index 19

        self._manage_items = ManageItemsWidget()
        self._stack.addWidget(self._manage_items)        # index 20
        # Rebuild the sidebar's dynamic ITEMS whenever the accountant changes them.
        self._manage_items.items_changed.connect(self._sidebar.refresh_items)
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

    def _on_badge_updated(self, count: int) -> None:
        self._sidebar.set_verify_badge(count)

    def _on_nav(self, key: str) -> None:
        _routes = {
            "overview":       (0,  self._overview),
            "verify":         (1,  self._verify_inbox),
            "master_expenses":(2,  self._master_expenses),
            "toll_plaza":     (3,  self._toll_plaza),
            "parking_congo":  (4,  self._parking_congo),
            "congo_exp":      (5,  self._congo_exp),
            "ahmed_kimvi":    (6,  self._ahmed_kimvi),
            "zambia_parking": (7,  self._zambia_parking),
            "harrison":       (8,  self._harrison),
            "afritrack":      (9,  self._afritrack),
            "third_party":    (10, self._third_party),
            "comesa":         (11, self._comesa),
            "infinity":       (13, self._infinity),
            "lake_zambia":    (14, self._lake_zambia),
            "lake_tunduma":   (15, self._lake_tunduma),
            "gbp_diesel":     (16, self._gbp_diesel),
            "rahntech":         (17, self._rahntech),
            "manage_trucks":    (18, self._trucks_registry),
            "manage_trailers":  (19, self._trailers_registry),
            "manage_categories":(20, self._manage_items),
        }
        if key in _routes:
            idx, widget = _routes[key]
            self._stack.setCurrentIndex(idx)
            widget.refresh()
        elif self._sidebar.item_def(key) is not None:
            self._show_category(key)
        elif key == "sm_burhani":
            # Parent click opens the first sub-table; children open via sidebar.
            self._show_recon("rpa_schedule")
        else:
            self._stack.setCurrentIndex(12)

    def _show_category(self, key: str) -> None:
        """Lazily create (and cache) the item table for this dynamic sidebar key."""
        if key not in self._category_indices:
            d = self._sidebar.item_def(key)
            if d is None:
                self._stack.setCurrentIndex(12)
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
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
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
            " font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        hl.addWidget(status)
        hl.addStretch()
