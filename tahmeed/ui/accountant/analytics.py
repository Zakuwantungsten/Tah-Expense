"""AccountantDashboard — Analytics page (spend KPIs + 3×2 chart grid)."""

from __future__ import annotations

import math
from calendar import month_abbr
from datetime import date
from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QSize, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QSizePolicy, QMenu, QButtonGroup,
    QStackedWidget, QToolButton,
)

from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.overview_geometry import OverviewGeometryContent
from tahmeed.ui.accountant.chart_interactions import (
    ChartInteractionMixin,
    rect_contains,
)

# ── Design tokens ────────────────────────────────────────────────────────────
_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_BG = "#F1F5F9"
_NAVY = "#1B2B4B"
_BLUE = "#0077C5"
_BLUE_L = "#E8F4FD"
_GREEN = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER = "#D97706"
_AMBER_L = "#FEF3C7"
_RED = "#DC2626"
_TITLE = "#C45C5C"  # screenshot-style chart titles
_PURPLE = "#7C3AED"
_ORANGE = "#E67E22"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"

_TREEMAP_COLORS = (
    "#3B82F6", "#1E3A5F", "#EAB308", "#EF4444", "#22C55E",
    "#60A5FA", "#A78BFA", "#F97316", "#14B8A6", "#94A3B8",
)
_MONTH_ABBR = tuple(month_abbr[1:])
_CURRENCIES = ("TZS", "USD", "ZMW")
_CURRENCY_KEY = {"TZS": "tzs", "USD": "usd", "ZMW": "zmw"}

_SUBTABS: tuple[tuple[str, str, str], ...] = (
    ("main", "mdi.chart-box-outline", "Spend overview"),
    ("trends", "mdi.chart-line", "Trends"),
    ("regions", "mdi.earth", "Entities"),
    ("trucks", "mdi.truck-outline", "Trucks"),
    ("scatter", "mdi.chart-scatter-plot", "Scatter"),
    ("table", "mdi.database-outline", "Data table"),
    ("meeting", "mdi.account-group-outline", "Meetings"),
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
    f.setObjectName("analyticsCard")
    f.setStyleSheet(
        "QFrame#analyticsCard {"
        "  background: #FFFFFF;"
        "  border: 1px solid #E5E7EB;"
        "  border-radius: 12px;"
        "}"
    )
    return f


def _chart_title(text: str) -> QLabel:
    return _lbl(text, size=13, weight=600, color=_TITLE)


def _fmt_currency_short(currency: str, amount: float) -> str:
    abs_amt = abs(amount or 0.0)
    cur = (currency or "TZS").upper()
    if cur == "USD":
        if abs_amt >= 1_000_000:
            return f"${abs_amt / 1_000_000:.2f}M"
        if abs_amt >= 1_000:
            return f"${abs_amt / 1_000:.1f}K"
        return f"${abs_amt:,.0f}"
    prefix = "TZS" if cur == "TZS" else "ZMW"
    if abs_amt >= 1_000_000:
        return f"{prefix} {abs_amt / 1_000_000:.1f}M"
    if abs_amt >= 1_000:
        return f"{prefix} {abs_amt / 1_000:.1f}K"
    return f"{prefix} {abs_amt:,.0f}"


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


def _toggle_style(checked: bool) -> str:
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
            btn.setStyleSheet(_toggle_style(cur == "TZS"))
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
            btn.setStyleSheet(_toggle_style(code == cur))
        if emit:
            self.changed.emit(cur)

    def _on_clicked(self, currency: str) -> None:
        self.set_currency(currency)


class _KPICard(QFrame):
    period_menu_requested = Signal()

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
        self.setObjectName("analyticsCard")
        self.setStyleSheet(
            "QFrame#analyticsCard {"
            "  background: #FFFFFF;"
            "  border: 1px solid #E5E7EB;"
            "  border-radius: 12px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(118)
        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 14, 16, 14)
        vl.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        icon_w = QLabel()
        icon_w.setFixedSize(40, 40)
        icon_w.setAlignment(Qt.AlignCenter)
        icon_w.setStyleSheet(
            f"QLabel {{ background: {icon_bg}; border: none; border-radius: 10px; }}"
        )
        try:
            icon_w.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(26, 26))
        except Exception:
            pass
        top.addWidget(icon_w)
        top.addStretch()
        self._period_btn = QPushButton("Full year")
        self._period_btn.setCursor(Qt.PointingHandCursor)
        self._period_btn.setFixedHeight(26)
        self._period_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: {_WHITE}; color: {_T2}; border: 1px solid {_BORDER};"
            f"  border-radius: 6px; font-size: 11px; font-weight: 600;"
            f"  font-family: 'Segoe UI'; padding: 0 8px;"
            f"}}"
            f"QPushButton:hover {{ background: #F8FAFC; color: {_T1}; }}"
            "QPushButton::menu-indicator { image: none; width: 0; }"
        )
        self._period_btn.clicked.connect(self.period_menu_requested.emit)
        top.addWidget(self._period_btn)
        vl.addLayout(top)
        vl.addSpacing(10)
        self._val_lbl = _lbl(value, size=22, weight=700)
        vl.addWidget(self._val_lbl)
        vl.addSpacing(4)
        self._label_lbl = _lbl(label, size=12, color=_T2, wrap=True)
        vl.addWidget(self._label_lbl)

    def set_value(self, value: str) -> None:
        self._val_lbl.setText(value)

    def set_label(self, label: str) -> None:
        self._label_lbl.setText(label)

    def set_period_text(self, text: str) -> None:
        self._period_btn.setText(text)

    def period_button(self) -> QPushButton:
        return self._period_btn


# ── Chart widgets ────────────────────────────────────────────────────────────

class _TreemapWidget(ChartInteractionMixin, QWidget):
    """Category spend treemap (screenshot-style blocks)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, float, str]] = []
        self._empty = "No category spend yet."
        self._hit_cells: list[tuple[QRectF, str, float]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Spend by Category")

    def set_data(
        self,
        items: list[tuple[str, float, str]],
        *,
        empty: str | None = None,
    ) -> None:
        self._items = [(n, v, c) for n, v, c in items if v > 0]
        if empty is not None:
            self._empty = empty
        self.update()

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        for rect, name, value in self._hit_cells:
            if rect_contains(rect, pos):
                return f"{name}\n{_fmt_tick(value)}"
        return None

    def chart_full_view_factory(self) -> QWidget:
        clone = _TreemapWidget()
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(list(self._items), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hit_cells = []
        if not self._items:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty)
            p.end()
            return

        rect = QRectF(4, 4, max(1, self.width() - 8), max(1, self.height() - 8))
        total = sum(v for _, v, _ in self._items) or 1.0
        self._paint_slice(p, self._items, rect, total, horizontal=True)
        p.end()

    def _paint_slice(
        self,
        p: QPainter,
        items: list[tuple[str, float, str]],
        rect: QRectF,
        total: float,
        *,
        horizontal: bool,
    ) -> None:
        if not items or rect.width() < 2 or rect.height() < 2:
            return
        if len(items) == 1:
            name, value, color = items[0]
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor(_WHITE), 2))
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(QColor(_WHITE))
            p.setFont(QFont("Segoe UI", 9, QFont.Bold))
            label = f"{name}\n{_fmt_tick(value)}"
            p.drawText(rect.adjusted(6, 6, -6, -6), Qt.AlignLeft | Qt.AlignTop, label)
            self._hit_cells.append((QRectF(rect), name, float(value)))
            return

        first = items[0]
        rest = items[1:]
        share = first[1] / total
        if horizontal:
            w = max(18.0, rect.width() * share)
            a = QRectF(rect.x(), rect.y(), w, rect.height())
            b = QRectF(rect.x() + w, rect.y(), rect.width() - w, rect.height())
        else:
            h = max(18.0, rect.height() * share)
            a = QRectF(rect.x(), rect.y(), rect.width(), h)
            b = QRectF(rect.x(), rect.y() + h, rect.width(), rect.height() - h)
        self._paint_slice(p, [first], a, first[1], horizontal=not horizontal)
        rest_total = sum(v for _, v, _ in rest) or 1.0
        self._paint_slice(p, rest, b, rest_total, horizontal=not horizontal)


class _LineChartWidget(ChartInteractionMixin, QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._series: list[tuple[str, str, list[float]]] = []
        self._empty = "No spend for this period."
        self._hit_points: list[tuple[QPointF, str, str, float]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Spend Trend")

    def set_data(
        self,
        labels: list[str],
        series: list[tuple[str, str, list[float]]],
        *,
        empty: str | None = None,
    ) -> None:
        self._labels = labels or []
        self._series = series or []
        if empty is not None:
            self._empty = empty
        self.update()

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        best = None
        best_d = 14.0
        for point, series_name, label, value in self._hit_points:
            d = math.hypot(point.x() - pos.x(), point.y() - pos.y())
            if d < best_d:
                best_d = d
                best = f"{series_name} · {label}\n{_fmt_tick(value)}"
        return best

    def chart_full_view_factory(self) -> QWidget:
        clone = _LineChartWidget()
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(list(self._labels), list(self._series), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hit_points = []
        pl, pr, pt, pb = 48, 16, 12, 36
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
            p.drawText(4, y + 4, _fmt_tick(max_v * i / 4))
            p.setPen(QPen(QColor(_BORDER), 1))

        has_values = any(any(v > 0 for v in values) for _, _, values in self._series)
        if not labels or not self._series or not has_values:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(QRectF(pl, pt, w, h), Qt.AlignCenter, self._empty)
            p.end()
            return

        def _x_at(i: int) -> float:
            if n == 1:
                return pl + w / 2
            return pl + (i / (n - 1)) * w

        def _y_at(value: float) -> float:
            return pt + h - (max(value, 0.0) / max_v) * h

        for name, color, values in self._series:
            if not values:
                continue
            path = QPainterPath()
            points: list[QPointF] = []
            count = min(n, len(values))
            for i in range(count):
                point = QPointF(_x_at(i), _y_at(values[i]))
                points.append(point)
                label = labels[i] if i < len(labels) else str(i + 1)
                self._hit_points.append((point, name, label, float(values[i])))
                if i == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            p.setPen(QPen(QColor(color), 2.2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setBrush(QBrush(QColor(color)))
            p.setPen(Qt.NoPen)
            for point in points:
                p.drawEllipse(point, 3.0, 3.0)

        # Legend
        lx = pl
        p.setFont(QFont("Segoe UI", 9))
        for name, color, _ in self._series:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawRect(int(lx), self.height() - 16, 10, 10)
            p.setPen(QColor(_T2))
            p.drawText(int(lx + 14), self.height() - 7, name)
            lx += 14 + p.fontMetrics().horizontalAdvance(name) + 16

        p.setPen(QColor(_T2))
        p.setFont(QFont("Segoe UI", 8))
        fm = p.fontMetrics()
        for i, label in enumerate(labels):
            cx = int(_x_at(i))
            tw = fm.horizontalAdvance(label)
            p.drawText(cx - tw // 2, int(pt + h + 14), label)
        p.end()


class _ParetoChartWidget(ChartInteractionMixin, QWidget):
    """Top 80% / Bottom 20% bars + transaction count line."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._top_amt = 0.0
        self._bot_amt = 0.0
        self._top_count = 0
        self._bot_count = 0
        self._unit = ""
        self._hit_bars: list[tuple[QRectF, str]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Pareto Analysis")

    def set_data(
        self,
        top_amount: float,
        bottom_amount: float,
        top_count: int,
        bottom_count: int,
        *,
        unit: str = "",
    ) -> None:
        self._top_amt = max(0.0, top_amount)
        self._bot_amt = max(0.0, bottom_amount)
        self._top_count = max(0, top_count)
        self._bot_count = max(0, bottom_count)
        self._unit = unit
        self.update()

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        for rect, tip in self._hit_bars:
            if rect_contains(rect, pos):
                return tip
        return None

    def chart_full_view_factory(self) -> QWidget:
        clone = _ParetoChartWidget()
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(
            self._top_amt,
            self._bot_amt,
            self._top_count,
            self._bot_count,
            unit=self._unit,
        )
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hit_bars = []
        pl, pr, pt, pb = 48, 48, 12, 40
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        amounts = [self._top_amt, self._bot_amt]
        counts = [float(self._top_count), float(self._bot_count)]
        if self._top_amt <= 0 and self._bot_amt <= 0:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignCenter, "No pareto data yet.")
            p.end()
            return

        max_a = _nice_max(max(amounts) or 1.0)
        max_c = _nice_max(max(counts) or 1.0)
        labels = ("Top 80%", "Bottom 20%")

        p.setPen(QPen(QColor(_BORDER), 1))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.drawLine(pl, y, pl + w, y)
            p.setPen(QColor(_TM))
            p.drawText(4, y + 4, _fmt_tick(max_a * i / 4))
            p.setPen(QColor(_TM))
            right = _fmt_tick(max_c * i / 4)
            p.drawText(pl + w + 6, y + 4, right)
            p.setPen(QPen(QColor(_BORDER), 1))

        slot = w / 2
        bar_w = slot * 0.45
        centers: list[float] = []
        for i, amt in enumerate(amounts):
            cx = pl + i * slot + slot / 2
            centers.append(cx)
            bh = (amt / max_a) * h if amt > 0 else 0
            bx = cx - bar_w / 2
            by = pt + h - bh
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(_BLUE)))
            p.drawRoundedRect(int(bx), int(by), int(bar_w), int(bh), 3, 3)
            tip = (
                f"{labels[i]}\nSpend: {_fmt_tick(amt)}"
                f"{(' ' + self._unit) if self._unit else ''}"
                f"\nTransactions: {_fmt_tick(counts[i])}"
            )
            self._hit_bars.append(
                (QRectF(bx, by, bar_w, max(bh, 8.0)), tip)
            )
            p.setPen(QColor(_T1))
            p.setFont(QFont("Segoe UI", 9, QFont.Bold))
            val = _fmt_tick(amt)
            tw = p.fontMetrics().horizontalAdvance(val)
            p.drawText(int(cx - tw / 2), int(by - 4), val)

        # Count line
        path = QPainterPath()
        points: list[QPointF] = []
        for i, c in enumerate(counts):
            pt_x = centers[i]
            pt_y = pt + h - (c / max_c) * h
            points.append(QPointF(pt_x, pt_y))
            if i == 0:
                path.moveTo(points[-1])
            else:
                path.lineTo(points[-1])
        p.setPen(QPen(QColor(_RED), 2.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setBrush(QBrush(QColor(_RED)))
        p.setPen(Qt.NoPen)
        for point in points:
            p.drawEllipse(point, 3.5, 3.5)
            # count label
            idx = points.index(point) if point in points else 0
        for i, point in enumerate(points):
            p.setPen(QColor(_RED))
            p.setFont(QFont("Segoe UI", 8))
            txt = _fmt_tick(counts[i])
            tw = p.fontMetrics().horizontalAdvance(txt)
            p.drawText(int(point.x() - tw / 2), int(point.y() - 8), txt)

        p.setPen(QColor(_T2))
        p.setFont(QFont("Segoe UI", 9))
        for i, lab in enumerate(labels):
            tw = p.fontMetrics().horizontalAdvance(lab)
            p.drawText(int(centers[i] - tw / 2), self.height() - 18, lab)

        # Mini legend
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(_BLUE)))
        p.drawRect(pl, self.height() - 12, 8, 8)
        p.setPen(QColor(_T2))
        p.drawText(pl + 12, self.height() - 4, f"Spend ({self._unit})" if self._unit else "Spend")
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(_RED)))
        p.drawRect(pl + 110, self.height() - 12, 8, 8)
        p.setPen(QColor(_T2))
        p.drawText(pl + 122, self.height() - 4, "Transactions")
        p.end()


class _HBarChartWidget(ChartInteractionMixin, QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, float]] = []
        self._empty = "No change vs prior year."
        self._hit_rows: list[tuple[QRectF, str, float]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Category MoM")

    def set_data(
        self,
        rows: list[tuple[str, float]],
        *,
        empty: str | None = None,
    ) -> None:
        self._rows = rows or []
        if empty is not None:
            self._empty = empty
        n = max(4, len(self._rows))
        self.setMinimumHeight(max(220, 28 + n * 26))
        self.update()

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        for rect, label, value in self._hit_rows:
            if rect_contains(rect, pos):
                return f"{label}\n{_fmt_tick(value)}"
        return None

    def chart_full_view_factory(self) -> QWidget:
        clone = _HBarChartWidget()
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(list(self._rows), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hit_rows = []
        if not self._rows:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty)
            p.end()
            return

        pl, pr, pt, pb = 120, 56, 8, 8
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        n = len(self._rows)
        max_v = max((v for _, v in self._rows), default=0.0) or 1.0
        slot = h / n
        bar_h = min(16.0, slot * 0.55)
        p.setFont(QFont("Segoe UI", 9))
        fm = p.fontMetrics()
        for i, (label, value) in enumerate(self._rows):
            cy = pt + i * slot + slot / 2
            p.setPen(QColor(_T2))
            text = fm.elidedText(label, Qt.ElideRight, pl - 8)
            p.drawText(4, int(cy + fm.ascent() / 2 - 2), text)
            bw = (value / max_v) * w if value > 0 else 0
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(_BLUE)))
            p.drawRoundedRect(int(pl), int(cy - bar_h / 2), max(0, int(bw)), int(bar_h), 3, 3)
            hit = QRectF(4, cy - slot / 2, max(1, self.width() - 8), slot)
            self._hit_rows.append((hit, label, float(value)))
            p.setPen(QColor(_T1))
            p.drawText(int(pl + bw + 6), int(cy + fm.ascent() / 2 - 2), _fmt_tick(value))
        p.end()


class _VBarChartWidget(ChartInteractionMixin, QWidget):
    """Vertical bars with rotated x labels (supplier distribution)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[float] = []
        self._empty = "No truck spend yet."
        self._hit_bars: list[tuple[QRectF, str, float]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Distribution")

    def set_data(
        self,
        labels: list[str],
        values: list[float],
        *,
        empty: str | None = None,
    ) -> None:
        self._labels = labels or []
        self._values = values or []
        if empty is not None:
            self._empty = empty
        self.update()

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        for rect, label, value in self._hit_bars:
            if rect_contains(rect, pos):
                return f"{label}\n{_fmt_tick(value)}"
        return None

    def chart_full_view_factory(self) -> QWidget:
        clone = _VBarChartWidget()
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(list(self._labels), list(self._values), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hit_bars = []
        if not self._labels or not any(v > 0 for v in self._values):
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty)
            p.end()
            return

        pl, pr, pt, pb = 40, 12, 18, 58
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        n = len(self._labels)
        max_v = _nice_max(max(self._values) or 1.0)

        p.setPen(QPen(QColor(_BORDER), 1))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.drawLine(pl, y, pl + w, y)
            p.setPen(QColor(_TM))
            p.drawText(4, y + 4, _fmt_tick(max_v * i / 4))
            p.setPen(QPen(QColor(_BORDER), 1))

        slot = w / n
        bar_w = slot * 0.55
        for i, v in enumerate(self._values):
            bh = (v / max_v) * h if v > 0 else 0
            bx = pl + i * slot + (slot - bar_w) / 2
            by = pt + h - bh
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(_BLUE)))
            p.drawRoundedRect(int(bx), int(by), int(bar_w), int(bh), 3, 3)
            lab = self._labels[i] if i < len(self._labels) else "—"
            self._hit_bars.append(
                (QRectF(bx, min(by, pt + h - 8), max(bar_w, 8.0), max(bh, 8.0)), lab, float(v))
            )
            if v > 0:
                p.setPen(QColor(_T1))
                p.setFont(QFont("Segoe UI", 7))
                txt = _fmt_tick(v)
                tw = p.fontMetrics().horizontalAdvance(txt)
                p.drawText(int(bx + bar_w / 2 - tw / 2), int(by - 2), txt)

        p.setFont(QFont("Segoe UI", 8))
        for i, lab in enumerate(self._labels):
            cx = pl + i * slot + slot / 2
            short = lab if len(lab) <= 10 else lab[:9] + "…"
            p.save()
            p.translate(cx, pt + h + 8)
            p.rotate(-45)
            p.setPen(QColor(_T2))
            p.drawText(0, 0, short)
            p.restore()
        p.end()


class _DonutChart(ChartInteractionMixin, QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._slices: list[tuple[str, float, str, str]] = []  # label, frac, color, amount_text
        self._empty = "No data yet"
        self._hit_slices: list[tuple[float, float, str]] = []  # start_deg, span_deg, tip
        self._donut_center = QPointF(0, 0)
        self._outer_r = 0.0
        self._inner_r = 0.0
        self.setMinimumSize(160, 160)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Expense distribution")

    def set_data(
        self,
        slices: list[tuple[str, float, str, str]],
        *,
        empty: str | None = None,
    ) -> None:
        self._slices = slices
        if empty is not None:
            self._empty = empty
        self.update()

    def chart_tooltip_at(self, pos: QPoint) -> str | None:
        if not self._hit_slices or self._outer_r <= 0:
            return None
        dx = pos.x() - self._donut_center.x()
        dy = pos.y() - self._donut_center.y()
        dist = math.hypot(dx, dy)
        if dist < self._inner_r or dist > self._outer_r:
            return None
        # Qt pie: 0° at 3 o'clock, CCW. Screen y is down → flip for atan2.
        qt_deg = math.degrees(math.atan2(-dy, dx)) % 360.0
        for start, span, tip in self._hit_slices:
            end = (start + span) % 360.0
            if span >= 359.9:
                return tip
            if start <= end:
                if start <= qt_deg <= end:
                    return tip
            else:
                if qt_deg >= start or qt_deg <= end:
                    return tip
        return None

    def chart_full_view_factory(self) -> QWidget:
        clone = _DonutChart()
        clone.set_chart_title(self.chart_full_view_title())
        clone.setMinimumSize(360, 360)
        clone.set_data(list(self._slices), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hit_slices = []
        if not self._slices:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 11))
            p.drawText(self.rect(), Qt.AlignCenter, self._empty)
            p.end()
            return
        side = min(self.width(), self.height()) - 12
        x = (self.width() - side) / 2
        y = (self.height() - side) / 2
        rect = QRectF(x, y, side, side)
        self._donut_center = QPointF(x + side / 2, y + side / 2)
        self._outer_r = side / 2
        angle = 90 * 16
        for label, frac, color, amount in self._slices:
            span = max(1, int(frac * 360 * 16))
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor(_WHITE), 2))
            p.drawPie(rect, angle, span)
            start_deg = (angle / 16.0) % 360.0
            span_deg = span / 16.0
            pct = int(round(frac * 100))
            self._hit_slices.append(
                (start_deg, span_deg, f"{label}\n{amount} ({pct}%)")
            )
            angle += span
        inner = side * 0.55
        self._inner_r = inner / 2
        ix = x + (side - inner) / 2
        iy = y + (side - inner) / 2
        p.setBrush(QBrush(QColor(_WHITE)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(ix, iy, inner, inner))
        p.end()


def _donut_legend(slices: list[tuple[str, float, str, str]]) -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: transparent; border: none;")
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(6)
    for label, frac, color, amount in slices:
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
        tip = f"{label}\n{amount} ({pct}%)"
        text = _lbl(f"{label}  {amount} ({pct}%)", size=11, color=_T2)
        text.setToolTip(tip)
        row.addWidget(text)
        row.addStretch()
        vl.addLayout(row)
    return w


class _IconRail(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._key = "main"
        self.setFixedWidth(56)
        self.setStyleSheet(
            f"QWidget {{ background: {_WHITE}; border: 1px solid {_BORDER};"
            f" border-radius: 12px; }}"
        )
        vl = QVBoxLayout(self)
        vl.setContentsMargins(6, 10, 6, 10)
        vl.setSpacing(4)
        self._buttons: dict[str, QToolButton] = {}
        for key, icon, tip in _SUBTABS:
            btn = QToolButton()
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.setAutoRaise(True)
            btn.setFixedSize(40, 40)
            try:
                btn.setIcon(qta.icon(icon, color=_T2))
            except Exception:
                pass
            btn.setIconSize(QSize(26, 26))
            btn.setStyleSheet(
                "QToolButton {"
                "  border: none; border-radius: 8px; background: transparent; padding: 0;"
                "}"
                f"QToolButton:hover {{ background: {_BLUE_L}; }}"
                f"QToolButton:checked {{ background: {_NAVY}; }}"
            )
            btn.clicked.connect(lambda _=False, k=key: self._on_clicked(k))
            self._buttons[key] = btn
            vl.addWidget(btn, alignment=Qt.AlignHCenter)
        vl.addStretch()
        self._sync_icons()

    def _on_clicked(self, key: str) -> None:
        if key == self._key:
            self._buttons[key].setChecked(True)
            return
        self._key = key
        self._sync_icons()
        self.changed.emit(key)

    def _sync_icons(self) -> None:
        for key, icon, _tip in _SUBTABS:
            btn = self._buttons[key]
            checked = key == self._key
            btn.setChecked(checked)
            color = _WHITE if checked else _T2
            try:
                btn.setIcon(qta.icon(icon, color=color))
            except Exception:
                pass


class AnalyticsWidget(QWidget):
    """Spend analytics — KPIs + screenshot-style 3×2 chart grid."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        today = date.today()
        self._year = today.year
        self._period = "year"
        self._month = today.month
        self._chart_currency = "TZS"
        self._loading = False
        self._data: dict = {}
        self._kpi_avg: Optional[_KPICard] = None
        self._kpi_usd: Optional[_KPICard] = None
        self._kpi_tzs: Optional[_KPICard] = None
        self._kpi_zmw: Optional[_KPICard] = None
        self._kpi_cards: list[_KPICard] = []
        self._year_btn: Optional[QPushButton] = None
        self._period_menu: Optional[QMenu] = None
        self._currency_toggles: list[_CurrencyToggle] = []
        self._treemap: Optional[_TreemapWidget] = None
        self._line_chart: Optional[_LineChartWidget] = None
        self._pareto: Optional[_ParetoChartWidget] = None
        self._hbar: Optional[_HBarChartWidget] = None
        self._vbar: Optional[_VBarChartWidget] = None
        self._donut: Optional[_DonutChart] = None
        self._donut_legend_host: Optional[QVBoxLayout] = None
        self._stack: Optional[QStackedWidget] = None
        self.setStyleSheet(
            f"AnalyticsWidget {{ background: {_BG}; border: none; }}"
            "AnalyticsWidget QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        show_widget_loading(self, "Loading analytics…")
        from tahmeed.services.accountant_service import get_analytics_dashboard
        try:
            month = self._month if self._period == "month" else None
            self._data = await get_analytics_dashboard(
                self._year, period=self._period, month=month
            )
            self._apply()
        except Exception:
            pass
        finally:
            self._loading = False
            hide_widget_loading(self)

    def _apply(self) -> None:
        kpis = self._data.get("kpis") or {}
        totals = kpis.get("totals") or {}
        avg = kpis.get("avg_per_day") or {}
        days = int(self._data.get("days") or 1)
        label = str(self._data.get("period_label") or self._year)
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")

        if self._kpi_avg:
            self._kpi_avg.set_value(
                _fmt_currency_short(self._chart_currency, float(avg.get(key) or 0.0))
            )
            self._kpi_avg.set_label(f"Avg / day · {label} ({days}d)")
        if self._kpi_usd:
            self._kpi_usd.set_value(_fmt_currency_short("USD", float(totals.get("usd") or 0)))
            self._kpi_usd.set_label(f"Dollars · {label}")
        if self._kpi_tzs:
            self._kpi_tzs.set_value(_fmt_currency_short("TZS", float(totals.get("tzs") or 0)))
            self._kpi_tzs.set_label(f"TZ · {label}")
        if self._kpi_zmw:
            self._kpi_zmw.set_value(_fmt_currency_short("ZMW", float(totals.get("zmw") or 0)))
            self._kpi_zmw.set_label(f"ZMB · {label}")

        self._refresh_all_charts()
        self._update_period_buttons()

    def _on_currency_changed(self, currency: str) -> None:
        if currency == self._chart_currency:
            return
        self._chart_currency = currency
        for toggle in self._currency_toggles:
            if toggle.currency() != currency:
                toggle.set_currency(currency, emit=False)
        kpis = self._data.get("kpis") or {}
        avg = kpis.get("avg_per_day") or {}
        days = int(self._data.get("days") or 1)
        label = str(self._data.get("period_label") or self._year)
        if self._kpi_avg:
            key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
            self._kpi_avg.set_value(
                _fmt_currency_short(self._chart_currency, float(avg.get(key) or 0.0))
            )
            self._kpi_avg.set_label(f"Avg / day · {label} ({days}d)")
        self._refresh_all_charts()

    def _refresh_all_charts(self) -> None:
        self._refresh_treemap()
        self._refresh_trend()
        self._refresh_pareto()
        self._refresh_feeds()
        self._refresh_suppliers()
        self._refresh_source_mix()

    def _refresh_treemap(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        cats = list(self._data.get("categories") or [])
        items: list[tuple[str, float, str]] = []
        for i, cat in enumerate(cats):
            amt = float(cat.get(key) or 0.0)
            if amt <= 0:
                continue
            name = str(cat.get("name") or "Uncategorised")
            items.append((name, amt, _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)]))
        if self._treemap:
            self._treemap.set_data(items)

    def _refresh_trend(self) -> None:
        trend = list(self._data.get("month_trend") or [])
        prior = list(self._data.get("prior_month_trend") or [])
        today = date.today()
        end_month = today.month if self._year == today.year else 12
        months = list(_MONTH_ABBR[:end_month])
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")

        def _series_raw(rows: list[dict]) -> list[float]:
            out = []
            for m in range(1, end_month + 1):
                row = next((r for r in rows if int(r.get("month") or 0) == m), None)
                out.append(float((row or {}).get(key) or 0.0))
            return out

        cur_raw = _series_raw(trend)
        prior_raw = _series_raw(prior)
        cur_vals, unit = _chart_values(self._chart_currency, cur_raw)
        prior_vals, _ = _chart_values(self._chart_currency, prior_raw)
        series = [
            (str(self._year - 1), _ORANGE, prior_vals),
            (str(self._year), _BLUE, cur_vals),
        ]
        if self._line_chart:
            self._line_chart.set_data(
                months, series, empty="No master spend for this year."
            )

    def _refresh_pareto(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        block = (self._data.get("pareto") or {}).get(key) or {}
        top = block.get("top80") or {}
        bot = block.get("bottom20") or {}
        raw = [float(top.get("amount") or 0.0), float(bot.get("amount") or 0.0)]
        scaled, unit = _chart_values(self._chart_currency, raw)
        if self._pareto:
            self._pareto.set_data(
                scaled[0] if scaled else 0.0,
                scaled[1] if len(scaled) > 1 else 0.0,
                int(top.get("count") or 0),
                int(bot.get("count") or 0),
                unit=unit,
            )

    def _refresh_feeds(self) -> None:
        feeds = list(self._data.get("separate_feeds") or [])
        cur = self._chart_currency
        rows: list[tuple[str, float]] = []
        for feed in feeds:
            if str(feed.get("currency") or "").upper() != cur:
                continue
            label = str(feed.get("label") or feed.get("key") or "")
            rows.append((label, float(feed.get("amount") or 0.0)))
        raw = [v for _, v in rows]
        scaled, _unit = _chart_values(cur, raw)
        scaled_rows = [
            (lab, scaled[i] if i < len(scaled) else 0.0)
            for i, (lab, _) in enumerate(rows)
        ]
        if self._hbar:
            self._hbar.set_data(scaled_rows)

    def _refresh_suppliers(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        suppliers = list(self._data.get("suppliers") or [])
        labels = [str(s.get("name") or "Supplier") for s in suppliers]
        raw = [float(s.get(key) or 0.0) for s in suppliers]
        scaled, _ = _chart_values(self._chart_currency, raw)
        if self._vbar:
            self._vbar.set_data(labels, scaled)

    def _refresh_source_mix(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        mix = self._data.get("source_mix") or {}
        master = float((mix.get("master") or {}).get(key) or 0.0)
        separate = float((mix.get("separate") or {}).get(key) or 0.0)
        total = master + separate
        slices: list[tuple[str, float, str, str]] = []
        if total > 0:
            if separate > 0:
                slices.append((
                    "Separate feeds",
                    separate / total,
                    _RED,
                    _fmt_currency_short(self._chart_currency, separate),
                ))
            if master > 0:
                slices.append((
                    "Master expenses",
                    master / total,
                    _BLUE,
                    _fmt_currency_short(self._chart_currency, master),
                ))
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
                    _lbl(f"No {self._chart_currency} source mix yet.", size=12, color=_TM)
                )

    def _period_chip_text(self) -> str:
        if self._period == "month":
            return f"{_MONTH_ABBR[self._month - 1]} only"
        return "Full year"

    def _update_period_buttons(self) -> None:
        if self._year_btn:
            self._year_btn.setText(f"Year {self._year}")
        chip = self._period_chip_text()
        for card in self._kpi_cards:
            card.set_period_text(chip)

    def _on_year_selected(self, year: int) -> None:
        if year == self._year:
            return
        self._year = year
        self.refresh()

    def _on_period_selected(self, period: str, month: int | None = None) -> None:
        period = "month" if period == "month" else "year"
        if period == self._period and (period == "year" or month == self._month):
            return
        self._period = period
        if period == "month" and month is not None:
            self._month = month
        self.refresh()

    def _popup_period_menu(self) -> None:
        menu = self._period_menu
        if menu is None:
            return
        sender = self.sender()
        anchor = None
        if isinstance(sender, _KPICard):
            anchor = sender.period_button()
        if anchor is None and self._kpi_cards:
            anchor = self._kpi_cards[0].period_button()
        if anchor is None:
            return
        menu.popup(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _on_subtab(self, key: str) -> None:
        if not self._stack:
            return
        # 0 main, 1 trends, 2 entities, 3 trucks, 4 data table, 5 meetings, 6 placeholder
        index_by_key = {
            "main": 0,
            "trends": 1,
            "regions": 2,
            "trucks": 3,
            "table": 4,
            "meeting": 5,
        }
        idx = index_by_key.get(key, 6)
        self._stack.setCurrentIndex(idx)
        if idx in {1, 2, 3, 4, 5}:
            page = self._stack.widget(idx)
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def _make_currency_toggle(self) -> _CurrencyToggle:
        toggle = _CurrencyToggle()
        toggle.changed.connect(self._on_currency_changed)
        self._currency_toggles.append(toggle)
        return toggle

    def _chart_shell(self, title: str, body: QWidget, *, with_currency: bool = True) -> QFrame:
        card = _card()
        card.setMinimumHeight(280)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(14, 12, 14, 12)
        vl.setSpacing(6)
        header = QHBoxLayout()
        header.addWidget(_chart_title(title))
        header.addStretch()
        if with_currency:
            header.addWidget(self._make_currency_toggle())
        vl.addLayout(header)
        set_title = getattr(body, "set_chart_title", None)
        if callable(set_title):
            set_title(title)
        vl.addWidget(body, 1)
        return card

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        rail = _IconRail()
        rail.changed.connect(self._on_subtab)
        root.addWidget(rail)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_main())  # 0
        from tahmeed.ui.accountant.analytics_trends import AnalyticsTrendsPage
        from tahmeed.ui.accountant.analytics_entities import AnalyticsEntitiesPage
        from tahmeed.ui.accountant.analytics_trucks import AnalyticsTrucksPage
        from tahmeed.ui.accountant.analytics_data_table import AnalyticsDataTablePage
        from tahmeed.ui.accountant.analytics_meetings import AnalyticsMeetingsPage
        self._stack.addWidget(AnalyticsTrendsPage())  # 1 — Trends
        self._stack.addWidget(AnalyticsEntitiesPage())  # 2 — Entities (earth)
        self._stack.addWidget(AnalyticsTrucksPage())  # 3 — Trucks
        self._stack.addWidget(AnalyticsDataTablePage())  # 4 — Data table
        self._stack.addWidget(AnalyticsMeetingsPage())  # 5 — Meetings
        self._stack.addWidget(self._build_placeholder())  # 6 — other icons
        root.addWidget(self._stack, 1)
        self.refresh()

    def _build_placeholder(self) -> QWidget:
        page = QFrame()
        page.setObjectName("analyticsCard")
        page.setStyleSheet(
            "QFrame#analyticsCard {"
            "  background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px;"
            "}"
        )
        vl = QVBoxLayout(page)
        vl.setContentsMargins(40, 40, 40, 40)
        vl.addStretch()
        title = _lbl("Coming soon", size=20, weight=700)
        title.setAlignment(Qt.AlignCenter)
        vl.addWidget(title)
        body = _lbl(
            "This Analytics sub-view is a placeholder. "
            "Use the top icon for the live spend overview.",
            size=13,
            color=_T2,
            wrap=True,
        )
        body.setAlignment(Qt.AlignCenter)
        vl.addWidget(body)
        vl.addStretch()
        return page

    def _build_main(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {_BG}; border: none; }}"
            "QScrollBar:vertical { background: #F1F5F9; width: 6px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #CBD5E1; border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        content = OverviewGeometryContent()
        vl = QVBoxLayout(content)
        vl.setContentsMargins(8, 4, 12, 12)
        vl.setSpacing(14)

        header = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(_lbl("Analytics", size=22, weight=700))
        left.addWidget(
            _lbl(
                "All-expense category mix, trends, suppliers, feeds, and source split.",
                size=13,
                color=_T2,
            )
        )
        header.addLayout(left)
        header.addStretch()

        self._year_btn = QPushButton(f"Year {self._year}")
        self._year_btn.setCursor(Qt.PointingHandCursor)
        self._year_btn.setFixedHeight(34)
        self._year_btn.setStyleSheet(self._menu_btn_style())
        year_menu = QMenu(self._year_btn)
        year_menu.setStyleSheet(self._menu_style())
        current_yr = date.today().year
        for yr in range(current_yr - 3, current_yr + 2):
            action = year_menu.addAction(f"Year {yr}")
            action.setData(yr)
        self._year_btn.setMenu(year_menu)
        year_menu.triggered.connect(lambda a: self._on_year_selected(int(a.data())))
        header.addWidget(self._year_btn)
        vl.addLayout(header)

        self._period_menu = QMenu(self)
        self._period_menu.setStyleSheet(self._menu_style())
        full = self._period_menu.addAction("Full year")
        full.setData(("year", None))
        self._period_menu.addSeparator()
        for m in range(1, 13):
            act = self._period_menu.addAction(f"{_MONTH_ABBR[m - 1]} only")
            act.setData(("month", m))
        self._period_menu.triggered.connect(
            lambda a: self._on_period_selected(a.data()[0], a.data()[1])
        )

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self._kpi_avg = _KPICard("mdi.calendar-clock", _BLUE, _BLUE_L, "—", "Avg / day")
        self._kpi_usd = _KPICard("mdi.currency-usd", _GREEN, _GREEN_L, "—", "Dollars")
        self._kpi_tzs = _KPICard("mdi.cash", _NAVY, "#E8EEF4", "—", "TZ")
        self._kpi_zmw = _KPICard("mdi.cash-multiple", _AMBER, _AMBER_L, "—", "ZMB")
        self._kpi_cards = [self._kpi_avg, self._kpi_usd, self._kpi_tzs, self._kpi_zmw]
        for card in self._kpi_cards:
            card.period_menu_requested.connect(self._popup_period_menu)
            kpi_row.addWidget(card)
        self._update_period_buttons()
        vl.addLayout(kpi_row)

        # Row 1 — Category treemap | Spend trend | Pareto
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        self._treemap = _TreemapWidget()
        row1.addWidget(self._chart_shell("Spend by Category", self._treemap), 1)
        self._line_chart = _LineChartWidget()
        row1.addWidget(self._chart_shell("Spend Trend", self._line_chart), 1)
        self._pareto = _ParetoChartWidget()
        row1.addWidget(self._chart_shell("Pareto Analysis", self._pareto), 1)
        vl.addLayout(row1)

        # Row 2 — Separate feeds | Suppliers | Master vs Separate donut
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        self._hbar = _HBarChartWidget()
        row2.addWidget(self._chart_shell("Spend by Separate Feed", self._hbar), 1)
        self._vbar = _VBarChartWidget()
        row2.addWidget(self._chart_shell("Spend by Supplier", self._vbar), 1)

        donut_card = _card()
        donut_card.setMinimumHeight(280)
        donut_vl = QVBoxLayout(donut_card)
        donut_vl.setContentsMargins(14, 12, 14, 12)
        donut_vl.setSpacing(6)
        donut_header = QHBoxLayout()
        donut_header.addWidget(_chart_title("Spend by Source"))
        donut_header.addStretch()
        donut_header.addWidget(self._make_currency_toggle())
        donut_vl.addLayout(donut_header)
        self._donut = _DonutChart()
        donut_vl.addWidget(self._donut, 1, Qt.AlignHCenter)
        legend_wrap = QWidget()
        legend_wrap.setStyleSheet("background: transparent; border: none;")
        self._donut_legend_host = QVBoxLayout(legend_wrap)
        self._donut_legend_host.setContentsMargins(0, 0, 0, 0)
        donut_vl.addWidget(legend_wrap)
        row2.addWidget(donut_card, 1)
        vl.addLayout(row2)

        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _menu_btn_style() -> str:
        return f"""
            QPushButton {{
                background: {_WHITE};
                border: 1px solid {_BORDER};
                border-radius: 8px;
                color: {_T1};
                font-size: 13px;
                font-weight: 600;
                font-family: 'Segoe UI';
                padding: 0 14px;
            }}
            QPushButton:hover {{ background: #F8FAFC; }}
            QPushButton::menu-indicator {{ image: none; width: 0; }}
        """

    @staticmethod
    def _menu_style() -> str:
        return (
            "QMenu { background: #FFFFFF; border: 1px solid #E5E7EB; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; font-family: 'Segoe UI'; }"
            "QMenu::item:selected { background: #E8F4FD; }"
        )
