"""AccountantDashboard — Overview page."""

from __future__ import annotations
import asyncio
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QSizePolicy, QProgressBar,
)
from PySide6.QtCore import Qt, QRectF, QDateTime
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
import qtawesome as qta

# ── Design tokens ─────────────────────────────────────────────────────────────
_WHITE   = "#FFFFFF"
_BORDER  = "#E5E7EB"
_BG      = "#F4F6F8"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_PURPLE  = "#7C3AED"
_PURPLE_L = "#EDE9FE"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"

_CARD_SS = "background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 6px;"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str, size: int = 13, weight: int = 400,
         color: str = _T1, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color: {color}; font-size: {size}px; font-weight: {weight};"
        " font-family: 'Segoe UI', sans-serif; background: transparent;"
    )
    if wrap:
        w.setWordWrap(True)
    return w


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setStyleSheet(_CARD_SS)
    return f


def _hsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    sep.setStyleSheet(f"background: {_BORDER};")
    return sep


# ── KPI Card ──────────────────────────────────────────────────────────────────

class _KPICard(QFrame):
    def __init__(
        self,
        icon_name: str,
        icon_color: str,
        icon_bg: str,
        title: str,
        value: str,
        sub: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(_CARD_SS)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 14, 16, 16)
        vl.setSpacing(0)

        # Icon
        icon_w = QLabel()
        icon_w.setFixedSize(36, 36)
        icon_w.setAlignment(Qt.AlignCenter)
        icon_w.setStyleSheet(f"background: {icon_bg}; border-radius: 8px;")
        try:
            icon_w.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(20, 20))
        except Exception:
            pass
        vl.addWidget(icon_w)
        vl.addSpacing(10)

        # Value
        self._val_lbl = _lbl(value, size=24, weight=700)
        vl.addWidget(self._val_lbl)
        vl.addSpacing(4)

        # Sub
        self._sub_lbl = _lbl(sub, size=11, color=_T2, wrap=True)
        vl.addWidget(self._sub_lbl)
        vl.addSpacing(10)

        # Title
        vl.addWidget(_lbl(title, size=11, weight=600, color=_TM))

    def set_value(self, value: str) -> None:
        self._val_lbl.setText(value)

    def set_sub(self, sub: str) -> None:
        self._sub_lbl.setText(sub)


# ── Bar chart ─────────────────────────────────────────────────────────────────

_BAR_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
_BAR_VALUES = [120, 95, 140, 108, 130, 87]  # millions TZS


def _make_bar_chart() -> QWidget:
    try:
        import pyqtgraph as pg

        pg.setConfigOptions(antialias=True, foreground=_T2, background=_WHITE)

        plot = pg.PlotWidget()
        plot.setStyleSheet(f"border: 1px solid {_BORDER}; border-radius: 6px;")
        plot.setBackground(_WHITE)
        plot.showGrid(x=False, y=True, alpha=0.25)
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)

        ax = plot.getAxis("bottom")
        ax.setTicks([list(enumerate(_BAR_MONTHS))])
        ax.setStyle(tickFont=QFont("Segoe UI", 9))
        ax.setPen(pg.mkPen(color=_BORDER, width=1))

        ay = plot.getAxis("left")
        ay.setLabel("TZS (M)", color=_T2)
        ay.setPen(pg.mkPen(color=_BORDER, width=1))

        bars = pg.BarGraphItem(
            x=list(range(len(_BAR_MONTHS))),
            height=_BAR_VALUES,
            width=0.55,
            brush=pg.mkBrush(_BLUE),
            pen=pg.mkPen(None),
        )
        plot.addItem(bars)
        plot.setXRange(-0.6, len(_BAR_MONTHS) - 0.4, padding=0)
        plot.setYRange(0, max(_BAR_VALUES) * 1.15, padding=0)
        return plot

    except ImportError:
        return _FallbackBar()


class _FallbackBar(QWidget):
    """QPainter bar chart used when pyqtgraph is unavailable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(280, 160)
        self.setStyleSheet(f"background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 6px;")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pl, pr, pt, pb = 36, 16, 16, 28
        w = self.width() - pl - pr
        h = self.height() - pt - pb
        max_v = max(_BAR_VALUES)
        n = len(_BAR_VALUES)
        slot_w = w / n

        # Grid
        p.setPen(QPen(QColor(_BORDER), 1))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.drawLine(pl, y, pl + w, y)

        # Bars
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(_BLUE)))
        bar_w = slot_w * 0.55
        for i, v in enumerate(_BAR_VALUES):
            bh = (v / max_v) * h
            bx = int(pl + i * slot_w + (slot_w - bar_w) / 2)
            by = int(pt + h - bh)
            p.drawRoundedRect(bx, by, int(bar_w), int(bh), 2, 2)

        # Month labels
        p.setPen(QColor(_T2))
        p.setFont(QFont("Segoe UI", 8))
        fm = p.fontMetrics()
        for i, m in enumerate(_BAR_MONTHS):
            cx = int(pl + i * slot_w + slot_w / 2)
            tw = fm.horizontalAdvance(m)
            p.drawText(cx - tw // 2, self.height() - 6, m)

        p.end()


# ── Pie chart ─────────────────────────────────────────────────────────────────

_PIE_SLICES = [
    ("Mileage",  0.28, _BLUE),
    ("LATRA",    0.18, _GREEN),
    ("Diesel",   0.15, _AMBER),
    ("Council",  0.12, _PURPLE),
    ("Other",    0.27, _TM),
]


class _PieChart(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(150, 150)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        side = min(self.width(), self.height()) - 8
        x = (self.width() - side) / 2
        y = (self.height() - side) / 2
        rect = QRectF(x, y, side, side)

        angle = 90 * 16
        for _, frac, color in _PIE_SLICES:
            span = int(frac * 360 * 16)
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor(_WHITE), 2))
            p.drawPie(rect, angle, span)
            angle += span

        p.end()


def _pie_legend() -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(6)
    for label, frac, color in _PIE_SLICES:
        row = QHBoxLayout()
        row.setSpacing(7)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 13px; background: transparent;")
        dot.setFixedWidth(14)
        row.addWidget(dot)
        row.addWidget(_lbl(f"{label}  {int(frac * 100)}%", size=12, color=_T2))
        row.addStretch()
        vl.addLayout(row)
    return w


# ── Receipt status card ───────────────────────────────────────────────────────

def _receipt_card() -> QFrame:
    card = _card()
    vl = QVBoxLayout(card)
    vl.setContentsMargins(16, 14, 16, 16)
    vl.setSpacing(12)

    vl.addWidget(_lbl("Receipt Status Breakdown", size=13, weight=600))

    _ROWS = [
        ("✅  Received", 3241, 67, _GREEN, _GREEN_L),
        ("⏳  Pending",   982, 20, _AMBER, _AMBER_L),
        ("❌  Missing",   598, 13, _RED,   _RED_L),
    ]
    for label, count, pct, color, bg in _ROWS:
        row_w = QWidget()
        row_w.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(row_w)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.addWidget(_lbl(label, size=12, color=_T2))
        top_row.addStretch()
        top_row.addWidget(_lbl(f"{count:,}  ·  {pct}%", size=12, weight=600))
        rl.addLayout(top_row)

        bar = QProgressBar()
        bar.setFixedHeight(8)
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setStyleSheet(f"""
            QProgressBar {{
                background: {_BORDER};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background: {color};
                border-radius: 4px;
            }}
        """)
        rl.addWidget(bar)
        vl.addWidget(row_w)

    return card


# ── Recent activity card ──────────────────────────────────────────────────────

_ACTIVITY = [
    ("Jun 9",  [
        ("T588 DRE", "LATRA",             "TZS 130,000", _BLUE_L,  _BLUE),
        ("T760 DNH", "Mileage – Dar Congo", "TZS 100,000", _GREEN_L, _GREEN),
    ]),
    ("Jun 8", [
        ("T164 DZY", "Council Tunduma",   "TZS 15,000",  _AMBER_L, _AMBER),
        ("T712 DXY", "Return Weighbridge","TZS 27,600",  _BLUE_L,  _BLUE),
    ]),
]


def _activity_card() -> QFrame:
    card = _card()
    vl = QVBoxLayout(card)
    vl.setContentsMargins(16, 14, 16, 16)
    vl.setSpacing(10)

    header = QHBoxLayout()
    header.addWidget(_lbl("Recent Activity", size=13, weight=600))
    header.addStretch()
    view_all = QPushButton("View All →")
    view_all.setCursor(Qt.PointingHandCursor)
    view_all.setStyleSheet(
        f"QPushButton {{ border: none; color: {_BLUE}; font-size: 11px;"
        " font-family: 'Segoe UI', sans-serif; background: transparent; }}"
        f"QPushButton:hover {{ color: #005fa3; }}"
    )
    header.addWidget(view_all)
    vl.addLayout(header)

    for date_label, entries in _ACTIVITY:
        vl.addWidget(_lbl(f"─  {date_label}", size=10, weight=600, color=_TM))
        for truck, desc, amount, pill_bg, pill_fg in entries:
            row = QHBoxLayout()
            row.setSpacing(8)

            truck_l = _lbl(truck, size=11, weight=600)
            truck_l.setFixedWidth(68)
            row.addWidget(truck_l)

            desc_l = _lbl(desc, size=11, color=_T2)
            desc_l.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(desc_l)

            amount_l = _lbl(amount, size=11, weight=600, color=_T1)
            row.addWidget(amount_l)
            vl.addLayout(row)

    vl.addStretch()
    return card


# ── Quick actions ─────────────────────────────────────────────────────────────

def _quick_actions() -> QFrame:
    card = _card()
    vl = QVBoxLayout(card)
    vl.setContentsMargins(16, 14, 16, 16)
    vl.setSpacing(10)

    vl.addWidget(_lbl("Quick Actions", size=13, weight=600))

    row = QHBoxLayout()
    row.setSpacing(10)

    _ACTIONS = [
        ("mdi.check-circle-outline", _GREEN,   "Go to Verify Inbox"),
        ("mdi.table-large",          _BLUE,    "Master Table"),
        ("mdi.download-outline",     _PURPLE,  "Export Report"),
    ]
    for icon_name, color, label_txt in _ACTIONS:
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setFixedHeight(38)
        try:
            btn.setIcon(qta.icon(icon_name, color=color))
        except Exception:
            pass

        btn.setText(f"  {label_txt}")
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {_WHITE};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                color: {_T1};
                font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 12px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {_BG};
                border-color: #C7CDD6;
            }}
            QPushButton:pressed {{
                background: #E8F4FD;
            }}
        """)
        row.addWidget(btn)

    vl.addLayout(row)
    return card


# ── Welcome row ───────────────────────────────────────────────────────────────

def _welcome_row() -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent;")
    hl = QHBoxLayout(w)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(0)

    now = QDateTime.currentDateTime()
    hour = now.time().hour()
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")
    date_str = now.toString("dddd, d MMMM yyyy")

    hl.addWidget(_lbl(f"{greeting}, Accountant.", size=18, weight=700))
    hl.addSpacing(16)
    hl.addWidget(_lbl(date_str, size=13, color=_T2))
    hl.addStretch()

    fy = _lbl("FY 2025  ▾", size=12, weight=600, color=_BLUE)
    fy.setCursor(Qt.PointingHandCursor)
    hl.addWidget(fy)

    return w


# ── OverviewWidget (public) ───────────────────────────────────────────────────

class OverviewWidget(QWidget):
    """Overview dashboard — KPI cards load from live DB."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {_BG};")
        self._kpi_pending: Optional[_KPICard] = None
        self._kpi_master: Optional[_KPICard] = None
        self._kpi_verified: Optional[_KPICard] = None
        self._kpi_total: Optional[_KPICard] = None
        self._build()

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_kpis())

    async def _load_kpis(self) -> None:
        from tahmeed.services.accountant_service import get_overview_kpis
        try:
            kpis = await get_overview_kpis()
            self._apply_kpis(kpis)
        except Exception:
            pass

    def _apply_kpis(self, kpis: dict) -> None:
        if self._kpi_pending:
            self._kpi_pending.set_value(str(kpis["pending_count"]))

        if self._kpi_master:
            self._kpi_master.set_value(f"{kpis['master_count']:,}")

        if self._kpi_verified:
            self._kpi_verified.set_value(str(kpis["verified_this_month"]))
            self._kpi_verified.set_sub(f"of {kpis['submitted_this_month']} submitted")

        if self._kpi_total:
            tzs = kpis["total_tzs_ytd"]
            if tzs >= 1_000_000:
                tzs_str = f"{tzs / 1_000_000:.1f}M"
            else:
                tzs_str = f"{tzs:,.0f}"
            self._kpi_total.set_value(tzs_str)
            usd = kpis["total_usd_ytd"]
            self._kpi_total.set_sub(f"TZS  ·  ${usd:,.0f} USD")

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            f"QWidget {{ background: {_BG}; }}"
            "QScrollBar:vertical { background: #F4F6F8; width: 6px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #D1D5DB; border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        content = QWidget()
        content.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(content)
        vl.setContentsMargins(24, 20, 24, 24)
        vl.setSpacing(20)

        # Welcome
        vl.addWidget(_welcome_row())

        # KPI row
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(16)
        self._kpi_pending = _KPICard(
            "mdi.inbox-arrow-down", _RED, _RED_L,
            "PENDING VERIFICATION",
            "—",
            "entries awaiting review",
        )
        kpi_row.addWidget(self._kpi_pending)
        self._kpi_master = _KPICard(
            "mdi.table-large", _BLUE, _BLUE_L,
            "MASTER ENTRIES",
            "—",
            "YTD total verified",
        )
        kpi_row.addWidget(self._kpi_master)
        self._kpi_verified = _KPICard(
            "mdi.check-circle-outline", _GREEN, _GREEN_L,
            "VERIFIED THIS MONTH",
            "—",
            "of — submitted",
        )
        kpi_row.addWidget(self._kpi_verified)
        self._kpi_total = _KPICard(
            "mdi.currency-usd", _PURPLE, _PURPLE_L,
            "TOTAL EXPENSES",
            "—",
            "TZS  ·  $— USD",
        )
        kpi_row.addWidget(self._kpi_total)
        vl.addLayout(kpi_row)

        # Charts row
        charts_row = QHBoxLayout()
        charts_row.setSpacing(16)

        # Bar chart card
        bar_card = _card()
        bar_vl = QVBoxLayout(bar_card)
        bar_vl.setContentsMargins(16, 14, 16, 14)
        bar_vl.setSpacing(8)
        bar_vl.addWidget(_lbl("Monthly Expense Trend", size=13, weight=600))
        bar_vl.addWidget(_lbl("TZS (millions) · Jan – Jun 2025", size=11, color=_TM))
        bar_chart_w = _make_bar_chart()
        bar_chart_w.setMinimumHeight(180)
        bar_vl.addWidget(bar_chart_w, 1)
        charts_row.addWidget(bar_card, 6)

        # Pie chart card
        pie_card = _card()
        pie_vl = QVBoxLayout(pie_card)
        pie_vl.setContentsMargins(16, 14, 16, 14)
        pie_vl.setSpacing(8)
        pie_vl.addWidget(_lbl("By Category", size=13, weight=600))

        pie_body = QHBoxLayout()
        pie_body.setSpacing(12)
        pie = _PieChart()
        pie.setFixedSize(120, 120)
        pie_body.addWidget(pie, alignment=Qt.AlignVCenter)
        pie_body.addWidget(_pie_legend(), 1)
        pie_vl.addLayout(pie_body)
        pie_vl.addStretch()
        charts_row.addWidget(pie_card, 4)

        vl.addLayout(charts_row)

        # Bottom row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(16)
        bottom_row.addWidget(_receipt_card(), 6)
        bottom_row.addWidget(_activity_card(), 4)
        vl.addLayout(bottom_row)

        # Quick actions
        vl.addWidget(_quick_actions())

        scroll.setWidget(content)
        root.addWidget(scroll)
        self.refresh()
