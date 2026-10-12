"""
CashierOverview — spend analytics (Overview tab).

Layout:
  ┌─ Welcome banner ─────────────────────────────────────────────┐
  ├─ Top payees (line) ──────────────┬─ By item (donut) ─────────┤
  └──────────────────────────────────┴───────────────────────────┘
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from tahmeed.models.user import User
from tahmeed.services.cashier_service import (
    bucket_payee_spend,
    get_overview_charts,
    get_overview_stats,
)
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading

# ── Design tokens (aligned with accountant Overview) ─────────────────────────
_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_BG = "#F1F5F9"
_NAVY = "#1B2B4B"
_NAVY_2 = "#253A5C"
_BLUE = "#0077C5"
_BLUE_L = "#E8F4FD"
_GREEN = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER = "#D97706"
_AMBER_L = "#FEF3C7"
_RED = "#DC2626"
_RED_L = "#FEE2E2"
_PURPLE = "#7C3AED"
_PURPLE_L = "#EDE9FE"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"

_ITEM_COLORS = (_NAVY, _BLUE, _AMBER, _PURPLE, _TM)
_PAYEE_COLORS = (_NAVY, _BLUE, _AMBER, _PURPLE, _GREEN, "#0EA5E9")
_CURRENCIES = ("TZS", "USD", "ZMW")
_CURRENCY_KEY = {"TZS": "tzs", "USD": "usd", "ZMW": "zmw"}
_PERIODS = (
    ("month", "Month"),
    ("3m", "3 months"),
    ("year", "Year"),
)


def _lbl(text: str, size: int = 13, weight: int = 400,
         color: str = _T1, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"QLabel {{"
        f"  color: {color}; font-size: {size}px; font-weight: {weight};"
        f"  font-family:'Segoe UI'; background: transparent; border: none;"
        f"}}"
    )
    if wrap:
        w.setWordWrap(True)
    return w


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("overviewCard")
    f.setStyleSheet(
        "QFrame#overviewCard {"
        "  background: #FFFFFF;"
        "  border: 1px solid #E5E7EB;"
        "  border-radius: 12px;"
        "}"
    )
    return f


def _chart_values(currency: str, raw: list[float]) -> tuple[list[float], str]:
    if currency == "TZS":
        return [abs(v) / 1_000_000 for v in raw], "TZS millions"
    if currency == "USD":
        max_v = max((abs(v) for v in raw), default=0.0)
        if max_v >= 10_000:
            return [abs(v) / 1_000 for v in raw], "USD thousands"
        return [abs(v) for v in raw], "USD"
    max_v = max((abs(v) for v in raw), default=0.0)
    if max_v >= 1_000_000:
        return [abs(v) / 1_000_000 for v in raw], "ZMW millions"
    if max_v >= 10_000:
        return [abs(v) / 1_000 for v in raw], "ZMW thousands"
    return [abs(v) for v in raw], "ZMW"


def _currency_toggle_style(checked: bool) -> str:
    if checked:
        return (
            f"QPushButton {{"
            f"  background: {_NAVY}; color: {_WHITE}; border: 1px solid {_NAVY};"
            f"  border-radius: 6px; font-size: 11px; font-weight: 600;"
            f"  font-family: 'Segoe UI'; padding: 0 10px;"
            f"}}"
        )
    return (
        f"QPushButton {{"
        f"  background: {_WHITE}; color: {_T2}; border: 1px solid {_BORDER};"
        f"  border-radius: 6px; font-size: 11px; font-weight: 600;"
        f"  font-family: 'Segoe UI'; padding: 0 10px;"
        f"}}"
        f"QPushButton:hover {{ background: #F8FAFC; color: {_T1}; }}"
    )


class _CurrencyToggle(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._currency = "TZS"
        self.setStyleSheet("background: transparent; border: none;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for cur in _CURRENCIES:
            btn = QPushButton(cur)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setChecked(cur == "TZS")
            btn.setStyleSheet(_currency_toggle_style(cur == "TZS"))
            btn.clicked.connect(lambda _=False, c=cur: self._on_clicked(c))
            self._group.addButton(btn)
            self._buttons[cur] = btn
            row.addWidget(btn)

    def currency(self) -> str:
        return self._currency

    def set_currency(self, currency: str, *, emit: bool = True) -> None:
        cur = currency if currency in _CURRENCIES else "TZS"
        if cur == self._currency:
            return
        self._currency = cur
        for code, btn in self._buttons.items():
            btn.setChecked(code == cur)
            btn.setStyleSheet(_currency_toggle_style(code == cur))
        if emit:
            self.changed.emit(cur)

    def _on_clicked(self, currency: str) -> None:
        self.set_currency(currency)


class _PeriodToggle(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._period = "year"
        self.setStyleSheet("background: transparent; border: none;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, label in _PERIODS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setChecked(key == "year")
            btn.setStyleSheet(_currency_toggle_style(key == "year"))
            btn.clicked.connect(lambda _=False, k=key: self._on_clicked(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            row.addWidget(btn)

    def period(self) -> str:
        return self._period

    def set_period(self, period: str, *, emit: bool = True) -> None:
        key = period if period in self._buttons else "year"
        if key == self._period:
            return
        self._period = key
        for code, btn in self._buttons.items():
            btn.setChecked(code == key)
            btn.setStyleSheet(_currency_toggle_style(code == key))
        if emit:
            self.changed.emit(key)

    def _on_clicked(self, period: str) -> None:
        self.set_period(period)


def _fmt_tick(value: float) -> str:
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}M"
        return text.replace(".0M", "M")
    if value >= 1000:
        text = f"{value / 1000:.1f}k"
        return text.replace(".0k", "k")
    if abs(value - round(value)) < 0.05:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    frac = value / (10 ** exp)
    if frac <= 1:
        nice = 1
    elif frac <= 2:
        nice = 2
    elif frac <= 5:
        nice = 5
    else:
        nice = 10
    return nice * (10 ** exp)


class _DonutChart(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._slices: list[tuple[str, float, str]] = []
        self.setMinimumSize(160, 160)
        self.setStyleSheet("background: transparent; border: none;")

    def set_data(self, slices: list[tuple[str, float, str]]) -> None:
        self._slices = slices
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if not self._slices:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignCenter, "No data yet")
            p.end()
            return

        side = min(self.width(), self.height()) - 12
        x = (self.width() - side) / 2
        y = (self.height() - side) / 2
        rect = QRectF(x, y, side, side)

        angle = 90 * 16
        for _, frac, color in self._slices:
            span = max(1, int(frac * 360 * 16))
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor(_WHITE), 2))
            p.drawPie(rect, angle, span)
            angle += span

        inner = side * 0.55
        ix = x + (side - inner) / 2
        iy = y + (side - inner) / 2
        p.setBrush(QBrush(QColor(_WHITE)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(ix, iy, inner, inner))
        p.end()


def _donut_legend(slices: list[tuple[str, float, str]]) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent; border: none;")
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(8)
    for label, frac, color in slices:
        row = QHBoxLayout()
        row.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: 12px;"
            f" background: transparent; border: none; }}"
        )
        dot.setFixedWidth(12)
        row.addWidget(dot)
        pct = int(round(frac * 100))
        row.addWidget(_lbl(f"{label}  {pct}%", size=12, color=_T2))
        row.addStretch()
        vl.addLayout(row)
    return w


class _LineChartWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._series: list[tuple[str, str, list[float]]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet("background: transparent; border: none;")

    def set_data(
        self,
        labels: list[str],
        series: list[tuple[str, str, list[float]]],
    ) -> None:
        self._labels = labels or []
        self._series = series or []
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pl, pr, pt, pb = 48, 16, 12, 32
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        labels = self._labels
        n = max(1, len(labels))
        max_v = 0.0
        for _, _, values in self._series:
            if values:
                max_v = max(max_v, max(values))
        max_v = _nice_max(max_v)

        p.setPen(QPen(QColor(_BORDER), 1))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.drawLine(pl, y, pl + w, y)
            p.setPen(QColor(_TM))
            tick = max_v * i / 4
            p.drawText(4, y + 4, _fmt_tick(tick))
            p.setPen(QPen(QColor(_BORDER), 1))

        empty_msg = "No spend for this period."
        if not labels or not self._series or max_v <= 0:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(QRectF(pl, pt, w, h), Qt.AlignCenter, empty_msg)
            p.end()
            return

        has_values = any(any(v > 0 for v in values) for _, _, values in self._series)
        if not has_values:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(QRectF(pl, pt, w, h), Qt.AlignCenter, empty_msg)
            p.setPen(QColor(_T2))
            p.setFont(QFont("Segoe UI", 9))
            fm = p.fontMetrics()
            step = 1 if n <= 12 else max(1, (n - 1) // 7)
            for i, label in enumerate(labels):
                if i % step and i != n - 1:
                    continue
                cx = int(pl + (i / max(n - 1, 1)) * w) if n > 1 else pl + w // 2
                tw = fm.horizontalAdvance(label)
                p.drawText(cx - tw // 2, self.height() - 8, label)
            p.end()
            return

        def _x_at(i: int) -> float:
            if n == 1:
                return pl + w / 2
            return pl + (i / (n - 1)) * w

        def _y_at(value: float) -> float:
            return pt + h - (max(value, 0.0) / max_v) * h

        for _, color, values in self._series:
            if not values:
                continue
            path = QPainterPath()
            started = False
            points: list[QPointF] = []
            count = min(n, len(values))
            for i in range(count):
                point = QPointF(_x_at(i), _y_at(values[i]))
                points.append(point)
                if not started:
                    path.moveTo(point)
                    started = True
                else:
                    path.lineTo(point)
            p.setPen(QPen(QColor(color), 2.2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            if count <= 16:
                p.setBrush(QBrush(QColor(color)))
                p.setPen(Qt.NoPen)
                for point in points:
                    p.drawEllipse(point, 3.2, 3.2)

        p.setPen(QColor(_T2))
        p.setFont(QFont("Segoe UI", 9))
        fm = p.fontMetrics()
        step = 1 if n <= 12 else max(1, (n - 1) // 7)
        for i, label in enumerate(labels):
            if i % step and i != n - 1:
                continue
            cx = int(_x_at(i))
            tw = fm.horizontalAdvance(label)
            p.drawText(cx - tw // 2, self.height() - 8, label)

        p.end()


def _payee_legend(series: list[tuple[str, str, str]]) -> QWidget:
    """series items: (key, label, color)."""
    w = QWidget()
    w.setStyleSheet("background: transparent; border: none;")
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(14)
    for _key, label, color in series:
        cell = QHBoxLayout()
        cell.setSpacing(6)
        cell.setContentsMargins(0, 0, 0, 0)
        dot = QLabel("●")
        dot.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: 11px;"
            f" background: transparent; border: none; }}"
        )
        cell.addWidget(dot)
        cell.addWidget(_lbl(label, size=11, color=_T2))
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent; border: none;")
        wrap.setLayout(cell)
        row.addWidget(wrap)
    row.addStretch()
    return w


def _fmt_tzs(amount: float) -> str:
    abs_amt = abs(float(amount or 0.0))
    if abs_amt >= 1_000_000:
        return f"TZS {abs_amt / 1_000_000:.1f}M"
    if abs_amt >= 1_000:
        return f"TZS {abs_amt / 1_000:.1f}K"
    return f"TZS {abs_amt:,.0f}"


class _KPICard(QFrame):
    def __init__(
        self,
        icon_name: str,
        icon_color: str,
        icon_bg: str,
        value: str,
        label: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("overviewCard")
        self.setStyleSheet(
            "QFrame#overviewCard {"
            "  background: #FFFFFF;"
            "  border: 1px solid #E5E7EB;"
            "  border-radius: 12px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(110)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(18, 16, 18, 16)
        vl.setSpacing(0)

        icon_w = QLabel()
        icon_w.setFixedSize(32, 32)
        icon_w.setAlignment(Qt.AlignCenter)
        icon_w.setStyleSheet(
            f"QLabel {{ background: {icon_bg}; border: none; border-radius: 8px; }}"
        )
        try:
            icon_w.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(18, 18))
        except Exception:
            pass
        vl.addWidget(icon_w)
        vl.addSpacing(12)

        self._val_lbl = _lbl(value, size=26, weight=700)
        vl.addWidget(self._val_lbl)
        vl.addSpacing(4)
        self._label_lbl = _lbl(label, size=12, color=_T2, wrap=True)
        vl.addWidget(self._label_lbl)

    def set_value(self, value: str) -> None:
        self._val_lbl.setText(value)

    def set_label(self, label: str) -> None:
        self._label_lbl.setText(label)


class _ReceiptStatusCard(QFrame):
    """Single KPI-sized card listing Received / Pending / Missing counts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overviewCard")
        self.setStyleSheet(
            "QFrame#overviewCard {"
            "  background: #FFFFFF;"
            "  border: 1px solid #E5E7EB;"
            "  border-radius: 12px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(110)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(18, 14, 18, 14)
        vl.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        icon_w = QLabel()
        icon_w.setFixedSize(28, 28)
        icon_w.setAlignment(Qt.AlignCenter)
        icon_w.setStyleSheet(
            f"QLabel {{ background: {_AMBER_L}; border: none; border-radius: 8px; }}"
        )
        try:
            icon_w.setPixmap(
                qta.icon("mdi.receipt", color=_AMBER).pixmap(16, 16)
            )
        except Exception:
            pass
        header.addWidget(icon_w)
        header.addWidget(_lbl("Receipt status", size=13, weight=600))
        header.addStretch()
        vl.addLayout(header)

        self._rows: dict[str, QLabel] = {}
        for key, name, color in (
            ("received", "Received", _GREEN),
            ("pending", "Pending", _AMBER),
            ("missing", "Missing", _RED),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("●")
            dot.setStyleSheet(
                f"QLabel {{ color: {color}; font-size: 11px;"
                f" background: transparent; border: none; }}"
            )
            dot.setFixedWidth(12)
            row.addWidget(dot)
            row.addWidget(_lbl(name, size=12, color=_T2))
            row.addStretch()
            count = _lbl("0", size=13, weight=700)
            self._rows[key] = count
            row.addWidget(count)
            vl.addLayout(row)

    def set_counts(self, received: int, pending: int, missing: int) -> None:
        self._rows["received"].setText(str(int(received or 0)))
        self._rows["pending"].setText(str(int(pending or 0)))
        self._rows["missing"].setText(str(int(missing or 0)))


class CashierOverview(QWidget):
    """Cashier Overview — month KPIs, top-payee trend + by-item donut."""

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self._user = user
        self._loading = False
        self._year = date.today().year
        self._chart_currency = "TZS"
        self._period = "year"
        self._items: list[dict] = []
        self._payees: list[dict] = []
        self._kpi_entries: Optional[_KPICard] = None
        self._kpi_tzs: Optional[_KPICard] = None
        self._kpi_avg: Optional[_KPICard] = None
        self._receipt_card: Optional[_ReceiptStatusCard] = None
        self._payee_chart: Optional[_LineChartWidget] = None
        self._payee_subtitle: Optional[QLabel] = None
        self._payee_legend_host: Optional[QVBoxLayout] = None
        self._period_toggle: Optional[_PeriodToggle] = None
        self._payee_currency: Optional[_CurrencyToggle] = None
        self._donut: Optional[_DonutChart] = None
        self._donut_legend_host: Optional[QVBoxLayout] = None
        self._item_currency: Optional[_CurrencyToggle] = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setStyleSheet(
            f"CashierOverview {{ background: {_BG}; border: none; }}"
            "CashierOverview QLabel { border: none; }"
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            f"QScrollBar:vertical {{ background: {_BG}; width: 6px; margin: 0; }}"
            "QScrollBar::handle:vertical {"
            "  background: #cbd5e1; border-radius: 3px; min-height: 24px;"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        content = QWidget()
        content.setStyleSheet(f"background: {_BG};")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(28, 24, 28, 28)
        cl.setSpacing(16)

        cl.addWidget(self._build_banner())

        # ── Month KPI row ─────────────────────────────────────────────
        month_name = date.today().strftime("%B %Y")
        cl.addWidget(_lbl(f"This month — {month_name}", size=12, weight=600, color=_TM))
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self._kpi_entries = _KPICard(
            "mdi.calendar-month-outline", _BLUE, _BLUE_L, "—", "Total entries",
        )
        kpi_row.addWidget(self._kpi_entries)
        self._kpi_tzs = _KPICard(
            "mdi.cash", _BLUE, _BLUE_L, "—", "TZS total",
        )
        kpi_row.addWidget(self._kpi_tzs)
        self._kpi_avg = _KPICard(
            "mdi.chart-timeline-variant", _PURPLE, _PURPLE_L, "—",
            "Avg. daily (TZS)",
        )
        kpi_row.addWidget(self._kpi_avg)
        self._receipt_card = _ReceiptStatusCard()
        kpi_row.addWidget(self._receipt_card)
        cl.addLayout(kpi_row)

        charts = QHBoxLayout()
        charts.setSpacing(16)

        # ── Top payees line chart ─────────────────────────────────────
        payee_card = _card()
        payee_card.setMinimumHeight(320)
        payee_vl = QVBoxLayout(payee_card)
        payee_vl.setContentsMargins(20, 18, 20, 18)
        payee_vl.setSpacing(6)

        payee_header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.setContentsMargins(0, 0, 0, 0)
        titles.addWidget(_lbl("Top payees", size=14, weight=600))
        self._payee_subtitle = _lbl("Spend by month", size=12, color=_TM)
        titles.addWidget(self._payee_subtitle)
        payee_header.addLayout(titles)
        payee_header.addStretch()
        self._payee_currency = _CurrencyToggle()
        self._payee_currency.changed.connect(self._on_currency_changed)
        payee_header.addWidget(self._payee_currency)
        self._period_toggle = _PeriodToggle()
        self._period_toggle.changed.connect(self._on_period_changed)
        payee_header.addWidget(self._period_toggle)
        payee_vl.addLayout(payee_header)

        self._payee_chart = _LineChartWidget()
        payee_vl.addWidget(self._payee_chart, 1)
        legend_wrap = QWidget()
        legend_wrap.setStyleSheet("background: transparent; border: none;")
        self._payee_legend_host = QVBoxLayout(legend_wrap)
        self._payee_legend_host.setContentsMargins(0, 4, 0, 0)
        payee_vl.addWidget(legend_wrap)
        charts.addWidget(payee_card, 6)

        # ── By item donut ─────────────────────────────────────────────
        item_card = _card()
        item_vl = QVBoxLayout(item_card)
        item_vl.setContentsMargins(20, 18, 20, 18)
        item_vl.setSpacing(12)
        item_header = QHBoxLayout()
        item_header.addWidget(_lbl("By item", size=14, weight=600))
        item_header.addStretch()
        self._item_currency = _CurrencyToggle()
        self._item_currency.changed.connect(self._on_currency_changed)
        item_header.addWidget(self._item_currency)
        item_vl.addLayout(item_header)
        self._donut = _DonutChart()
        item_vl.addWidget(self._donut, alignment=Qt.AlignHCenter)
        item_legend = QWidget()
        item_legend.setStyleSheet("background: transparent; border: none;")
        self._donut_legend_host = QVBoxLayout(item_legend)
        self._donut_legend_host.setContentsMargins(0, 0, 0, 0)
        item_vl.addWidget(item_legend)
        charts.addWidget(item_card, 4)

        cl.addLayout(charts)
        cl.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    def _build_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("Banner")
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
        greeting = (
            "Good morning" if hour < 12
            else ("Good afternoon" if hour < 17 else "Good evening")
        )
        greet_lbl = QLabel(f"{greeting}, {self._user.full_name.split()[0]}")
        greet_lbl.setStyleSheet(
            "color: #f1f5f9; font-size: 15px; font-weight: 700; background: transparent;"
        )
        sub_lbl = QLabel("Item and payee money consumption")
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

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        show_widget_loading(self, "Loading overview…")
        try:
            charts, stats = await asyncio.gather(
                get_overview_charts(self._year),
                get_overview_stats(),
                return_exceptions=True,
            )
            if not isinstance(charts, Exception):
                self._apply_charts(charts)
            if not isinstance(stats, Exception):
                self._apply_stats(stats)
        except Exception:
            pass
        finally:
            self._loading = False
            hide_widget_loading(self)

    def _apply_charts(self, data: dict) -> None:
        self._year = int(data.get("year") or self._year)
        self._items = list(data.get("items") or [])
        self._payees = list(data.get("payees") or [])
        self._refresh_payee_chart()
        self._refresh_item_chart()

    def _apply_stats(self, stats: dict) -> None:
        month = stats.get("month") or {}
        count = int(month.get("count") or 0)
        tzs_total = float(month.get("tzs_total") or 0.0)
        verified = int(month.get("verified") or 0)
        days_elapsed = max(date.today().day, 1)
        avg_daily = tzs_total / days_elapsed

        if self._kpi_entries:
            self._kpi_entries.set_value(f"{count:,}")
            self._kpi_entries.set_label("Total entries this month")
        if self._kpi_tzs:
            self._kpi_tzs.set_value(_fmt_tzs(tzs_total))
            self._kpi_tzs.set_label("TZS total this month")
        if self._kpi_avg:
            self._kpi_avg.set_value(_fmt_tzs(avg_daily))
            self._kpi_avg.set_label(f"Avg. daily · {verified:,} verified")
        if self._receipt_card:
            self._receipt_card.set_counts(
                int(month.get("receipt_received") or 0),
                int(month.get("receipt_pending") or 0),
                int(month.get("receipt_missing") or 0),
            )

    def _on_currency_changed(self, currency: str) -> None:
        if currency == self._chart_currency:
            return
        self._chart_currency = currency
        if self._payee_currency and self._payee_currency.currency() != currency:
            self._payee_currency.set_currency(currency, emit=False)
        if self._item_currency and self._item_currency.currency() != currency:
            self._item_currency.set_currency(currency, emit=False)
        self._refresh_payee_chart()
        self._refresh_item_chart()

    def _on_period_changed(self, period: str) -> None:
        if period == self._period:
            return
        self._period = period
        self._refresh_payee_chart()

    def _refresh_payee_chart(self) -> None:
        chart = bucket_payee_spend(
            self._payees,
            self._period,
            self._year,
            self._chart_currency,
        )
        all_raw = [
            float(v or 0.0)
            for item in (chart.get("series") or [])
            for v in (item.get("values") or [])
        ]
        _, unit = _chart_values(self._chart_currency, all_raw or [0.0])

        painted: list[tuple[str, str, list[float]]] = []
        legend: list[tuple[str, str, str]] = []
        for i, item in enumerate(chart.get("series") or []):
            color = _PAYEE_COLORS[i % len(_PAYEE_COLORS)]
            raw = [float(v or 0.0) for v in (item.get("values") or [])]
            if unit in ("TZS millions", "ZMW millions"):
                scaled = [v / 1_000_000 for v in raw]
            elif unit in ("USD thousands", "ZMW thousands"):
                scaled = [v / 1_000 for v in raw]
            else:
                scaled = list(raw)
            key = item.get("key") or f"p{i}"
            label = item.get("label") or "Payee"
            painted.append((key, color, scaled))
            legend.append((key, label, color))

        if self._payee_chart:
            self._payee_chart.set_data(list(chart.get("labels") or []), painted)
        if self._payee_subtitle:
            base = chart.get("subtitle") or "Spend"
            self._payee_subtitle.setText(f"{base}  ·  {unit}")
        if self._payee_legend_host:
            while self._payee_legend_host.count():
                item = self._payee_legend_host.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if legend:
                self._payee_legend_host.addWidget(_payee_legend(legend))
            else:
                self._payee_legend_host.addWidget(
                    _lbl("No payee spend yet.", size=12, color=_TM)
                )

    def _refresh_item_chart(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        amounts = [
            (cat.get("name") or "Uncategorised", float(cat.get(key, 0.0) or 0.0))
            for cat in self._items
        ]
        total = sum(a for _, a in amounts)
        slices: list[tuple[str, float, str]] = []
        if total > 0:
            for i, (name, amount) in enumerate(amounts):
                if amount <= 0:
                    continue
                color = _ITEM_COLORS[i % len(_ITEM_COLORS)]
                slices.append((name, amount / total, color))

        if self._donut:
            self._donut.set_data(slices)
        if self._donut_legend_host:
            while self._donut_legend_host.count():
                item = self._donut_legend_host.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if slices:
                self._donut_legend_host.addWidget(_donut_legend(slices))
            else:
                self._donut_legend_host.addWidget(
                    _lbl(
                        f"No {self._chart_currency} item spend yet.",
                        size=12,
                        color=_TM,
                    )
                )
