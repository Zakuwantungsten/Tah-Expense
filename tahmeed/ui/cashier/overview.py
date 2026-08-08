"""
CashierOverview — summary dashboard (Overview tab).

Layout (scrollable content):
  ┌─ Welcome banner ─────────────────────────────────────────────┐
  ├─ TODAY  · 4 stat cards ──────────────────────────────────────┤
  ├─ THIS MONTH · 4 stat cards ──────────────────────────────────┤
  ├─ Quick actions ──────────────────────────────────────────────┤
  ├─ Receipt status (left) │ Recent activity (right) ─────────────┤  ← side-by-side when wide
  └─ stacked vertically when narrow ────────────────────────────┘
"""

import asyncio
from datetime import date, datetime

from PySide6.QtCore import Qt, QByteArray, QSize, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QBoxLayout, QFrame, QGraphicsDropShadowEffect, QGridLayout,
    QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

from tahmeed.models.user import User
from tahmeed.services.cashier_service import get_overview_stats

# ---------------------------------------------------------------------------
# Palette — unified with sidebar navy (#1B2B4B)
# ---------------------------------------------------------------------------
_BG        = "#f1f5f9"
_CARD_BG   = "#ffffff"
_BORDER    = "#e2e8f0"
_DARK      = "#0f172a"
_MUTED     = "#64748b"
_SECTION   = "#94a3b8"
_GREEN     = "#22c55e"
_BLUE      = "#0077C5"   # QB blue — matches sidebar/header
_AMBER     = "#f59e0b"
_PURPLE    = "#a78bfa"
_RED       = "#ef4444"
_NAVY      = "#1B2B4B"   # sidebar colour — replaces espresso #1c1917
_NAVY_2    = "#253A5C"   # sidebar active bg

# Light tint pill backgrounds for icon containers
_GREEN_PILL  = "#dcfce7"
_BLUE_PILL   = "#dbeafe"
_AMBER_PILL  = "#fef3c7"
_PURPLE_PILL = "#ede9fe"
_RED_PILL    = "#fee2e2"

_ACCENT_PILLS: dict[str, str] = {
    _GREEN:  _GREEN_PILL,
    _BLUE:   _BLUE_PILL,
    _AMBER:  _AMBER_PILL,
    _PURPLE: _PURPLE_PILL,
    _RED:    _RED_PILL,
}

# ---------------------------------------------------------------------------
# SVG icon set
# ---------------------------------------------------------------------------
_SVG_GRAY = b"#94a3b8"   # placeholder replaced by accent colour at render time

_SVGS: dict[str, bytes] = {
    "list": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<polygon points="12,2 2,7 12,12 22,7"/>'
        b'<polyline points="2,17 12,22 22,17"/>'
        b'<polyline points="2,12 12,17 22,12"/>'
        b'</svg>'
    ),
    "money": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<rect x="2" y="6" width="20" height="12" rx="2"/>'
        b'<circle cx="12" cy="12" r="3"/>'
        b'<line x1="2" y1="10" x2="5" y2="10"/>'
        b'<line x1="2" y1="14" x2="5" y2="14"/>'
        b'<line x1="19" y1="10" x2="22" y2="10"/>'
        b'<line x1="19" y1="14" x2="22" y2="14"/>'
        b'</svg>'
    ),
    "receipt": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round">'
        b'<path d="M4 2l2 2 2-2 2 2 2-2 2 2 2-2v18l-2-2-2 2-2-2-2 2-2-2-2 2V2z"/>'
        b'<line x1="8" y1="9" x2="16" y2="9"/>'
        b'<line x1="8" y1="13" x2="14" y2="13"/>'
        b'</svg>'
    ),
    "calendar": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round">'
        b'<rect x="3" y="4" width="18" height="18" rx="2"/>'
        b'<line x1="3" y1="9" x2="21" y2="9"/>'
        b'<line x1="8" y1="2" x2="8" y2="6"/>'
        b'<line x1="16" y1="2" x2="16" y2="6"/>'
        b'<line x1="8" y1="14" x2="10" y2="14"/>'
        b'<line x1="12" y1="14" x2="14" y2="14"/>'
        b'<line x1="16" y1="14" x2="18" y2="14"/>'
        b'<line x1="8" y1="18" x2="10" y2="18"/>'
        b'<line x1="12" y1="18" x2="14" y2="18"/>'
        b'</svg>'
    ),
    "money_blue": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<rect x="2" y="6" width="20" height="12" rx="2"/>'
        b'<circle cx="12" cy="12" r="3"/>'
        b'<line x1="2" y1="10" x2="5" y2="10"/>'
        b'<line x1="2" y1="14" x2="5" y2="14"/>'
        b'<line x1="19" y1="10" x2="22" y2="10"/>'
        b'<line x1="19" y1="14" x2="22" y2="14"/>'
        b'</svg>'
    ),
    "clock": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round">'
        b'<circle cx="12" cy="12" r="9"/>'
        b'<polyline points="12,6 12,12 16,14"/>'
        b'</svg>'
    ),
    "check": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#22c55e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/>'
        b'<polyline points="22,4 12,14.01 9,11.01"/>'
        b'</svg>'
    ),
    "alert": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#ef4444" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>'
        b'<line x1="12" y1="9" x2="12" y2="13"/>'
        b'<line x1="12" y1="17" x2="12.01" y2="17" stroke-width="2.5"/>'
        b'</svg>'
    ),
    "refresh": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#94a3b8" stroke-width="1.8" stroke-linecap="round">'
        b'<path d="M3 12a9 9 0 109-9 9.75 9.75 0 00-6.74 2.74L3 8"/>'
        b'<path d="M3 3v5h5"/>'
        b'</svg>'
    ),
    "table_go": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        b'<rect x="3" y="3" width="7" height="7" rx="1.5" fill="#E85D04"/>'
        b'<rect x="14" y="3" width="7" height="7" rx="1.5" fill="#E85D04"/>'
        b'<rect x="3" y="14" width="7" height="7" rx="1.5" fill="#E85D04"/>'
        b'<rect x="14" y="14" width="7" height="7" rx="1.5" fill="#E85D04"/>'
        b'</svg>'
    ),
    "form_go": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#E85D04" stroke-width="1.8" stroke-linecap="round">'
        b'<rect x="4" y="2" width="16" height="20" rx="2"/>'
        b'<line x1="7" y1="8" x2="17" y2="8"/>'
        b'<line x1="7" y1="12" x2="17" y2="12"/>'
        b'<line x1="7" y1="16" x2="13" y2="16"/>'
        b'</svg>'
    ),
    "browse_go": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#E85D04" stroke-width="1.8" stroke-linecap="round">'
        b'<circle cx="11" cy="11" r="7"/>'
        b'<line x1="16.5" y1="16.5" x2="21" y2="21" stroke-width="2.2"/>'
        b'</svg>'
    ),
    "export_go": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#E85D04" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M12 3v13"/>'
        b'<polyline points="17,8 12,3 7,8"/>'
        b'<path d="M5 20h14"/>'
        b'</svg>'
    ),
    "import_go": (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"'
        b' stroke="#E85D04" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        b'<path d="M12 16V3"/>'
        b'<polyline points="7,11 12,16 17,11"/>'
        b'<path d="M5 20h14"/>'
        b'</svg>'
    ),
}


def _icon(key: str, color: str = "#94a3b8", size: int = 20) -> QIcon:
    raw = _SVGS[key]
    if color.lower() != "#94a3b8":
        raw = raw.replace(_SVG_GRAY, color.lower().encode())
    renderer = QSvgRenderer(QByteArray(raw))
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    renderer.render(p)
    p.end()
    return QIcon(px)


def _fmt_tzs(v: float) -> str:
    return f"{v:,.0f}"


def _drop_shadow(widget: QWidget, blur: int = 16, dy: int = 2, alpha: int = 18) -> None:
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(15, 23, 42, alpha))
    widget.setGraphicsEffect(eff)


# ---------------------------------------------------------------------------
# Stat card  — white card, coloured icon pill, bold value
# ---------------------------------------------------------------------------

class _StatCard(QFrame):
    def __init__(
        self,
        icon_key: str,
        value: str,
        label: str,
        accent: str,
        subtitle: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(f"""
            QFrame#StatCard {{
                background: {_CARD_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        self.setMinimumHeight(108)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        _drop_shadow(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)

        # icon pill + label row
        top = QHBoxLayout()
        top.setSpacing(10)
        top.setContentsMargins(0, 0, 0, 0)

        pill_bg = _ACCENT_PILLS.get(accent, _BLUE_PILL)
        icon_pill = QLabel()
        icon_pill.setFixedSize(36, 36)
        icon_pill.setAlignment(Qt.AlignCenter)
        icon_pill.setStyleSheet(f"background: {pill_bg}; border-radius: 8px;")
        icon_pill.setPixmap(_icon(icon_key, color=accent, size=20).pixmap(QSize(20, 20)))

        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 11px; font-weight: 500; background: transparent;"
        )

        top.addWidget(icon_pill)
        top.addWidget(lbl)
        top.addStretch()

        self._val_lbl = QLabel(value)
        self._val_lbl.setStyleSheet(
            f"color: {_DARK}; font-size: 22px; font-weight: 700;"
            f" background: transparent; letter-spacing: -0.5px;"
        )

        lay.addLayout(top)
        lay.addWidget(self._val_lbl)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(
                f"color: {_MUTED}; font-size: 10px; font-weight: 500; background: transparent;"
            )
            lay.addWidget(sub)
        else:
            lay.addStretch()

    def set_value(self, v: str) -> None:
        self._val_lbl.setText(v)


# ---------------------------------------------------------------------------
# Section header
# ---------------------------------------------------------------------------

def _section_header(title: str) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 8, 0, 2)
    h.setSpacing(10)

    lbl = QLabel(title.upper())
    lbl.setStyleSheet(
        f"color: {_SECTION}; font-size: 10px; font-weight: 700;"
        f" letter-spacing: 1.2px; background: transparent;"
    )

    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {_BORDER};")
    line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    h.addWidget(lbl)
    h.addWidget(line)
    return w


# ---------------------------------------------------------------------------
# Receipt status bar
# ---------------------------------------------------------------------------

class _ReceiptBar(QWidget):
    def __init__(self, received: int, pending: int, missing: int, parent=None):
        super().__init__(parent)
        self._received = received
        self._pending  = pending
        self._missing  = missing
        self._build()

    def _build(self) -> None:
        lay = self.layout()
        if lay is None:
            lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        total = self._received + self._pending + self._missing or 1

        bar = QWidget()
        bar.setFixedHeight(10)
        bar.setStyleSheet("background: transparent;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(0, 0, 0, 0)
        bar_lay.setSpacing(2)

        def _seg(color: str, count: int) -> QFrame:
            f = QFrame()
            f.setFixedHeight(10)
            ratio = count / total
            f.setMinimumWidth(max(4, int(ratio * 500)))
            f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            f.setStyleSheet(f"background: {color}; border-radius: 5px;")
            return f

        if self._received:
            bar_lay.addWidget(_seg(_GREEN, self._received))
        if self._pending:
            bar_lay.addWidget(_seg(_AMBER, self._pending))
        if self._missing:
            bar_lay.addWidget(_seg(_RED, self._missing))

        lay.addWidget(bar)

        legend = QHBoxLayout()
        legend.setSpacing(20)
        legend.setContentsMargins(0, 0, 0, 0)

        def _dot_label(color: str, text: str) -> QWidget:
            w = QWidget()
            w.setStyleSheet("background: transparent;")
            wl = QHBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(5)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
            wl.addWidget(dot)
            wl.addWidget(lbl)
            return w

        legend.addWidget(_dot_label(_GREEN, f"Received  {self._received}"))
        legend.addWidget(_dot_label(_AMBER, f"Pending  {self._pending}"))
        legend.addWidget(_dot_label(_RED,   f"Missing  {self._missing}"))
        legend.addStretch()
        lay.addLayout(legend)

    def update_data(self, received: int, pending: int, missing: int) -> None:
        self._received = received
        self._pending  = pending
        self._missing  = missing
        old = self.layout()
        while old.count():
            item = old.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._build()


# ---------------------------------------------------------------------------
# Recent activity table
# ---------------------------------------------------------------------------

def _activity_header() -> QWidget:
    w = QWidget()
    # Navy header — matches sidebar exactly
    w.setStyleSheet(
        f"background: {_NAVY};"
        " border-top-left-radius:6px;border-top-right-radius:6px;"
        " border-bottom-left-radius:0;border-bottom-right-radius:0;"
    )
    h = QHBoxLayout(w)
    h.setContentsMargins(16, 10, 16, 10)
    h.setSpacing(0)

    def _col(text: str, flex: int, align=Qt.AlignLeft) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #94a3b8; font-size: 10px; font-weight: 700;"
            " letter-spacing: 0.8px; background: transparent;"
        )
        lbl.setAlignment(align)
        lbl.setSizePolicy(
            QSizePolicy.Expanding if flex else QSizePolicy.Fixed,
            QSizePolicy.Fixed,
        )
        return lbl

    h.addWidget(_col("DATE", 1))
    h.addWidget(_col("ITEM", 2))
    h.addWidget(_col("DESCRIPTION", 3))
    h.addWidget(_col("TRUCK", 1))
    h.addWidget(_col("AMOUNT (TZS)", 1, Qt.AlignRight))
    return w


def _activity_row(tx, even: bool) -> QWidget:
    bg = "#ffffff" if even else "#f8fafc"
    w = QWidget()
    w.setStyleSheet(f"background: {bg};")
    h = QHBoxLayout(w)
    h.setContentsMargins(16, 10, 16, 10)
    h.setSpacing(0)

    date_str = tx.date.strftime("%b %d, %Y") if tx.date else "—"
    item     = (tx.item or "—")[:22]
    desc     = (tx.description or "—")[:38]
    truck    = tx.truck_number or "—"
    amount_str = f"{_fmt_tzs(tx.amount or 0)} TZS"

    def _cell(text: str, flex: int, color: str = _DARK,
               align=Qt.AlignLeft, bold=False) -> QLabel:
        lbl = QLabel(text)
        fw = "600" if bold else "400"
        lbl.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: {fw}; background: transparent;"
        )
        lbl.setAlignment(align)
        lbl.setSizePolicy(
            QSizePolicy.Expanding if flex else QSizePolicy.Fixed,
            QSizePolicy.Fixed,
        )
        return lbl

    h.addWidget(_cell(date_str,    1, _MUTED))
    h.addWidget(_cell(item,        2, _DARK))
    h.addWidget(_cell(desc,        3, _DARK))
    h.addWidget(_cell(truck,       1, _MUTED))
    h.addWidget(_cell(amount_str,  1, _GREEN, Qt.AlignRight, bold=True))
    return w


# ---------------------------------------------------------------------------
# Quick action button style — white card, orange icon, navy hover
# ---------------------------------------------------------------------------

_ACTION_BTN = f"""
QToolButton {{
    background: {_CARD_BG};
    border: 1.5px solid {_BORDER};
    border-radius: 8px;
    padding: 8px 20px;
    color: {_DARK};
    font-size: 12px;
    font-weight: 600;
    min-width: 140px;
}}
QToolButton:hover   {{
    background: {_NAVY};
    color: #ffffff;
    border-color: {_NAVY};
}}
QToolButton:pressed {{
    background: {_NAVY_2};
    color: #ffffff;
    border-color: {_NAVY_2};
}}
"""


# ---------------------------------------------------------------------------
# Responsive side-by-side panel row
# ---------------------------------------------------------------------------

class _PanelRow(QWidget):
    """Places two child panels side-by-side (width ≥ _BREAK) or stacked vertically."""
    _BREAK = 860

    def __init__(self, receipt: QWidget, activity: QWidget, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(20)
        self._lay.addWidget(receipt, 1)
        self._lay.addWidget(activity, 2)
        self._horiz: bool | None = None

    def _apply(self, horiz: bool) -> None:
        if self._horiz == horiz:
            return
        self._horiz = horiz
        if horiz:
            self._lay.setDirection(QBoxLayout.Direction.LeftToRight)
            self._lay.setStretch(0, 1)
            self._lay.setStretch(1, 2)
        else:
            self._lay.setDirection(QBoxLayout.Direction.TopToBottom)
            self._lay.setStretch(0, 0)
            self._lay.setStretch(1, 0)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._apply(ev.size().width() >= self._BREAK)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._apply(self.width() >= self._BREAK)


# ---------------------------------------------------------------------------
# CashierOverview
# ---------------------------------------------------------------------------

class CashierOverview(QWidget):
    go_to_register = Signal()
    go_to_form     = Signal()
    go_to_browse   = Signal()
    export_data    = Signal()
    import_data    = Signal()

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user = user
        self._cards: dict[str, _StatCard] = {}
        self._receipt_bar: _ReceiptBar | None = None
        self._activity_container: QWidget | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setStyleSheet(f"CashierOverview {{ background: {_BG}; }}")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_BG}; border: none; }}
            QScrollBar:vertical {{ background: {_BG}; width: 6px; margin: 0; }}
            QScrollBar::handle:vertical {{
                background: #cbd5e1; border-radius: 3px; min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        content = QWidget()
        content.setStyleSheet(f"background: {_BG};")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(28, 24, 28, 28)
        cl.setSpacing(0)

        # ── Welcome banner ────────────────────────────────────────────
        cl.addWidget(self._build_banner())
        cl.addSpacing(24)

        # ── Today section ─────────────────────────────────────────────
        cl.addWidget(_section_header("Today"))
        cl.addSpacing(10)
        today_grid = QGridLayout()
        today_grid.setSpacing(12)
        today_grid.setContentsMargins(0, 0, 0, 0)

        self._cards["t_count"]   = _StatCard("list",    "—", "Transactions",     _GREEN, "entries today")
        self._cards["t_tzs"]     = _StatCard("money",   "—", "TZS Total",        _GREEN, "today")
        self._cards["t_rcpt_ok"] = _StatCard("check",   "—", "Receipts OK",      _GREEN, "received today")
        self._cards["t_rcpt"]    = _StatCard("receipt", "—", "Pending Receipts", _AMBER, "today")

        today_grid.addWidget(self._cards["t_count"],   0, 0)
        today_grid.addWidget(self._cards["t_tzs"],     0, 1)
        today_grid.addWidget(self._cards["t_rcpt_ok"], 0, 2)
        today_grid.addWidget(self._cards["t_rcpt"],    0, 3)
        cl.addLayout(today_grid)
        cl.addSpacing(20)

        # ── Month section ─────────────────────────────────────────────
        today = date.today()
        cl.addWidget(_section_header(today.strftime("This Month — %B %Y")))
        cl.addSpacing(10)
        month_grid = QGridLayout()
        month_grid.setSpacing(12)
        month_grid.setContentsMargins(0, 0, 0, 0)

        self._cards["m_count"] = _StatCard("calendar",   "—", "Total Entries",    _BLUE,   "this month")
        self._cards["m_tzs"]   = _StatCard("money_blue", "—", "TZS Total",        _BLUE,   "this month")
        self._cards["m_avg"]   = _StatCard("money",      "—", "Avg. Daily (TZS)", _PURPLE, "this month")
        self._cards["m_unver"] = _StatCard("clock",      "—", "Unverified",        _PURPLE, "awaiting review")

        month_grid.addWidget(self._cards["m_count"], 0, 0)
        month_grid.addWidget(self._cards["m_tzs"],   0, 1)
        month_grid.addWidget(self._cards["m_avg"],   0, 2)
        month_grid.addWidget(self._cards["m_unver"], 0, 3)
        cl.addLayout(month_grid)
        cl.addSpacing(20)

        # ── Quick actions ─────────────────────────────────────────────
        cl.addWidget(_section_header("Quick Actions"))
        cl.addSpacing(10)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.setContentsMargins(0, 0, 0, 0)

        def _abtn(label: str, icon_key: str, slot) -> QToolButton:
            b = QToolButton()
            b.setText(f"  {label}")
            b.setIcon(_icon(icon_key, size=16))
            b.setIconSize(QSize(16, 16))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setStyleSheet(_ACTION_BTN)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            return b

        actions.addWidget(_abtn("Today's Register", "table_go",  self.go_to_register.emit))
        actions.addWidget(_abtn("New Entry",         "form_go",   self.go_to_form.emit))
        actions.addWidget(_abtn("Browse All",        "browse_go", self.go_to_browse.emit))
        actions.addWidget(_abtn("Export",            "export_go", self.export_data.emit))
        actions.addWidget(_abtn("Import",            "import_go", self.import_data.emit))
        actions.addStretch()
        cl.addLayout(actions)
        cl.addSpacing(20)

        # ── Receipt status + Recent activity (side-by-side) ──────────
        left_panel = QWidget()
        left_panel.setStyleSheet("background: transparent;")
        lp_lay = QVBoxLayout(left_panel)
        lp_lay.setContentsMargins(0, 0, 0, 0)
        lp_lay.setSpacing(10)
        lp_lay.addWidget(_section_header("Receipt Status  ·  This Month"))

        rcpt_card = QFrame()
        rcpt_card.setObjectName("RcptCard")
        rcpt_card.setStyleSheet(f"""
            QFrame#RcptCard {{
                background: {_CARD_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        _drop_shadow(rcpt_card)
        rcpt_lay = QVBoxLayout(rcpt_card)
        rcpt_lay.setContentsMargins(18, 16, 18, 16)
        self._receipt_bar = _ReceiptBar(0, 0, 0)
        rcpt_lay.addWidget(self._receipt_bar)
        lp_lay.addWidget(rcpt_card)
        lp_lay.addStretch()

        right_panel = QWidget()
        right_panel.setStyleSheet("background: transparent;")
        rp_lay = QVBoxLayout(right_panel)
        rp_lay.setContentsMargins(0, 0, 0, 0)
        rp_lay.setSpacing(10)
        rp_lay.addWidget(_section_header("Recent Activity"))

        activity_card = QFrame()
        activity_card.setObjectName("ActCard")
        activity_card.setStyleSheet(f"""
            QFrame#ActCard {{
                background: {_CARD_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        _drop_shadow(activity_card)
        self._activity_lay = QVBoxLayout(activity_card)
        self._activity_lay.setContentsMargins(0, 0, 0, 0)
        self._activity_lay.setSpacing(0)
        self._activity_lay.addWidget(_activity_header())

        self._activity_rows_container = QWidget()
        self._activity_rows_container.setStyleSheet("background: transparent;")
        self._rows_lay = QVBoxLayout(self._activity_rows_container)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(0)
        self._activity_lay.addWidget(self._activity_rows_container)

        bottom_pad = QFrame()
        bottom_pad.setFrameShape(QFrame.NoFrame)
        bottom_pad.setFixedHeight(8)
        bottom_pad.setStyleSheet(
            f"background: {_CARD_BG};"
            " border-top-left-radius:0;border-top-right-radius:0;"
            " border-bottom-left-radius:12px;border-bottom-right-radius:12px;"
        )
        self._activity_lay.addWidget(bottom_pad)

        rp_lay.addWidget(activity_card)

        cl.addWidget(_PanelRow(left_panel, right_panel))
        cl.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    def _build_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("Banner")
        # Navy gradient — matches sidebar colours exactly
        banner.setStyleSheet(f"""
            QWidget#Banner {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {_NAVY},
                    stop:1 {_NAVY_2}
                );
                border-radius: 12px;
            }}
        """)
        banner.setFixedHeight(82)

        bl = QHBoxLayout(banner)
        bl.setContentsMargins(20, 0, 20, 0)
        bl.setSpacing(14)

        initials = "".join(p[0].upper() for p in self._user.full_name.split()[:2]) or "U"
        avatar = QLabel(initials)
        avatar.setFixedSize(46, 46)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            "background: rgba(0,119,197,0.25); border-radius: 23px;"
            " color: #7DD3FC; font-size: 16px; font-weight: 700;"
        )

        text_w = QWidget()
        text_w.setStyleSheet("background: transparent;")
        tl = QVBoxLayout(text_w)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(3)

        hour = datetime.now().hour
        greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")
        greet_lbl = QLabel(f"{greeting}, {self._user.full_name.split()[0]}")
        greet_lbl.setStyleSheet(
            "color: #f1f5f9; font-size: 15px; font-weight: 700; background: transparent;"
        )
        sub_lbl = QLabel("Here's your activity summary for today")
        sub_lbl.setStyleSheet(
            "color: #93C5FD; font-size: 11px; background: transparent;"
        )
        tl.addWidget(greet_lbl)
        tl.addWidget(sub_lbl)

        bl.addWidget(avatar)
        bl.addWidget(text_w)
        bl.addStretch()

        date_badge = QWidget()
        date_badge.setStyleSheet(
            "background: rgba(255,255,255,0.10); border-radius: 8px;"
        )
        dbl = QVBoxLayout(date_badge)
        dbl.setContentsMargins(16, 10, 16, 10)
        dbl.setSpacing(1)
        today = date.today()
        day_lbl = QLabel(today.strftime("%B %d"))
        day_lbl.setAlignment(Qt.AlignCenter)
        day_lbl.setStyleSheet(
            "color: #f1f5f9; font-size: 13px; font-weight: 700; background: transparent;"
        )
        yr_lbl = QLabel(today.strftime("%Y  ·  %A"))
        yr_lbl.setAlignment(Qt.AlignCenter)
        yr_lbl.setStyleSheet(
            "color: #93C5FD; font-size: 10px; background: transparent;"
        )
        dbl.addWidget(day_lbl)
        dbl.addWidget(yr_lbl)
        bl.addWidget(date_badge)

        return banner

    # ------------------------------------------------------------------
    # Load / refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        try:
            stats = await get_overview_stats()
            self._apply(stats)
        except Exception:
            pass

    def _apply(self, stats: dict) -> None:
        t = stats["today"]
        m = stats["month"]

        self._cards["t_count"].set_value(str(t.get("count", 0)))
        self._cards["t_tzs"].set_value(_fmt_tzs(t.get("tzs_total", 0)))
        self._cards["t_rcpt_ok"].set_value(str(t.get("receipt_received", 0)))
        pending = t.get("receipt_pending", 0) + t.get("receipt_missing", 0)
        self._cards["t_rcpt"].set_value(str(pending))

        self._cards["m_count"].set_value(str(m.get("count", 0)))
        self._cards["m_tzs"].set_value(_fmt_tzs(m.get("tzs_total", 0)))
        days_elapsed = max(date.today().day, 1)
        avg_daily = m.get("tzs_total", 0) / days_elapsed
        self._cards["m_avg"].set_value(_fmt_tzs(avg_daily))
        self._cards["m_unver"].set_value(str(m.get("unverified", 0)))

        if self._receipt_bar:
            self._receipt_bar.update_data(
                m.get("receipt_received", 0),
                m.get("receipt_pending", 0),
                m.get("receipt_missing", 0),
            )

        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rows = stats.get("recent", [])
        if rows:
            for i, tx in enumerate(rows):
                row_w = _activity_row(tx, even=(i % 2 == 0))
                self._rows_lay.addWidget(row_w)
                if i < len(rows) - 1:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.HLine)
                    sep.setStyleSheet(f"color: {_BORDER};")
                    self._rows_lay.addWidget(sep)
        else:
            empty = QLabel("No transactions yet.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color: {_MUTED}; font-size: 12px; padding: 24px; background: transparent;"
            )
            self._rows_lay.addWidget(empty)
