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
)

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
        }
        if key in _routes:
            idx, widget = _routes[key]
            self._stack.setCurrentIndex(idx)
            widget.refresh()
        else:
            self._stack.setCurrentIndex(12)


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
