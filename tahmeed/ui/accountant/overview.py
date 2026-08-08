"""AccountantDashboard — Overview page (live data, minimal UI)."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QRectF, QDateTime, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QSizePolicy, QProgressBar, QMenu, QButtonGroup,
)

from tahmeed.app_state import app_state

# ── Design tokens (minimal dashboard) ────────────────────────────────────────
_WHITE   = "#FFFFFF"
_BORDER  = "#E5E7EB"
_BG      = "#F1F5F9"
_NAVY    = "#1B2B4B"   # matches sidebar
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_PURPLE  = "#7C3AED"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"

_CATEGORY_COLORS = (_NAVY, _BLUE, _AMBER, _PURPLE, _TM)
_MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_CURRENCIES = ("TZS", "USD", "ZMW")
_CURRENCY_KEY = {"TZS": "tzs", "USD": "usd", "ZMW": "zmw"}

# Card chrome is applied only via QFrame#overviewCard — never unscoped,
# so border does not cascade onto child labels.


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


def _fmt_currency_short(currency: str, amount: float) -> str:
    abs_amt = abs(amount or 0.0)
    cur = (currency or "TZS").upper()
    if cur == "USD":
        if abs_amt >= 1_000_000:
            return f"${abs_amt / 1_000_000:.1f}M"
        if abs_amt >= 1_000:
            return f"${abs_amt / 1_000:.1f}K"
        return f"${abs_amt:,.0f}"
    prefix = "TZS" if cur == "TZS" else "ZMW"
    if abs_amt >= 1_000_000:
        return f"{prefix} {abs_amt / 1_000_000:.1f}M"
    if abs_amt >= 1_000:
        return f"{prefix} {abs_amt / 1_000:.1f}K"
    return f"{prefix} {abs_amt:,.0f}"


def _fmt_amount(tx) -> str:
    amt = abs(tx.amount or 0)
    cur = (tx.currency or "TZS").upper()
    if cur in ("USD",):
        return f"${amt:,.0f}"
    if cur in ("ZMW", "ZMB", "ZK"):
        return f"ZMW {amt:,.0f}"
    return f"TZS {amt:,.0f}"


def _chart_values(currency: str, raw: list[float]) -> tuple[list[float], str]:
    """Scale chart values for readable Y-axis units per currency."""
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


# ── KPI Card ──────────────────────────────────────────────────────────────────

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

        self._val_lbl = _lbl(value, size=28, weight=700)
        vl.addWidget(self._val_lbl)
        vl.addSpacing(4)

        self._label_lbl = _lbl(label, size=12, color=_T2, wrap=True)
        vl.addWidget(self._label_lbl)

    def set_value(self, value: str) -> None:
        self._val_lbl.setText(value)

    def set_label(self, label: str) -> None:
        self._label_lbl.setText(label)


# ── Bar chart ─────────────────────────────────────────────────────────────────

class _BarChartWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._months: list[str] = list(_MONTH_ABBR)
        self._values: list[float] = [0.0] * 12
        self.setMinimumHeight(200)
        self.setStyleSheet("background: transparent; border: none;")

    def set_data(self, months: list[str], values: list[float]) -> None:
        self._months = months or list(_MONTH_ABBR)
        self._values = values or [0.0] * len(self._months)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        pl, pr, pt, pb = 40, 16, 12, 32
        w = max(1, self.width() - pl - pr)
        h = max(1, self.height() - pt - pb)
        n = max(1, len(self._months))
        max_v = max(self._values) if self._values else 0
        if max_v <= 0:
            max_v = 1

        p.setPen(QPen(QColor(_BORDER), 1))
        for i in range(5):
            y = int(pt + h - (i / 4) * h)
            p.drawLine(pl, y, pl + w, y)
            p.setPen(QColor(_TM))
            p.setFont(QFont("Segoe UI", 8))
            tick = max_v * i / 4
            tick_s = f"{tick:.1f}" if tick < 10 else f"{tick:.0f}"
            p.drawText(4, y + 4, tick_s)
            p.setPen(QPen(QColor(_BORDER), 1))

        slot_w = w / n
        bar_w = slot_w * 0.55
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(_NAVY)))
        for i, v in enumerate(self._values):
            bh = (v / max_v) * h if v > 0 else 0
            bx = int(pl + i * slot_w + (slot_w - bar_w) / 2)
            by = int(pt + h - bh)
            if bh > 0:
                p.drawRoundedRect(bx, by, int(bar_w), int(bh), 3, 3)

        p.setPen(QColor(_T2))
        p.setFont(QFont("Segoe UI", 9))
        fm = p.fontMetrics()
        for i, m in enumerate(self._months):
            cx = int(pl + i * slot_w + slot_w / 2)
            tw = fm.horizontalAdvance(m)
            p.drawText(cx - tw // 2, self.height() - 8, m)

        p.end()


# ── Donut chart ───────────────────────────────────────────────────────────────

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


# ── Receipt status card ───────────────────────────────────────────────────────

class _ReceiptCard(QFrame):
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
        vl = QVBoxLayout(self)
        vl.setContentsMargins(18, 16, 18, 16)
        vl.setSpacing(12)
        vl.addWidget(_lbl("Receipt status", size=14, weight=600))
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(10)
        vl.addLayout(self._rows_layout)

    def set_data(self, received: int, pending: int, missing: int, total: int) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if total <= 0:
            self._rows_layout.addWidget(_lbl("No verified entries for this FY.", size=12, color=_TM))
            return

        rows = [
            ("Received", received, _GREEN),
            ("Pending", pending, _AMBER),
            ("Missing", missing, _RED),
        ]
        for label, count, color in rows:
            pct = int(round(count / total * 100))
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent; border: none;")
            rl = QVBoxLayout(row_w)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)

            top = QHBoxLayout()
            top.addWidget(_lbl(label, size=12, color=_T2))
            top.addStretch()
            top.addWidget(_lbl(f"{count:,}  ·  {pct}%", size=12, weight=600))
            rl.addLayout(top)

            bar = QProgressBar()
            bar.setFixedHeight(6)
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(False)
            bar.setStyleSheet(f"""
                QProgressBar {{
                    background: {_BORDER};
                    border: none;
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background: {color};
                    border-radius: 3px;
                }}
            """)
            rl.addWidget(bar)
            self._rows_layout.addWidget(row_w)


# ── Recent activity card ─────────────────────────────────────────────────────

class _ActivityCard(QFrame):
    view_all_clicked = Signal()

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
        vl = QVBoxLayout(self)
        vl.setContentsMargins(18, 16, 18, 16)
        vl.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(_lbl("Recent activity", size=14, weight=600))
        header.addStretch()
        view_all = QPushButton("View all")
        view_all.setCursor(Qt.PointingHandCursor)
        view_all.setStyleSheet(
            f"QPushButton {{ border: none; color: {_BLUE}; font-size: 12px;"
            " font-family:'Segoe UI'; background: transparent; }}"
            f"QPushButton:hover {{ color: #005fa3; }}"
        )
        view_all.clicked.connect(self.view_all_clicked.emit)
        header.addWidget(view_all)
        vl.addLayout(header)

        self._list_layout = QVBoxLayout()
        self._list_layout.setSpacing(8)
        vl.addLayout(self._list_layout)
        vl.addStretch()

    def set_transactions(self, transactions) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not transactions:
            self._list_layout.addWidget(
                _lbl("No verified entries for this FY.", size=12, color=_TM)
            )
            return

        for tx in transactions:
            row = QHBoxLayout()
            row.setSpacing(10)

            truck = (tx.truck_number or "—").strip()
            truck_l = _lbl(truck, size=12, weight=600)
            truck_l.setFixedWidth(72)
            row.addWidget(truck_l)

            desc = (tx.description or tx.category_name or "—").strip()
            desc_l = _lbl(desc, size=12, color=_T2)
            desc_l.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(desc_l)

            row.addWidget(_lbl(_fmt_amount(tx), size=12, weight=600))

            wrap = QWidget()
            wrap.setStyleSheet("background: transparent; border: none;")
            wrap.setLayout(row)
            self._list_layout.addWidget(wrap)


# ── OverviewWidget (public) ───────────────────────────────────────────────────

class OverviewWidget(QWidget):
    """Accountant overview — live KPIs, charts, receipts, and activity."""

    navigate = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._year = app_state.fiscal_year
        self._loading = False
        self._chart_currency = "TZS"
        self._month_totals: dict = {}
        self._categories: list[dict] = []
        self._kpi_pending: Optional[_KPICard] = None
        self._kpi_master: Optional[_KPICard] = None
        self._kpi_verified: Optional[_KPICard] = None
        self._kpi_tzs: Optional[_KPICard] = None
        self._kpi_usd: Optional[_KPICard] = None
        self._kpi_zmw: Optional[_KPICard] = None
        self._bar_chart: Optional[_BarChartWidget] = None
        self._bar_subtitle: Optional[QLabel] = None
        self._trend_toggle: Optional[_CurrencyToggle] = None
        self._category_toggle: Optional[_CurrencyToggle] = None
        self._donut: Optional[_DonutChart] = None
        self._donut_legend_host: Optional[QVBoxLayout] = None
        self._receipt_card: Optional[_ReceiptCard] = None
        self._activity_card: Optional[_ActivityCard] = None
        self._fy_btn: Optional[QPushButton] = None
        self.setStyleSheet(
            f"OverviewWidget {{ background: {_BG}; border: none; }}"
            "OverviewWidget QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        if self._year != app_state.fiscal_year:
            self._year = app_state.fiscal_year
            self._update_fy_button()
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        from tahmeed.services.accountant_service import get_overview_dashboard
        try:
            data = await get_overview_dashboard(self._year)
            self._apply(data)
        except Exception:
            pass
        finally:
            self._loading = False

    def _apply(self, data: dict) -> None:
        kpis = data["kpis"]
        self._month_totals = data["month_totals"]
        self._categories = data["categories"]
        receipts = data["receipts"]
        recent = data["recent"]

        if self._kpi_pending:
            self._kpi_pending.set_value(str(kpis["pending_count"]))
        if self._kpi_master:
            self._kpi_master.set_value(f"{kpis['master_count']:,}")
        if self._kpi_verified:
            verified = kpis["verified_this_month"]
            submitted = kpis["submitted_this_month"]
            self._kpi_verified.set_value(f"{verified} of {submitted}")
        if self._kpi_tzs:
            self._kpi_tzs.set_value(_fmt_currency_short("TZS", kpis.get("total_tzs_ytd", 0.0)))
        if self._kpi_usd:
            self._kpi_usd.set_value(_fmt_currency_short("USD", kpis.get("total_usd_ytd", 0.0)))
        if self._kpi_zmw:
            self._kpi_zmw.set_value(_fmt_currency_short("ZMW", kpis.get("total_zmw_ytd", 0.0)))

        self._refresh_trend_chart()
        self._refresh_category_chart()

        if self._receipt_card:
            self._receipt_card.set_data(
                receipts["received"],
                receipts["pending"],
                receipts["missing"],
                receipts["total"],
            )
        if self._activity_card:
            self._activity_card.set_transactions(recent)

    def _on_currency_changed(self, currency: str) -> None:
        if currency == self._chart_currency:
            return
        self._chart_currency = currency
        # Keep trend + category toggles in sync without re-emitting
        if self._trend_toggle and self._trend_toggle.currency() != currency:
            self._trend_toggle.set_currency(currency, emit=False)
        if self._category_toggle and self._category_toggle.currency() != currency:
            self._category_toggle.set_currency(currency, emit=False)
        self._refresh_trend_chart()
        self._refresh_category_chart()

    def _refresh_trend_chart(self) -> None:
        today = date.today()
        end_month = today.month if self._year == today.year else 12
        months = list(_MONTH_ABBR[:end_month])
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        raw = [
            float(self._month_totals.get(m, {}).get(key, 0.0) or 0.0)
            for m in range(1, end_month + 1)
        ]
        values, unit = _chart_values(self._chart_currency, raw)
        if self._bar_chart:
            self._bar_chart.set_data(months, values)
        if self._bar_subtitle:
            range_label = f"Jan to {_MONTH_ABBR[end_month - 1]} {self._year}"
            self._bar_subtitle.setText(f"{unit}, {range_label}")

    def _refresh_category_chart(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        amounts = [
            (cat.get("name") or "Uncategorised", float(cat.get(key, 0.0) or 0.0))
            for cat in self._categories
        ]
        total = sum(a for _, a in amounts)
        slices: list[tuple[str, float, str]] = []
        if total > 0:
            for i, (name, amount) in enumerate(amounts):
                if amount <= 0:
                    continue
                color = _CATEGORY_COLORS[i % len(_CATEGORY_COLORS)]
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
                    _lbl(f"No {self._chart_currency} category spend yet.", size=12, color=_TM)
                )

    def _update_fy_button(self) -> None:
        if self._fy_btn:
            self._fy_btn.setText(f"FY {self._year}")

    def _on_fy_selected(self, year: int) -> None:
        if year == self._year:
            return
        self._year = year
        app_state.fiscal_year = year
        self._update_fy_button()
        asyncio.ensure_future(self._load())

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

        content = QWidget()
        content.setObjectName("overviewContent")
        content.setStyleSheet(
            f"QWidget#overviewContent {{ background: {_BG}; border: none; }}"
        )
        vl = QVBoxLayout(content)
        vl.setContentsMargins(28, 24, 28, 28)
        vl.setSpacing(20)

        # Header
        header = QHBoxLayout()
        header.setSpacing(0)
        left = QVBoxLayout()
        left.setSpacing(4)
        now = QDateTime.currentDateTime()
        hour = now.time().hour()
        greeting = (
            "Good morning" if hour < 12
            else ("Good afternoon" if hour < 17 else "Good evening")
        )
        left.addWidget(_lbl(f"{greeting}, Accountant.", size=22, weight=700))
        left.addWidget(_lbl(now.toString("dddd, d MMMM yyyy"), size=13, color=_T2))
        header.addLayout(left)
        header.addStretch()

        self._fy_btn = QPushButton(f"FY {self._year}")
        self._fy_btn.setCursor(Qt.PointingHandCursor)
        self._fy_btn.setFixedHeight(34)
        self._fy_btn.setStyleSheet(f"""
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
        """)
        fy_menu = QMenu(self._fy_btn)
        fy_menu.setStyleSheet(
            "QMenu { background: #FFFFFF; border: 1px solid #E5E7EB; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; font-family: 'Segoe UI'; }"
            "QMenu::item:selected { background: #E8F4FD; }"
        )
        current_yr = date.today().year
        for yr in range(current_yr - 3, current_yr + 2):
            action = fy_menu.addAction(f"FY {yr}")
            action.setData(yr)
        self._fy_btn.setMenu(fy_menu)
        fy_menu.triggered.connect(
            lambda action: self._on_fy_selected(action.data())
        )
        header.addWidget(self._fy_btn)
        vl.addLayout(header)

        # KPI row — counts + per-currency totals (same height)
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(16)
        self._kpi_pending = _KPICard(
            "mdi.inbox-arrow-down", _BLUE, _BLUE_L,
            "—", "Entries awaiting review",
        )
        kpi_row.addWidget(self._kpi_pending)
        self._kpi_master = _KPICard(
            "mdi.table-large", _BLUE, _BLUE_L,
            "—", "Master entries YTD",
        )
        kpi_row.addWidget(self._kpi_master)
        self._kpi_verified = _KPICard(
            "mdi.check-circle-outline", _GREEN, _GREEN_L,
            "—", "Verified this month",
        )
        kpi_row.addWidget(self._kpi_verified)
        self._kpi_tzs = _KPICard(
            "mdi.cash", _BLUE, _BLUE_L,
            "—", "TZS expenses YTD",
        )
        kpi_row.addWidget(self._kpi_tzs)
        self._kpi_usd = _KPICard(
            "mdi.currency-usd", _GREEN, _GREEN_L,
            "—", "USD expenses YTD",
        )
        kpi_row.addWidget(self._kpi_usd)
        self._kpi_zmw = _KPICard(
            "mdi.cash-multiple", _AMBER, _AMBER_L,
            "—", "ZMW expenses YTD",
        )
        kpi_row.addWidget(self._kpi_zmw)
        vl.addLayout(kpi_row)

        # Charts row
        charts_row = QHBoxLayout()
        charts_row.setSpacing(16)

        bar_card = _card()
        bar_vl = QVBoxLayout(bar_card)
        bar_vl.setContentsMargins(20, 18, 20, 18)
        bar_vl.setSpacing(6)
        bar_header = QHBoxLayout()
        bar_header.addWidget(_lbl("Monthly expense trend", size=14, weight=600))
        bar_header.addStretch()
        self._trend_toggle = _CurrencyToggle()
        self._trend_toggle.changed.connect(self._on_currency_changed)
        bar_header.addWidget(self._trend_toggle)
        bar_vl.addLayout(bar_header)
        self._bar_subtitle = _lbl("TZS millions", size=12, color=_TM)
        bar_vl.addWidget(self._bar_subtitle)
        self._bar_chart = _BarChartWidget()
        bar_vl.addWidget(self._bar_chart, 1)
        charts_row.addWidget(bar_card, 6)

        pie_card = _card()
        pie_vl = QVBoxLayout(pie_card)
        pie_vl.setContentsMargins(20, 18, 20, 18)
        pie_vl.setSpacing(12)
        pie_header = QHBoxLayout()
        pie_header.addWidget(_lbl("By category", size=14, weight=600))
        pie_header.addStretch()
        self._category_toggle = _CurrencyToggle()
        self._category_toggle.changed.connect(self._on_currency_changed)
        pie_header.addWidget(self._category_toggle)
        pie_vl.addLayout(pie_header)
        self._donut = _DonutChart()
        pie_vl.addWidget(self._donut, alignment=Qt.AlignHCenter)
        legend_wrap = QWidget()
        legend_wrap.setStyleSheet("background: transparent; border: none;")
        self._donut_legend_host = QVBoxLayout(legend_wrap)
        self._donut_legend_host.setContentsMargins(0, 0, 0, 0)
        pie_vl.addWidget(legend_wrap)
        charts_row.addWidget(pie_card, 4)
        vl.addLayout(charts_row)

        # Bottom row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(16)
        self._receipt_card = _ReceiptCard()
        bottom_row.addWidget(self._receipt_card, 5)
        self._activity_card = _ActivityCard()
        self._activity_card.view_all_clicked.connect(
            lambda: self.navigate.emit("master_expenses")
        )
        bottom_row.addWidget(self._activity_card, 5)
        vl.addLayout(bottom_row)

        # Quick actions
        actions = QHBoxLayout()
        actions.setSpacing(10)
        for label, key in (
            ("Verify inbox", "verify"),
            ("Master expenses", "master_expenses"),
            ("Truck overview", "truck_overview"),
            ("Browse all", "browse"),
        ):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(36)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_WHITE};
                    border: 1px solid {_BORDER};
                    border-radius: 8px;
                    color: {_T1};
                    font-size: 12px;
                    font-family: 'Segoe UI';
                    padding: 0 16px;
                }}
                QPushButton:hover {{ background: #F8FAFC; border-color: #CBD5E1; }}
            """)
            btn.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            actions.addWidget(btn)
        actions.addStretch()
        vl.addLayout(actions)

        scroll.setWidget(content)
        root.addWidget(scroll)
        self.refresh()
