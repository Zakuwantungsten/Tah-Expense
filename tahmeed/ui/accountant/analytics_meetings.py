"""Analytics → Meetings: Master App by / Ownership accountability."""

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
    QButtonGroup, QStackedWidget, QComboBox,
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

_COLS = ("S/N", "Person", "Entries", "TZS", "USD", "ZMW", "Compliance")
_MIN_FILTER_DATE = QDate(2000, 1, 1)
_SPLIT_BREAKPOINT = 1000
_TREND_CARD_H = 318
_DIST_CARD_H_CONVERTED = 236
_DIST_CARD_H_RAW = 360
_DIST_TOP_N = 5
_DIST_TOP_N_RAW = 3
_FALLBACK_RATES = {"TZS": 1.0, "USD": 2600.0, "ZMW": 130.0}
_MONTH_SHORT = tuple(month_abbr[1:])
_SERIES_META = (("TZS", _BLUE), ("USD", _ORANGE), ("ZMW", _GREEN))


def _fmt_money(amount: float) -> str:
    return f"{float(amount or 0.0):,.2f}"


def _fmt_point(currency: str, amount: float) -> str:
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


def _fmt_compliance(pct: float | None) -> str:
    if pct is None:
        return "—"
    return f"{pct * 100:.0f}%"


def _row_to_tzs(row: dict, rates: dict[str, float]) -> float:
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
    out: list[tuple[str, float, str, str]] = []
    for i, (label, amt) in enumerate(top):
        color = _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)]
        out.append((label, amt / total, color, _fmt_point(currency, amt) or "0"))
    return out


def _build_converted_slices(
    people: list[dict], rates: dict[str, float]
) -> list[tuple[str, float, str, str]]:
    scored = []
    for row in people or []:
        amt = _row_to_tzs(row, rates)
        if amt <= 0:
            continue
        scored.append((str(row.get("name") or "—"), amt))
    return _slices_from_scored(scored, currency="TZS", top_n=_DIST_TOP_N)


def _build_raw_slices(
    people: list[dict], currency: str
) -> list[tuple[str, float, str, str]]:
    key = (currency or "TZS").lower()
    scored = []
    for row in people or []:
        amt = abs(float(row.get(key) or 0.0))
        if amt <= 0:
            continue
        scored.append((str(row.get("name") or "—"), amt))
    return _slices_from_scored(scored, currency=currency, top_n=_DIST_TOP_N_RAW)


def _distribution_legend(
    slices: list[tuple[str, float, str, str]], *, compact: bool = False
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


class _PersonSpendTrendChart(ChartInteractionMixin, QWidget):
    """Compact Jan–Dec chart: one line per person with clear labels."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
        series: list[tuple[str, str, list[float]]],
        *,
        empty: str | None = None,
    ) -> None:
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
                best = f"{series_name} · {label}\n{_fmt_point('TZS', value) or _fmt_tick(value)}"
        return best

    def chart_full_view_factory(self) -> QWidget:
        clone = _PersonSpendTrendChart()
        clone.setFixedHeight(520)
        clone.set_chart_title(self.chart_full_view_title())
        clone.set_data(list(self._series), empty=self._empty)
        return clone

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        self._hit_points = []
        pl, pr, pt, pb = 44, 14, 36, 42
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        n = 12

        # Legend with full person names (wrap to second row if needed)
        lx, ly = pl, 10
        p.setFont(QFont("Segoe UI", 8))
        max_x = self.width() - 8
        for name, color, _ in self._series:
            tw = p.fontMetrics().horizontalAdvance(name) + 18
            if lx + tw > max_x and lx > pl:
                lx = pl
                ly += 14
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawEllipse(QPointF(lx + 4, ly + 2), 4.0, 4.0)
            p.setPen(QColor(_T1))
            p.drawText(int(lx + 12), ly + 6, name)
            lx += tw

        # Shared Y scale so each person line reflects absolute TZS (not own peak).
        max_v = 0.0
        for _, _, values in self._series:
            if values:
                max_v = max(max_v, max(values))
        max_v = _nice_max(max_v)
        has_values = max_v > 0 and any(
            any(v > 0 for v in values) for _, _, values in self._series
        )

        p.setFont(QFont("Segoe UI", 7))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.setPen(QPen(QColor("#EEF2F7"), 1))
            p.drawLine(pl, y, pl + w, y)
            p.setPen(QColor(_TM))
            p.drawText(2, y + 3, _fmt_tick(max_v * i / 4))

        if not self._series or not has_values:
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(pl, pt, w, h), Qt.AlignCenter, self._empty)
            p.end()
            return

        def _x_at(i: int) -> float:
            return pl + w / 2 if n == 1 else pl + (i / (n - 1)) * w

        def _y_at(value: float) -> float:
            return pt + h - (max(value, 0.0) / max_v) * h

        for name, color, values in self._series:
            if not any(v > 0 for v in values):
                continue
            path = QPainterPath()
            points: list[QPointF] = []
            for i in range(n):
                point = QPointF(_x_at(i), _y_at(values[i]))
                points.append(point)
                month = self._labels[i] if i < len(self._labels) else str(i + 1)
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

        # Peak label only for top 3 annual spenders — offset so they don't pile up
        ranked = sorted(
            self._series,
            key=lambda s: sum(s[2]),
            reverse=True,
        )[:3]
        p.setFont(QFont("Segoe UI", 7))
        for offset, (name, color, values) in enumerate(ranked):
            best_i = max(range(n), key=lambda i: values[i])
            if values[best_i] <= 0:
                continue
            text = _fmt_point("TZS", values[best_i])
            if not text:
                continue
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(text)
            x = _x_at(best_i) - tw / 2
            y = _y_at(values[best_i]) - 6 - offset * 11
            p.setPen(QColor(color))
            p.drawText(QPointF(x, y), text)

        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor(_T2))
        for i, label in enumerate(self._labels):
            cx = _x_at(i)
            cy = pt + h + 10
            p.save()
            p.translate(cx, cy)
            p.rotate(-40)
            p.drawText(0, 0, label)
            p.restore()
        p.end()


class _ResponsiveSplitHost(QWidget):
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
        self._layout.setAlignment(
            self._right, Qt.AlignTop if not wrapped else Qt.Alignment()
        )


class AnalyticsMeetingsPage(QWidget):
    """Master people accountability (App by / Ownership) for a date range."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        today = date.today()
        self._date_from = date(today.year, 1, 1)
        self._date_to = today
        self._dimension = "approver"
        self._person = ""
        self._loading = False
        self._data: dict = {}
        self._dist_mode = "converted"

        self._subtitle: Optional[QLabel] = None
        self._status_lbl: Optional[QLabel] = None
        self._chart_year_lbl: Optional[QLabel] = None
        self._dist_mode_lbl: Optional[QLabel] = None
        self._dist_card: Optional[QFrame] = None
        self._dist_stack: Optional[QStackedWidget] = None
        self._dist_mode_toggle: Optional[_DistModeToggle] = None
        self._table: Optional[QTableWidget] = None
        self._line_chart: Optional[_PersonSpendTrendChart] = None
        self._donut: Optional[_DonutChart] = None
        self._dist_legend_host: Optional[QVBoxLayout] = None
        self._raw_donuts: dict[str, _DonutChart] = {}
        self._raw_legend_hosts: dict[str, QVBoxLayout] = {}
        self._from_date: Optional[QDateEdit] = None
        self._to_date: Optional[QDateEdit] = None
        self._dim_combo: Optional[QComboBox] = None
        self._person_combo: Optional[QComboBox] = None
        self._apply_btn: Optional[QPushButton] = None

        self.setStyleSheet(
            f"AnalyticsMeetingsPage {{ background: {_BG}; border: none; }}"
            "AnalyticsMeetingsPage QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    def ensure_loaded(self) -> None:
        if self._data:
            return
        self.refresh()

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        show_widget_loading(self, "Loading meetings…")
        from tahmeed.services.accountant_service import get_analytics_meetings
        try:
            self._data = await get_analytics_meetings(
                date_from=self._date_from.isoformat(),
                date_to=self._date_to.isoformat(),
                dimension=self._dimension,
                person=self._person or None,
            )
            self._apply()
        except Exception as exc:
            self._data = {
                "people": [],
                "totals": {},
                "month_trend": [],
                "chart_people": [],
                "chart_year": self._date_to.year,
                "dimension": self._dimension,
                "dimension_label": "App by" if self._dimension == "approver" else "Ownership",
                "rates_to_tzs": dict(_FALLBACK_RATES),
                "fx_source": "fallback",
                "message": f"Could not load meetings.\n{exc}",
            }
            self._apply()
        finally:
            self._loading = False
            hide_widget_loading(self)

    def _apply(self) -> None:
        people = list(self._data.get("people") or [])
        dim_label = str(self._data.get("dimension_label") or "App by")
        if self._subtitle:
            label = str(self._data.get("period_label") or "")
            n = int(self._data.get("person_count") or len(people))
            self._subtitle.setText(
                f"Master · {dim_label} · {label or '—'} — {n} people · "
                "receipt compliance excludes no-receipt entries."
            )
        chart_year = int(self._data.get("chart_year") or self._date_to.year)
        if self._chart_year_lbl:
            self._chart_year_lbl.setText(f"{chart_year} · Jan – Dec · TZS")
        self._sync_person_combo(people)
        self._fill_chart(
            list(self._data.get("chart_people") or []),
            list(self._data.get("month_trend") or []),
        )
        if not people:
            msg = str(self._data.get("message") or "No Master spend for this filter.")
            if self._status_lbl:
                self._status_lbl.setText(msg)
                self._status_lbl.show()
            self._fill_table([], {})
            self._refresh_distribution()
            return
        if self._status_lbl:
            self._status_lbl.hide()
        self._fill_table(people, dict(self._data.get("totals") or {}))
        self._refresh_distribution()

    def _sync_person_combo(self, people: list[dict]) -> None:
        if not self._person_combo:
            return
        current = self._person
        names = sorted(
            {str(r.get("name") or "").strip() for r in people if str(r.get("name") or "").strip()},
            key=str.casefold,
        )
        # When a person filter is active, API returns one row — keep prior options
        # by merging with what's already in the combo (except All).
        existing = []
        for i in range(1, self._person_combo.count()):
            existing.append(self._person_combo.itemText(i))
        if current and current not in names:
            names = sorted(set(names) | {current} | set(existing), key=str.casefold)
        elif not current and len(names) <= 1 and existing:
            names = sorted(set(names) | set(existing), key=str.casefold)
        self._person_combo.blockSignals(True)
        self._person_combo.clear()
        self._person_combo.addItem("All people", "")
        for name in names:
            self._person_combo.addItem(name, name)
        idx = self._person_combo.findData(current)
        self._person_combo.setCurrentIndex(max(0, idx))
        self._person_combo.blockSignals(False)

    def _rates(self) -> dict[str, float]:
        rates = dict(self._data.get("rates_to_tzs") or {})
        return {
            "TZS": float(rates.get("TZS") or _FALLBACK_RATES["TZS"]),
            "USD": float(rates.get("USD") or _FALLBACK_RATES["USD"]),
            "ZMW": float(rates.get("ZMW") or _FALLBACK_RATES["ZMW"]),
        }

    def _on_dist_mode(self, mode: str) -> None:
        self._dist_mode = mode if mode in {"converted", "raw"} else "converted"
        self._refresh_distribution()

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
        people = list(self._data.get("people") or [])
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
            slices = _build_converted_slices(people, self._rates())
            if self._donut:
                self._donut.set_data(slices, empty="No expense distribution.")
            self._set_legend(self._dist_legend_host, slices)
            return
        for cur in ("TZS", "USD", "ZMW"):
            slices = _build_raw_slices(people, cur)
            donut = self._raw_donuts.get(cur)
            if donut:
                donut.set_data(slices, empty=f"No {cur}")
            self._set_legend(self._raw_legend_hosts.get(cur), slices)

    def _fill_chart(self, chart_people: list[str], month_trend: list[dict]) -> None:
        if not self._line_chart:
            return
        names = [str(n) for n in chart_people if str(n).strip()]
        series = []
        for i, name in enumerate(names):
            color = _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)]
            values = []
            for m in range(1, 13):
                row = next(
                    (r for r in month_trend if int(r.get("month") or 0) == m),
                    None,
                )
                series_map = dict((row or {}).get("series") or {})
                values.append(float(series_map.get(name) or 0.0))
            series.append((name, color, values))
        self._line_chart.set_data(series, empty="No spend for this year.")

    def _fill_table(self, people: list[dict], totals: dict) -> None:
        if not self._table:
            return
        rows = list(people)
        self._table.setRowCount(len(rows) + (1 if rows else 0))
        bold = QFont("Segoe UI", 12)
        bold.setBold(True)
        for i, row in enumerate(rows):
            zebra = QColor("#F8FAFC") if i % 2 else QColor(_WHITE)
            cells = [
                str(int(row.get("sn") or (i + 1))),
                str(row.get("name") or "—"),
                f"{int(row.get('count') or 0):,}",
                _fmt_money(float(row.get("tzs") or 0.0)),
                _fmt_money(float(row.get("usd") or 0.0)),
                _fmt_money(float(row.get("zmw") or 0.0)),
                _fmt_compliance(row.get("compliance_pct")),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setBackground(zebra)
                item.setForeground(QColor(_T1))
                item.setTextAlignment(
                    Qt.AlignVCenter
                    | (Qt.AlignLeft if col == 1 else Qt.AlignRight)
                )
                self._table.setItem(i, col, item)
        if rows:
            trow = len(rows)
            for col, text in enumerate(
                [
                    "",
                    f"Total ({len(rows)})",
                    f"{int(totals.get('count') or 0):,}",
                    _fmt_money(float(totals.get("tzs") or 0.0)),
                    _fmt_money(float(totals.get("usd") or 0.0)),
                    _fmt_money(float(totals.get("zmw") or 0.0)),
                    _fmt_compliance(totals.get("compliance_pct")),
                ]
            ):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                item.setFont(bold)
                item.setBackground(QColor("#E8EEF4"))
                item.setForeground(QColor(_NAVY))
                item.setTextAlignment(
                    Qt.AlignVCenter
                    | (Qt.AlignLeft if col == 1 else Qt.AlignRight)
                )
                self._table.setItem(trow, col, item)

    def _on_apply(self) -> None:
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
        if self._dim_combo:
            self._dimension = str(self._dim_combo.currentData() or "approver")
        if self._person_combo:
            self._person = str(self._person_combo.currentData() or "")
        self.refresh()

    def _combo_style(self) -> str:
        return (
            f"QComboBox {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 10px; font-family: 'Segoe UI'; font-size: 13px; color: {_T1};"
            f"}}"
            f"QComboBox:focus {{ border-color: {_BLUE}; }}"
            "QComboBox::drop-down { border: none; width: 22px; }"
        )

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
        left.addWidget(_lbl("Meetings", size=22, weight=700))
        self._subtitle = _lbl(
            "Master spend and receipt compliance by App by or Ownership.",
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

        filter_row.addWidget(_lbl("Group by", size=12, color=_T2))
        self._dim_combo = QComboBox()
        self._dim_combo.setFixedHeight(34)
        self._dim_combo.setMinimumWidth(120)
        self._dim_combo.setStyleSheet(self._combo_style())
        self._dim_combo.addItem("App by", "approver")
        self._dim_combo.addItem("Ownership", "ownership")
        self._dim_combo.setCurrentIndex(0)
        filter_row.addWidget(self._dim_combo)

        filter_row.addWidget(_lbl("Person", size=12, color=_T2))
        self._person_combo = QComboBox()
        self._person_combo.setFixedHeight(34)
        self._person_combo.setMinimumWidth(160)
        self._person_combo.setStyleSheet(self._combo_style())
        self._person_combo.addItem("All people", "")
        filter_row.addWidget(self._person_combo)

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
        self._apply_btn.clicked.connect(self._on_apply)
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
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (0, 2, 3, 4, 5, 6):
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
        title_row.addWidget(_lbl("Spend Trend", size=13, weight=600, color=_TITLE))
        title_row.addStretch(1)
        self._chart_year_lbl = _lbl("", size=11, color=_T2)
        title_row.addWidget(self._chart_year_lbl)
        trend_vl.addLayout(title_row)
        self._line_chart = _PersonSpendTrendChart()
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
        converted_row.addWidget(legend_wrap, 1)
        self._dist_stack.addWidget(converted_page)

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
