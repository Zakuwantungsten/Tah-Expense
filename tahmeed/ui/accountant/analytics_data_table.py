"""Analytics → Data table: source spreadsheet + compact year Spend Trend."""

from __future__ import annotations

from calendar import month_abbr
from datetime import date
from typing import Optional

import math

from PySide6.QtCore import Qt, QDate, QPointF, QPoint, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDateEdit, QPushButton, QBoxLayout, QSizePolicy,
    QButtonGroup, QStackedWidget,
)

from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.overview_geometry import OverviewGeometryContent
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.accountant.chart_interactions import ChartInteractionMixin
from tahmeed.ui.accountant.analytics import (
    _BG, _WHITE, _BORDER, _NAVY, _BLUE, _T1, _T2, _AMBER, _TM, _TITLE,
    _ORANGE, _GREEN, _TREEMAP_COLORS,
    _lbl, _card, _DonutChart, _toggle_style, _nice_max, _fmt_tick,
)

_COLS = ("Source", "Group", "TZS", "USD", "ZMW", "Entries")
_MIN_FILTER_DATE = QDate(2000, 1, 1)
_SPLIT_BREAKPOINT = 1000
_TREND_CARD_H = 318
_DIST_CARD_H_CONVERTED = 236
_DIST_CARD_H_RAW = 360
_DIST_TOP_N = 5
_DIST_TOP_N_RAW = 3
_FALLBACK_RATES = {"TZS": 1.0, "USD": 2600.0, "ZMW": 130.0}
_SERIES_META = (
    ("TZS", _BLUE),
    ("USD", _ORANGE),
    ("ZMW", _GREEN),
)
_MONTH_SHORT = tuple(month_abbr[1:])


def _fmt_money(amount: float) -> str:
    return f"{float(amount or 0.0):,.2f}"


def _fmt_point(currency: str, amount: float) -> str:
    """Compact money label for charts."""
    abs_amt = abs(float(amount or 0.0))
    cur = (currency or "TZS").upper()
    if abs_amt <= 0:
        return ""
    if cur == "USD":
        if abs_amt >= 1_000_000:
            return f"${abs_amt / 1_000_000:.2f}M"
        if abs_amt >= 1_000:
            return f"${abs_amt / 1_000:.1f}K"
        return f"${abs_amt:,.0f}"
    if abs_amt >= 1_000_000:
        return f"{abs_amt / 1_000_000:.2f}M"
    if abs_amt >= 1_000:
        return f"{abs_amt / 1_000:.1f}K"
    return f"{abs_amt:,.0f}"


def _row_to_tzs(row: dict, rates: dict[str, float]) -> float:
    """Convert a source row's TZS+USD+ZMW into one TZS total."""
    r = rates or _FALLBACK_RATES
    return (
        abs(float(row.get("tzs") or 0.0)) * float(r.get("TZS") or 1.0)
        + abs(float(row.get("usd") or 0.0)) * float(r.get("USD") or 0.0)
        + abs(float(row.get("zmw") or 0.0)) * float(r.get("ZMW") or 0.0)
    )


def _slices_from_scored(
    scored: list[tuple[str, float]],
    *,
    currency: str,
    top_n: int,
) -> list[tuple[str, float, str, str]]:
    scored = sorted(scored, key=lambda item: item[1], reverse=True)
    if not scored:
        return []
    top = scored[:top_n]
    rest = scored[top_n:]
    if rest:
        top.append(("Other", sum(amt for _, amt in rest)))
    total = sum(amt for _, amt in top) or 1.0
    slices: list[tuple[str, float, str, str]] = []
    for i, (label, amt) in enumerate(top):
        color = _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)]
        slices.append(
            (label, amt / total, color, _fmt_point(currency, amt) or "0")
        )
    return slices


def _build_converted_slices(
    sources: list[dict],
    rates: dict[str, float],
) -> list[tuple[str, float, str, str]]:
    scored: list[tuple[str, float]] = []
    for row in sources or []:
        amt = _row_to_tzs(row, rates)
        if amt <= 0:
            continue
        label = str(row.get("label") or row.get("key") or "—").strip() or "—"
        scored.append((label, amt))
    return _slices_from_scored(scored, currency="TZS", top_n=_DIST_TOP_N)


def _build_raw_slices(
    sources: list[dict],
    currency: str,
) -> list[tuple[str, float, str, str]]:
    key = (currency or "TZS").lower()
    scored: list[tuple[str, float]] = []
    for row in sources or []:
        amt = abs(float(row.get(key) or 0.0))
        if amt <= 0:
            continue
        label = str(row.get("label") or row.get("key") or "—").strip() or "—"
        scored.append((label, amt))
    return _slices_from_scored(scored, currency=currency, top_n=_DIST_TOP_N_RAW)


def _distribution_legend(
    slices: list[tuple[str, float, str, str]],
    *,
    compact: bool = False,
) -> QWidget:
    host = QWidget()
    host.setStyleSheet("background: transparent; border: none;")
    vl = QVBoxLayout(host)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(4 if compact else 5)
    size = 9 if compact else 10
    for label, frac, color, amount in slices:
        row = QHBoxLayout()
        row.setSpacing(6)
        dot = QLabel("●")
        dot.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: {size}px;"
            f" background: transparent; border: none; }}"
        )
        dot.setFixedWidth(10)
        row.addWidget(dot)
        pct = int(round(frac * 100))
        short = label if len(label) <= 16 else label[:15] + "…"
        tip = f"{label}\n{amount} ({pct}%)"
        text = _lbl(f"{short}  {amount}  ({pct}%)", size=size, color=_T2)
        text.setToolTip(tip)
        row.addWidget(text, 1)
        vl.addLayout(row)
    vl.addStretch(1)
    return host


class _DistModeToggle(QWidget):
    """Converted (all → TZS) vs Raw (native currencies)."""

    changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "converted"
        self.setStyleSheet("background: transparent; border: none;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for mode, label in (("converted", "Converted"), ("raw", "Raw")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setChecked(mode == "converted")
            btn.setStyleSheet(_toggle_style(mode == "converted"))
            btn.clicked.connect(lambda _=False, m=mode: self._on_clicked(m))
            self._group.addButton(btn)
            self._buttons[mode] = btn
            row.addWidget(btn)

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, *, emit: bool = True) -> None:
        m = mode if mode in {"converted", "raw"} else "converted"
        if m == self._mode:
            return
        self._mode = m
        for code, btn in self._buttons.items():
            btn.setChecked(code == m)
            btn.setStyleSheet(_toggle_style(code == m))
        if emit:
            self.changed.emit(m)

    def _on_clicked(self, mode: str) -> None:
        self.set_mode(mode)


class _CurrencySpendTrendChart(ChartInteractionMixin, QWidget):
    """Compact Jan–Dec multi-currency line chart (reference-sheet style)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._year = 0
        self._labels: list[str] = list(_MONTH_SHORT)
        self._series: list[tuple[str, str, list[float]]] = []
        self._empty = "No spend for this year."
        self._hit_points: list[tuple[QPointF, str, str, float]] = []
        self.setFixedHeight(248)
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent; border: none;")
        self._enable_chart_interactions(title="Spend Trend")

    def set_data(
        self,
        *,
        year: int,
        series: list[tuple[str, str, list[float]]],
        empty: str | None = None,
    ) -> None:
        self._year = int(year or 0)
        self._labels = list(_MONTH_SHORT)
        self._series = []
        for name, color, values in series or []:
            padded = list(values or [])[:12]
            while len(padded) < 12:
                padded.append(0.0)
            self._series.append((str(name), str(color), padded))
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
                best = f"{series_name} · {label}\n{_fmt_point(series_name, value) or _fmt_tick(value)}"
        return best

    def chart_full_view_factory(self) -> QWidget:
        clone = _CurrencySpendTrendChart()
        clone.setFixedHeight(520)
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(year=self._year, series=list(self._series), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        self._hit_points = []

        # Margins: room for y-ticks, angled month labels, top legend
        pl, pr, pt, pb = 44, 14, 28, 42
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        labels = self._labels
        n = 12

        # Legend under title area (drawn at top of widget)
        lx = pl
        p.setFont(QFont("Segoe UI", 8))
        for name, color, _ in self._series:
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawEllipse(QPointF(lx + 4, 12), 4.0, 4.0)
            p.setPen(QColor(_T2))
            p.drawText(int(lx + 12), 16, name)
            lx += 12 + p.fontMetrics().horizontalAdvance(name) + 14

        # Per-series scale so TZS / USD / ZMW shapes stay visible together
        peaks = []
        for _, _, values in self._series:
            peaks.append(max((float(v) for v in values), default=0.0) or 0.0)
        has_values = any(peak > 0 for peak in peaks)

        # Soft horizontal grid (relative 0–100)
        p.setFont(QFont("Segoe UI", 7))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.setPen(QPen(QColor("#EEF2F7"), 1))
            p.drawLine(pl, y, pl + w, y)
            p.setPen(QColor(_TM))
            p.drawText(4, y + 3, f"{int(i * 25)}")

        if not self._series or not has_values:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(pl, pt, w, h), Qt.AlignCenter, self._empty)
            p.end()
            return

        def _x_at(i: int) -> float:
            if n == 1:
                return pl + w / 2
            return pl + (i / (n - 1)) * w

        def _y_at(value: float, peak: float) -> float:
            if peak <= 0:
                return pt + h
            return pt + h - (max(value, 0.0) / peak) * h

        # Lines + dots
        for (name, color, values), peak in zip(self._series, peaks):
            if peak <= 0:
                continue
            path = QPainterPath()
            points: list[QPointF] = []
            for i in range(n):
                point = QPointF(_x_at(i), _y_at(values[i], peak))
                points.append(point)
                month = labels[i] if i < len(labels) else str(i + 1)
                self._hit_points.append((point, name, month, float(values[i])))
                if i == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            pen = QPen(QColor(color), 2.0)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor(_WHITE), 1.2))
            for point in points:
                p.drawEllipse(point, 3.2, 3.2)

        # Point value labels (skip zeros; alternate above/below by series)
        p.setFont(QFont("Segoe UI", 6))
        for s_idx, ((name, color, values), peak) in enumerate(
            zip(self._series, peaks)
        ):
            if peak <= 0:
                continue
            p.setPen(QColor(_T2))
            for i, value in enumerate(values):
                if value <= 0:
                    continue
                text = _fmt_point(name, value)
                if not text:
                    continue
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(text)
                x = _x_at(i) - tw / 2
                y = _y_at(value, peak)
                # Prefer above for first series, below for others
                if s_idx == 0:
                    ty = y - 6
                else:
                    ty = y + 11 + (s_idx - 1) * 9
                p.drawText(QPointF(x, ty), text)

        # Angled month labels
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor(_T2))
        for i, label in enumerate(labels):
            cx = _x_at(i)
            cy = pt + h + 10
            p.save()
            p.translate(cx, cy)
            p.rotate(-40)
            p.drawText(0, 0, label)
            p.restore()

        p.end()


class _ResponsiveSplitHost(QWidget):
    """Table | compact chart: side-by-side when wide; stacked when narrow."""

    def __init__(self, left: QWidget, right: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._breakpoint = _SPLIT_BREAKPOINT
        self._wrapped = False
        self._right = right
        self.setStyleSheet("background: transparent; border: none;")
        self._layout = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)
        left.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Chart keeps its own height — never stretch to match the table
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._layout.addWidget(left, 3)
        self._layout.addWidget(right, 2, Qt.AlignTop)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        wrapped = self.width() < self._breakpoint
        if wrapped == self._wrapped:
            return
        self._wrapped = wrapped
        self._layout.setDirection(
            QBoxLayout.TopToBottom if wrapped else QBoxLayout.LeftToRight
        )
        self._layout.setSpacing(14)
        # Re-apply top alignment for the chart column when side-by-side
        self._layout.setAlignment(
            self._right, Qt.AlignTop if not wrapped else Qt.Alignment()
        )


class AnalyticsDataTablePage(QWidget):
    """Company-wide source monetary summary for a date range."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        today = date.today()
        self._date_from = date(today.year, 1, 1)
        self._date_to = today
        self._loading = False
        self._data: dict = {}

        self._subtitle: Optional[QLabel] = None
        self._status_lbl: Optional[QLabel] = None
        self._chart_year_lbl: Optional[QLabel] = None
        self._dist_mode_lbl: Optional[QLabel] = None
        self._dist_card: Optional[QFrame] = None
        self._dist_stack: Optional[QStackedWidget] = None
        self._dist_mode_toggle: Optional[_DistModeToggle] = None
        self._table: Optional[QTableWidget] = None
        self._line_chart: Optional[_CurrencySpendTrendChart] = None
        self._donut: Optional[_DonutChart] = None
        self._dist_legend_host: Optional[QVBoxLayout] = None
        self._raw_donuts: dict[str, _DonutChart] = {}
        self._raw_legend_hosts: dict[str, QVBoxLayout] = {}
        self._from_date: Optional[QDateEdit] = None
        self._to_date: Optional[QDateEdit] = None
        self._apply_btn: Optional[QPushButton] = None
        self._dist_mode = "converted"

        self.setStyleSheet(
            f"AnalyticsDataTablePage {{ background: {_BG}; border: none; }}"
            "AnalyticsDataTablePage QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        show_widget_loading(self, "Loading monetary sources…")
        from tahmeed.services.accountant_service import get_analytics_data_table
        try:
            self._data = await get_analytics_data_table(
                date_from=self._date_from.isoformat(),
                date_to=self._date_to.isoformat(),
            )
            self._apply()
        except Exception as exc:
            self._data = {
                "sources": [],
                "totals": {},
                "month_trend": [],
                "chart_year": self._date_to.year,
                "rates_to_tzs": dict(_FALLBACK_RATES),
                "fx_source": "fallback",
                "message": f"Could not load data table.\n{exc}",
            }
            self._apply()
        finally:
            self._loading = False
            hide_widget_loading(self)

    def _apply(self) -> None:
        sources = list(self._data.get("sources") or [])
        if self._subtitle:
            label = str(self._data.get("period_label") or "")
            n = int(self._data.get("source_count") or len(sources))
            self._subtitle.setText(
                f"All monetary tabs · {label or '—'} — {n} sources · "
                "Diesel Cash counted inside Master · deposits excluded."
            )
        chart_year = int(self._data.get("chart_year") or self._date_to.year)
        if self._chart_year_lbl:
            self._chart_year_lbl.setText(f"{chart_year} · Jan – Dec")
        self._fill_chart(chart_year, list(self._data.get("month_trend") or []))
        if not sources:
            msg = str(self._data.get("message") or "No monetary spend in this range.")
            if self._status_lbl:
                self._status_lbl.setText(msg)
                self._status_lbl.show()
            self._fill_table([], {})
            self._refresh_distribution()
            return
        if self._status_lbl:
            self._status_lbl.hide()
        self._fill_table(sources, dict(self._data.get("totals") or {}))
        self._refresh_distribution()

    def _on_dist_mode(self, mode: str) -> None:
        self._dist_mode = mode if mode in {"converted", "raw"} else "converted"
        self._refresh_distribution()

    def _rates(self) -> dict[str, float]:
        rates = dict(self._data.get("rates_to_tzs") or {})
        return {
            "TZS": float(rates.get("TZS") or _FALLBACK_RATES["TZS"]),
            "USD": float(rates.get("USD") or _FALLBACK_RATES["USD"]),
            "ZMW": float(rates.get("ZMW") or _FALLBACK_RATES["ZMW"]),
        }

    def _set_legend(self, host: Optional[QVBoxLayout], slices: list) -> None:
        if not host:
            return
        while host.count():
            item = host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if slices:
            host.addWidget(
                _distribution_legend(slices, compact=self._dist_mode == "raw")
            )
        else:
            host.addWidget(_lbl("No spend.", size=9, color=_TM))

    def _refresh_distribution(self) -> None:
        sources = list(self._data.get("sources") or [])
        converted = self._dist_mode != "raw"
        if self._dist_stack:
            self._dist_stack.setCurrentIndex(0 if converted else 1)
        if self._dist_card:
            self._dist_card.setFixedHeight(
                _DIST_CARD_H_CONVERTED if converted else _DIST_CARD_H_RAW
            )
        fx_src = str(self._data.get("fx_source") or "fallback")
        if self._dist_mode_lbl:
            if converted:
                tip = "live FX" if fx_src == "live" else "fallback FX"
                self._dist_mode_lbl.setText(f"All → TZS · {tip}")
            else:
                self._dist_mode_lbl.setText("Native TZS · USD · ZMW")

        if converted:
            slices = _build_converted_slices(sources, self._rates())
            if self._donut:
                self._donut.set_data(slices, empty="No expense distribution.")
            self._set_legend(self._dist_legend_host, slices)
            return

        for cur in ("TZS", "USD", "ZMW"):
            slices = _build_raw_slices(sources, cur)
            donut = self._raw_donuts.get(cur)
            if donut:
                donut.set_data(slices, empty=f"No {cur}")
            self._set_legend(self._raw_legend_hosts.get(cur), slices)

    def _fill_chart(self, year: int, month_trend: list[dict]) -> None:
        if not self._line_chart:
            return
        by_month = {int(row.get("month") or 0): row for row in month_trend}
        series = []
        for name, color in _SERIES_META:
            key = name.lower()
            values = [
                float((by_month.get(m) or {}).get(key) or 0.0) for m in range(1, 13)
            ]
            series.append((name, color, values))
        self._line_chart.set_data(
            year=year,
            series=series,
            empty="No spend for this year.",
        )

    def _fill_table(self, sources: list[dict], totals: dict) -> None:
        if not self._table:
            return
        rows = list(sources)
        self._table.setRowCount(len(rows) + (1 if rows else 0))
        bold = QFont("Segoe UI", 12)
        bold.setBold(True)
        for i, row in enumerate(rows):
            zebra = QColor("#F8FAFC") if i % 2 else QColor(_WHITE)
            cells = [
                str(row.get("label") or "—"),
                str(row.get("group") or "—"),
                _fmt_money(float(row.get("tzs") or 0.0)),
                _fmt_money(float(row.get("usd") or 0.0)),
                _fmt_money(float(row.get("zmw") or 0.0)),
                f"{int(row.get('count') or 0):,}",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setBackground(zebra)
                item.setForeground(QColor(_T1))
                if col <= 1:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                else:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
                self._table.setItem(i, col, item)
        if rows:
            trow = len(rows)
            for col, text in enumerate(
                [
                    f"Total ({len(rows)})",
                    "",
                    _fmt_money(float(totals.get("tzs") or 0.0)),
                    _fmt_money(float(totals.get("usd") or 0.0)),
                    _fmt_money(float(totals.get("zmw") or 0.0)),
                    f"{int(totals.get('count') or 0):,}",
                ]
            ):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                item.setFont(bold)
                item.setBackground(QColor("#E8EEF4"))
                item.setForeground(QColor(_NAVY))
                item.setTextAlignment(
                    Qt.AlignVCenter | (Qt.AlignLeft if col <= 1 else Qt.AlignRight)
                )
                self._table.setItem(trow, col, item)

    def _on_apply_dates(self) -> None:
        if not self._from_date or not self._to_date:
            return
        fd = self._from_date.date()
        td = self._to_date.date()
        if fd < _MIN_FILTER_DATE:
            fd = _MIN_FILTER_DATE
        if td < fd:
            fd, td = td, fd
            self._from_date.blockSignals(True)
            self._to_date.blockSignals(True)
            self._from_date.setDate(fd)
            self._to_date.setDate(td)
            self._from_date.blockSignals(False)
            self._to_date.blockSignals(False)
        self._date_from = date(fd.year(), fd.month(), fd.day())
        self._date_to = date(td.year(), td.month(), td.day())
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

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
        left.addWidget(_lbl("Data table", size=22, weight=700))
        self._subtitle = _lbl(
            "Monetary totals by source tab for the selected date range.",
            size=13,
            color=_T2,
        )
        left.addWidget(self._subtitle)
        header.addLayout(left, 1)
        vl.addLayout(header)

        filter_bar = QWidget()
        filter_bar.setStyleSheet("background: transparent; border: none;")
        filter_row = QHBoxLayout(filter_bar)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        filter_row.addWidget(_lbl("From", size=12, color=_T2))
        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDisplayFormat("dd MMM yyyy")
        self._from_date.setMinimumDate(_MIN_FILTER_DATE)
        self._from_date.setDate(
            QDate(self._date_from.year, self._date_from.month, self._date_from.day)
        )
        self._from_date.setFixedWidth(130)
        self._from_date.setFixedHeight(34)
        self._from_date.setStyleSheet(self._date_input_style())
        style_calendar_popup(self._from_date)
        filter_row.addWidget(self._from_date)

        filter_row.addWidget(_lbl("To", size=12, color=_T2))
        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDisplayFormat("dd MMM yyyy")
        self._to_date.setMinimumDate(_MIN_FILTER_DATE)
        self._to_date.setDate(
            QDate(self._date_to.year, self._date_to.month, self._date_to.day)
        )
        self._to_date.setFixedWidth(130)
        self._to_date.setFixedHeight(34)
        self._to_date.setStyleSheet(self._date_input_style())
        style_calendar_popup(self._to_date)
        filter_row.addWidget(self._to_date)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.setFixedHeight(34)
        self._apply_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: {_BLUE}; color: #FFFFFF; border: none; border-radius: 8px;"
            f"  padding: 0 16px; font-family: 'Segoe UI'; font-size: 13px; font-weight: 600;"
            f"}}"
            "QPushButton:hover { background: #0066A8; }"
        )
        self._apply_btn.clicked.connect(self._on_apply_dates)
        filter_row.addWidget(self._apply_btn)
        filter_row.addStretch(1)
        vl.addWidget(filter_bar)

        self._status_lbl = _lbl("", size=12, color=_AMBER, wrap=True)
        self._status_lbl.hide()
        vl.addWidget(self._status_lbl)

        table_card = _card()
        table_vl = QVBoxLayout(table_card)
        table_vl.setContentsMargins(12, 12, 12, 12)
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(list(_COLS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setFocusPolicy(Qt.StrongFocus)
        self._table.setMinimumHeight(320)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for col in range(2, len(_COLS)):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        self._table.setStyleSheet(
            f"QTableWidget {{"
            f"  background: {_WHITE}; border: none; font-family: 'Segoe UI';"
            f"  font-size: 12px; color: {_T1}; gridline-color: transparent;"
            f"}}"
            f"QHeaderView::section {{"
            f"  background: #F8FAFC; color: {_T2}; border: none;"
            f"  border-bottom: 1px solid {_BORDER}; padding: 8px 10px;"
            f"  font-weight: 600; font-size: 11px;"
            f"}}"
            f"QTableWidget::item {{ padding: 8px 10px; }}"
            f"QTableWidget::item:selected {{ background: #E8F4FD; color: {_NAVY}; }}"
        )
        table_vl.addWidget(self._table, 1)

        charts_col = QWidget()
        charts_col.setStyleSheet("background: transparent; border: none;")
        charts_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        charts_vl = QVBoxLayout(charts_col)
        charts_vl.setContentsMargins(0, 0, 0, 0)
        charts_vl.setSpacing(12)

        trend_card = _card()
        trend_card.setFixedHeight(_TREND_CARD_H)
        trend_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        trend_vl = QVBoxLayout(trend_card)
        trend_vl.setContentsMargins(14, 12, 14, 10)
        trend_vl.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_lbl("Spend Trend", size=13, weight=600, color=_TITLE))
        title_row.addStretch(1)
        self._chart_year_lbl = _lbl("", size=11, color=_T2)
        title_row.addWidget(self._chart_year_lbl)
        trend_vl.addLayout(title_row)
        self._line_chart = _CurrencySpendTrendChart()
        self._line_chart.set_chart_title("Spend Trend")
        trend_vl.addWidget(self._line_chart, 0)
        charts_vl.addWidget(trend_card)

        self._dist_card = _card()
        self._dist_card.setFixedHeight(_DIST_CARD_H_CONVERTED)
        self._dist_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dist_vl = QVBoxLayout(self._dist_card)
        dist_vl.setContentsMargins(14, 12, 14, 12)
        dist_vl.setSpacing(6)
        dist_header = QHBoxLayout()
        dist_header.setContentsMargins(0, 0, 0, 0)
        dist_header.setSpacing(8)
        dist_header.addWidget(
            _lbl("Expense distribution", size=13, weight=600, color=_TITLE)
        )
        dist_header.addStretch(1)
        self._dist_mode_lbl = _lbl("All → TZS", size=10, color=_T2)
        dist_header.addWidget(self._dist_mode_lbl)
        self._dist_mode_toggle = _DistModeToggle()
        self._dist_mode_toggle.changed.connect(self._on_dist_mode)
        dist_header.addWidget(self._dist_mode_toggle)
        dist_vl.addLayout(dist_header)

        self._dist_stack = QStackedWidget()
        self._dist_stack.setStyleSheet("background: transparent; border: none;")

        # Page 0 — one converted TZS donut
        converted_page = QWidget()
        converted_page.setStyleSheet("background: transparent; border: none;")
        converted_row = QHBoxLayout(converted_page)
        converted_row.setContentsMargins(0, 0, 0, 0)
        converted_row.setSpacing(10)
        self._donut = _DonutChart()
        self._donut.set_chart_title("Expense distribution")
        self._donut.setFixedSize(132, 132)
        converted_row.addWidget(self._donut, 0, Qt.AlignVCenter)
        legend_wrap = QWidget()
        legend_wrap.setStyleSheet("background: transparent; border: none;")
        self._dist_legend_host = QVBoxLayout(legend_wrap)
        self._dist_legend_host.setContentsMargins(0, 0, 0, 0)
        self._dist_legend_host.setSpacing(0)
        converted_row.addWidget(legend_wrap, 1)
        self._dist_stack.addWidget(converted_page)

        # Page 1 — three raw currency distributions
        raw_page = QWidget()
        raw_page.setStyleSheet("background: transparent; border: none;")
        raw_vl = QVBoxLayout(raw_page)
        raw_vl.setContentsMargins(0, 0, 0, 0)
        raw_vl.setSpacing(8)
        self._raw_donuts = {}
        self._raw_legend_hosts = {}
        for cur, color in _SERIES_META:
            row_host = QWidget()
            row_host.setStyleSheet("background: transparent; border: none;")
            row = QHBoxLayout(row_host)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            col.addWidget(_lbl(cur, size=10, weight=600, color=color))
            donut = _DonutChart()
            donut.set_chart_title(f"Expense distribution · {cur}")
            donut.setFixedSize(72, 72)
            col.addWidget(donut, 0, Qt.AlignLeft)
            row.addLayout(col, 0)
            raw_legend = QWidget()
            raw_legend.setStyleSheet("background: transparent; border: none;")
            legend_host = QVBoxLayout(raw_legend)
            legend_host.setContentsMargins(0, 0, 0, 0)
            legend_host.setSpacing(0)
            row.addWidget(raw_legend, 1)
            self._raw_donuts[cur] = donut
            self._raw_legend_hosts[cur] = legend_host
            raw_vl.addWidget(row_host)
        self._dist_stack.addWidget(raw_page)

        dist_vl.addWidget(self._dist_stack, 1)
        charts_vl.addWidget(self._dist_card)

        split = _ResponsiveSplitHost(table_card, charts_col)
        vl.addWidget(split, 1)

        scroll.setWidget(content)
        root.addWidget(scroll)

    @staticmethod
    def _date_input_style() -> str:
        return (
            f"QDateEdit {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 10px; font-family: 'Segoe UI'; font-size: 13px; color: {_T1};"
            f"}}"
            f"QDateEdit:focus {{ border-color: {_BLUE}; }}"
            "QDateEdit::drop-down { border: none; width: 22px; }"
        )
