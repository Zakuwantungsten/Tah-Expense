"""Analytics → Trucks: fleet table + per-truck deep-dive (Phases T1–T3)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QMenu, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QStackedWidget, QButtonGroup,
    QLineEdit, QComboBox, QToolTip,
)

from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.overview_geometry import OverviewGeometryContent
from tahmeed.ui.accountant.analytics import (
    _BG, _WHITE, _BORDER, _NAVY, _BLUE, _BLUE_L, _GREEN, _GREEN_L,
    _AMBER, _AMBER_L, _RED, _T1, _T2, _TM,
    _MONTH_ABBR, _CURRENCY_KEY, _TREEMAP_COLORS,
    _lbl, _card, _chart_title, _fmt_currency_short, _chart_values,
    _CurrencyToggle, _KPICard, _LineChartWidget, _TreemapWidget,
    _VBarChartWidget, _HBarChartWidget, _DonutChart, _donut_legend,
)

_COLS = ("SN", "Truck", "TZS", "USD", "ZMW", "MoM", "Share", "Entries")
_COL_SN = 0
_COL_TRUCK = 1
_COL_MOM = 5
_SHARE_BASIS_LABEL = {"tzs": "TZS", "usd": "USD", "zmw": "ZMW"}
_KIND_FILTERS = (
    ("all", "All vehicles"),
    ("truck", "Trucks"),
    ("trailer", "Trailers"),
    ("motor_vehicle", "Motorcycles & Cars"),
)
_BLANK_PLATES = frozenset(
    {"", "—", "-", "–", "Unknown", "N/A", "n/a", "none", "null"}
)


class AnalyticsTrucksPage(QWidget):
    """Trucks tab: multi-currency fleet table → click for deep-dive charts."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        today = date.today()
        self._year = today.year
        self._period = "year"
        self._month = today.month
        self._chart_currency = "TZS"
        self._loading = False
        self._load_gen = 0
        self._fleet_loaded = False
        self._fleet: dict = {}
        self._detail: dict = {}
        self._selected_truck = ""
        self._view = "fleet"  # fleet | detail
        self._search_text = ""
        self._fleet_kind = "all"

        self._year_btn: Optional[QPushButton] = None
        self._period_btn: Optional[QPushButton] = None
        self._period_menu: Optional[QMenu] = None
        self._back_btn: Optional[QPushButton] = None
        self._subtitle: Optional[QLabel] = None
        self._status_lbl: Optional[QLabel] = None
        self._table: Optional[QTableWidget] = None
        self._body_stack: Optional[QStackedWidget] = None
        self._search_edit: Optional[QLineEdit] = None
        self._filter_bar: Optional[QWidget] = None
        self._kind_combo: Optional[QComboBox] = None

        self._kpi_total: Optional[_KPICard] = None
        self._kpi_avg: Optional[_KPICard] = None
        self._kpi_count: Optional[_KPICard] = None
        self._kpi_yoy: Optional[_KPICard] = None
        self._kpi_cards: list[_KPICard] = []
        self._line: Optional[_LineChartWidget] = None
        self._treemap: Optional[_TreemapWidget] = None
        self._mix_title: Optional[QLabel] = None
        self._desc_bars: Optional[_VBarChartWidget] = None
        self._desc_title: Optional[QLabel] = None
        self._deltas: Optional[_HBarChartWidget] = None
        self._deltas_title: Optional[QLabel] = None
        self._delta_mode = "yoy"  # yoy | mom
        self._delta_mode_btns: list[QPushButton] = []
        self._receipts: Optional[_DonutChart] = None
        self._receipts_legend: Optional[QVBoxLayout] = None
        self._currency_toggles: list[_CurrencyToggle] = []

        self.setStyleSheet(
            f"AnalyticsTrucksPage {{ background: {_BG}; border: none; }}"
            "AnalyticsTrucksPage QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        if self._view == "detail" and self._selected_truck:
            schedule_coro(self._load_detail())
        else:
            schedule_coro(self._load_fleet())

    def ensure_loaded(self) -> None:
        """IconRail revisit: keep current view; only fetch if never loaded."""
        if self._view == "detail" and self._detail.get("ready"):
            return
        if self._fleet_loaded and self._view == "fleet":
            return
        # Failed / blank detail must not keep re-hitting the API on tab revisit.
        if self._view == "detail" and not self._is_real_plate(self._selected_truck):
            self._view = "fleet"
            self._selected_truck = ""
            self._detail = {}
        if self._fleet_loaded:
            self._view = "fleet"
            self._apply_fleet()
            return
        self.refresh()

    def _is_real_plate(self, plate: str | None) -> bool:
        text = str(plate or "").strip()
        return bool(text) and text not in _BLANK_PLATES

    def _invalidate_loads(self) -> None:
        """Drop in-flight fleet/detail results and hide any loading overlay."""
        self._load_gen += 1
        self._loading = False
        hide_widget_loading(self)

    def _finish_load(self, gen: int) -> None:
        if gen != self._load_gen:
            return
        self._loading = False
        hide_widget_loading(self)

    async def _load_fleet(self) -> None:
        self._load_gen += 1
        gen = self._load_gen
        self._loading = True
        show_widget_loading(self, "Loading fleet…")
        from tahmeed.services.accountant_service import get_analytics_trucks_fleet
        try:
            month = self._month if self._period == "month" else None
            payload = await get_analytics_trucks_fleet(
                self._year, period=self._period, month=month
            )
            if gen != self._load_gen:
                return
            self._fleet = payload
            self._fleet_loaded = True
            self._view = "fleet"
            self._apply_fleet()
        except Exception as exc:
            if gen != self._load_gen:
                return
            self._fleet = {
                "trucks": [],
                "totals": {},
                "message": f"Could not load truck fleet.\n{exc}",
            }
            self._fleet_loaded = True
            self._view = "fleet"
            self._apply_fleet()
        finally:
            self._finish_load(gen)

    async def _load_detail(self) -> None:
        truck = (self._selected_truck or "").strip()
        if not self._is_real_plate(truck):
            self._selected_truck = ""
            self._view = "fleet"
            if self._fleet_loaded:
                self._apply_fleet()
            return
        self._load_gen += 1
        gen = self._load_gen
        self._view = "detail"
        self._loading = True
        show_widget_loading(self, f"Loading {truck}…")
        from tahmeed.services.accountant_service import get_analytics_truck_detail
        try:
            month = self._month if self._period == "month" else None
            payload = await get_analytics_truck_detail(
                truck,
                self._year,
                period=self._period,
                month=month,
            )
            if gen != self._load_gen or self._view != "detail":
                return
            self._detail = payload
            self._apply_detail()
        except Exception as exc:
            if gen != self._load_gen or self._view != "detail":
                return
            self._detail = {
                "ready": False,
                "label": truck,
                "message": f"Could not load truck deep-dive.\n{exc}",
            }
            self._apply_detail()
        finally:
            self._finish_load(gen)

    def _clear_detail_charts(self) -> None:
        """Shrink/clear deep-dive painters so Fleet stack flip is cheap."""
        if self._line:
            self._line.set_data([], [], empty="")
        if self._treemap:
            self._treemap.set_data([], empty="")
        if self._desc_bars:
            self._desc_bars.set_data([], [], empty="")
        if self._deltas:
            self._deltas.set_data([], empty="")
        if self._receipts:
            self._receipts.set_data([], empty="")
        if self._receipts_legend:
            while self._receipts_legend.count():
                item = self._receipts_legend.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        if self._kpi_total:
            self._kpi_total.set_value("—")
        if self._kpi_avg:
            self._kpi_avg.set_value("—")
        if self._kpi_count:
            self._kpi_count.set_value("—")
        if self._kpi_yoy:
            self._kpi_yoy.set_value("—")

    def _apply_fleet(self) -> None:
        # Belt-and-suspenders: never leave "Loading fleet…" over a filled table.
        hide_widget_loading(self)
        self._loading = False
        self._update_chrome()
        if self._back_btn:
            self._back_btn.hide()
        if self._filter_bar:
            self._filter_bar.show()
        visible, totals = self._visible_fleet()
        all_rows = list(self._fleet.get("trucks") or [])
        if not all_rows:
            msg = str(self._fleet.get("message") or "No truck spend in this period.")
            if self._status_lbl:
                self._status_lbl.setText(msg)
                self._status_lbl.show()
            if self._body_stack:
                self._body_stack.setCurrentIndex(0)
            return
        if not visible:
            if self._status_lbl:
                self._status_lbl.setText("No trucks match this search / filter.")
                self._status_lbl.show()
            if self._body_stack:
                self._body_stack.setCurrentIndex(1)
            self._fill_table([], {})
            return
        if self._status_lbl:
            self._status_lbl.hide()
        if self._body_stack:
            self._body_stack.setCurrentIndex(1)
        self._fill_table(visible, totals)

    def _visible_fleet(self) -> tuple[list[dict], dict]:
        """Filter fleet by search + kind; renumber SN and recompute share."""
        import re

        needle = re.sub(r"\s+", "", (self._search_text or "").strip()).casefold()
        kind = (self._fleet_kind or "all").strip().lower()
        rows: list[dict] = []
        for row in list(self._fleet.get("trucks") or []):
            plate = str(row.get("truck") or "").strip()
            if not self._is_real_plate(plate):
                continue
            if kind != "all" and str(row.get("vehicle_kind") or "unknown") != kind:
                continue
            if needle and needle not in re.sub(r"\s+", "", plate).casefold():
                continue
            rows.append(dict(row))
        basis = str(self._fleet.get("share_basis") or "tzs").lower()
        if basis not in {"tzs", "usd", "zmw"}:
            basis = "tzs"
        totals = {
            "tzs": sum(float(r.get("tzs") or 0.0) for r in rows),
            "usd": sum(float(r.get("usd") or 0.0) for r in rows),
            "zmw": sum(float(r.get("zmw") or 0.0) for r in rows),
            "count": sum(int(r.get("count") or 0) for r in rows),
        }
        if float(totals["tzs"]) <= 0 and float(totals["usd"]) > 0:
            basis = "usd"
        elif (
            float(totals["tzs"]) <= 0
            and float(totals["usd"]) <= 0
            and float(totals["zmw"]) > 0
        ):
            basis = "zmw"
        share_total = float(totals.get(basis) or 0.0)
        out = []
        for i, row in enumerate(rows, start=1):
            amt = float(row.get(basis) or 0.0)
            out.append({
                **row,
                "sn": i,
                "share": (amt / share_total) if share_total > 0 else 0.0,
            })
        return out, totals

    def _apply_detail(self) -> None:
        hide_widget_loading(self)
        self._loading = False
        self._update_chrome()
        if self._back_btn:
            self._back_btn.show()
        if self._filter_bar:
            self._filter_bar.hide()
        ready = bool(self._detail.get("ready"))
        if not ready:
            msg = str(self._detail.get("message") or "No data for this truck.")
            if self._status_lbl:
                self._status_lbl.setText(msg)
                self._status_lbl.show()
            if self._body_stack:
                self._body_stack.setCurrentIndex(0)
            return
        if self._status_lbl:
            sparse = bool(self._detail.get("sparse"))
            if sparse:
                self._status_lbl.setText(
                    f"No Master activity for {self._selected_truck} "
                    f"in {self._detail.get('period_label') or self._year}."
                )
                self._status_lbl.show()
            else:
                self._status_lbl.hide()
        if self._body_stack:
            self._body_stack.setCurrentIndex(2)
        self._apply_detail_live()

    def _update_chrome(self) -> None:
        if self._year_btn:
            self._year_btn.setText(f"Year {self._year}")
        if self._period_btn:
            if self._period == "month":
                self._period_btn.setText(f"{_MONTH_ABBR[self._month - 1]} only")
            else:
                self._period_btn.setText("Full year")
        if not self._subtitle:
            return
        if self._view == "detail":
            label = str(self._detail.get("label") or self._selected_truck)
            self._subtitle.setText(
                f"Truck · {label} — category mix, descriptions, YoY, receipts."
            )
        else:
            visible, _ = self._visible_fleet()
            n_all = int(
                self._fleet.get("truck_count") or len(self._fleet.get("trucks") or [])
            )
            n = len(visible)
            label = str(self._fleet.get("period_label") or self._year)
            basis = _SHARE_BASIS_LABEL.get(
                str(self._fleet.get("share_basis") or "tzs").lower(), "TZS"
            )
            mom = str(self._fleet.get("mom_label") or "").strip()
            mom_bit = f" · MoM {mom}" if mom else ""
            shown = f"{n} of {n_all}" if n != n_all else f"{n}"
            self._subtitle.setText(
                f"Fleet · {label} — {shown} vehicles · share of {basis}{mom_bit}. "
                "Click a row for deep-dive."
            )

    @staticmethod
    def _fmt_mom_pct(pct: float, direction: str) -> tuple[str, str]:
        """Return (display text, color hex) for a MoM percentage."""
        direction = (direction or "flat").lower()
        if direction == "flat" or abs(pct) < 1e-9:
            return "—", _T2
        sign = "+" if pct > 0 else ""
        text = f"{sign}{pct * 100:.0f}%"
        if direction == "up":
            return text, _GREEN
        if direction == "down":
            return text, _RED
        return text, _T2

    def _fill_table(self, trucks: list[dict], totals: dict) -> None:
        if not self._table:
            return
        rows = list(trucks)
        self._table.setRowCount(len(rows) + (1 if rows else 0))
        bold = QFont("Segoe UI", 12)
        bold.setBold(True)
        for i, row in enumerate(rows):
            zebra = QColor("#F8FAFC") if i % 2 else QColor(_WHITE)
            mom_text, mom_color = self._fmt_mom_pct(
                float(row.get("mom_pct") or 0.0),
                str(row.get("mom_direction") or "flat"),
            )
            cells = [
                str(int(row.get("sn") or (i + 1))),
                str(row.get("truck") or "—"),
                _fmt_currency_short("TZS", float(row.get("tzs") or 0.0)),
                _fmt_currency_short("USD", float(row.get("usd") or 0.0)),
                _fmt_currency_short("ZMW", float(row.get("zmw") or 0.0)),
                mom_text,
                f"{float(row.get('share') or 0.0) * 100:.1f}%",
                f"{int(row.get('count') or 0):,}",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                if col == _COL_TRUCK:
                    item.setData(Qt.UserRole, str(row.get("truck") or ""))
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                elif col == _COL_SN:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
                    item.setData(Qt.UserRole, str(row.get("truck") or ""))
                else:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
                item.setBackground(zebra)
                if col == _COL_MOM:
                    item.setForeground(QColor(mom_color))
                    font = QFont("Segoe UI", 12)
                    font.setBold(True)
                    item.setFont(font)
                else:
                    item.setForeground(QColor(_T1))
                self._table.setItem(i, col, item)
        if rows:
            trow = len(rows)
            for col, text in enumerate(
                [
                    "",
                    f"Total ({len(rows)})",
                    _fmt_currency_short("TZS", float(totals.get("tzs") or 0.0)),
                    _fmt_currency_short("USD", float(totals.get("usd") or 0.0)),
                    _fmt_currency_short("ZMW", float(totals.get("zmw") or 0.0)),
                    "",
                    "100%",
                    f"{int(totals.get('count') or 0):,}",
                ]
            ):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                item.setFont(bold)
                item.setBackground(QColor("#E8EEF4"))
                item.setForeground(QColor(_NAVY))
                item.setTextAlignment(
                    Qt.AlignVCenter
                    | (Qt.AlignLeft if col == _COL_TRUCK else Qt.AlignRight)
                )
                self._table.setItem(trow, col, item)

    def _apply_detail_live(self) -> None:
        kpis = self._detail.get("kpis") or {}
        totals = kpis.get("totals") or {}
        prior = kpis.get("prior_totals") or {}
        avg = kpis.get("avg_per_day") or {}
        days = int(self._detail.get("days") or 1)
        label = str(self._detail.get("period_label") or self._year)
        cur = self._chart_currency
        key = _CURRENCY_KEY.get(cur, "tzs")
        if self._mix_title:
            self._mix_title.setText(
                str(self._detail.get("mix_label") or "Spend by Category")
            )
        if self._desc_title:
            self._desc_title.setText(
                str(self._detail.get("trucks_label") or "Top descriptions")
            )
        if self._kpi_total:
            self._kpi_total.set_value(
                _fmt_currency_short(cur, float(totals.get(key) or 0))
            )
            self._kpi_total.set_label(f"Total · {label}")
            self._kpi_total.set_period_text(self._period_chip())
        if self._kpi_avg:
            self._kpi_avg.set_value(
                _fmt_currency_short(cur, float(avg.get(key) or 0))
            )
            self._kpi_avg.set_label(f"Avg / day · {days}d")
            self._kpi_avg.set_period_text(self._period_chip())
        if self._kpi_count:
            self._kpi_count.set_value(f"{int(kpis.get('count') or 0):,}")
            self._kpi_count.set_label("Transactions")
            self._kpi_count.set_period_text(self._period_chip())
        if self._kpi_yoy:
            cur_amt = float(totals.get(key) or 0)
            prior_amt = float(prior.get(key) or 0)
            delta = cur_amt - prior_amt
            sign = "+" if delta > 0 else ""
            self._kpi_yoy.set_value(f"{sign}{_fmt_currency_short(cur, delta)}")
            pct = (delta / prior_amt * 100) if prior_amt else (100.0 if cur_amt else 0.0)
            self._kpi_yoy.set_label(f"YoY vs {self._year - 1} · {pct:+.0f}%")
            self._kpi_yoy.set_period_text(self._period_chip())
        self._refresh_detail_charts()

    def _period_chip(self) -> str:
        if self._period == "month":
            return f"{_MONTH_ABBR[self._month - 1]} only"
        return "Full year"

    def _refresh_detail_charts(self) -> None:
        self._refresh_trend()
        self._refresh_categories()
        self._refresh_descriptions()
        self._refresh_deltas()
        self._refresh_receipts()

    def _refresh_trend(self) -> None:
        trend = list(self._detail.get("month_trend") or [])
        prior = list(self._detail.get("prior_month_trend") or [])
        today = date.today()
        end_month = today.month if self._year == today.year else 12
        months = list(_MONTH_ABBR[:end_month])
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")

        def _raw(rows: list[dict]) -> list[float]:
            out = []
            for m in range(1, end_month + 1):
                row = next((r for r in rows if int(r.get("month") or 0) == m), None)
                out.append(float((row or {}).get(key) or 0.0))
            return out

        cur_vals, _ = _chart_values(self._chart_currency, _raw(trend))
        prior_vals, _ = _chart_values(self._chart_currency, _raw(prior))
        if self._line:
            self._line.set_data(
                months,
                [
                    (str(self._year - 1), "#F59E0B", prior_vals),
                    (str(self._year), _BLUE, cur_vals),
                ],
                empty="No spend for this period.",
            )

    def _refresh_categories(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        items = []
        for i, cat in enumerate(self._detail.get("categories") or []):
            amt = float(cat.get(key) or 0.0)
            if amt <= 0:
                continue
            items.append((
                str(cat.get("name") or "—"),
                amt,
                _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)],
            ))
        if self._treemap:
            self._treemap.set_data(items, empty="No category mix yet.")

    def _refresh_descriptions(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        rows = list(self._detail.get("trucks") or [])
        labels = [str(t.get("name") or "—") for t in rows]
        raw = [float(t.get(key) or 0.0) for t in rows]
        scaled, _ = _chart_values(self._chart_currency, raw)
        if self._desc_bars:
            self._desc_bars.set_data(labels, scaled, empty="No descriptions yet.")

    def _on_delta_mode(self, mode: str) -> None:
        mode = "mom" if mode == "mom" else "yoy"
        if mode == self._delta_mode:
            return
        self._delta_mode = mode
        for btn in self._delta_mode_btns:
            btn.setChecked(btn.property("delta_mode") == mode)
        if self._view == "detail" and bool(self._detail.get("ready")):
            self._refresh_deltas()

    def _refresh_deltas(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        mode = self._delta_mode
        bundle = (self._detail.get("deltas") or {}).get(mode) or {}
        series = bundle.get(key) if isinstance(bundle, dict) else []
        today = date.today()
        end_month = today.month if self._year == today.year else 12
        rows: list[tuple[str, float]] = []
        raw_deltas: list[float] = []
        for row in list(series or []):
            m = int(row.get("month") or 0)
            if not 1 <= m <= end_month:
                continue
            delta = float(row.get("delta") or 0.0)
            if abs(delta) < 1e-9:
                continue
            direction = str(row.get("direction") or "flat")
            arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "●")
            rows.append((f"{arrow} {_MONTH_ABBR[m - 1]}", abs(delta)))
            raw_deltas.append(abs(delta))
        scaled, _ = _chart_values(self._chart_currency, raw_deltas)
        scaled_rows = [
            (lab, scaled[i] if i < len(scaled) else 0.0)
            for i, (lab, _) in enumerate(rows)
        ]
        label = "MoM" if mode == "mom" else "YoY"
        if self._deltas_title:
            self._deltas_title.setText(f"Where spend rose / fell ({label})")
        if self._deltas:
            self._deltas.set_data(
                scaled_rows,
                empty=f"No {label} change in this period.",
            )

    def _refresh_receipts(self) -> None:
        receipts = self._detail.get("receipts") or {}
        received = int(receipts.get("received") or 0)
        pending = int(receipts.get("pending") or 0)
        missing = int(receipts.get("missing") or 0)
        total_n = received + pending + missing
        slices: list[tuple[str, float, str, str]] = []
        if total_n > 0:
            total = max(1, total_n)
            if received:
                slices.append(("Received", received / total, _GREEN, f"{received:,}"))
            if pending:
                slices.append(("Pending", pending / total, _AMBER, f"{pending:,}"))
            if missing:
                slices.append(("Missing", missing / total, _RED, f"{missing:,}"))
        if self._receipts:
            self._receipts.set_data(slices, empty="No receipt data yet.")
        if self._receipts_legend:
            while self._receipts_legend.count():
                item = self._receipts_legend.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if slices:
                self._receipts_legend.addWidget(_donut_legend(slices))
            else:
                self._receipts_legend.addWidget(
                    _lbl("No receipt data yet.", size=12, color=_TM)
                )

    def _on_row_clicked(self, row: int, _col: int) -> None:
        if not self._table:
            return
        item = self._table.item(row, _COL_TRUCK)
        if item is None:
            return
        truck = str(item.data(Qt.UserRole) or "").strip()
        if not self._is_real_plate(truck):
            return
        self._selected_truck = truck
        self._view = "detail"
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_detail())

    def _on_back(self) -> None:
        # Cancel in-flight detail, shrink charts, then flip to fleet.
        self._invalidate_loads()
        QToolTip.hideText()
        self._view = "fleet"
        self._selected_truck = ""
        self._detail = {}
        self._clear_detail_charts()
        self._apply_fleet()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = str(text or "")
        if self._view == "fleet":
            self._apply_fleet()

    def _on_kind_filter(self, kind: str) -> None:
        kind = (kind or "all").strip().lower()
        if kind not in {k for k, _ in _KIND_FILTERS}:
            kind = "all"
        if kind == self._fleet_kind:
            return
        self._fleet_kind = kind
        if self._kind_combo is not None:
            idx = self._kind_combo.findData(kind)
            if idx >= 0 and self._kind_combo.currentIndex() != idx:
                self._kind_combo.blockSignals(True)
                self._kind_combo.setCurrentIndex(idx)
                self._kind_combo.blockSignals(False)
        if self._view == "fleet":
            self._apply_fleet()

    def _on_kind_combo_changed(self, _index: int = 0) -> None:
        if self._kind_combo is None:
            return
        kind = str(self._kind_combo.currentData() or "all")
        self._on_kind_filter(kind)

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
        if not self._period_menu or not self._kpi_cards:
            return
        btn = self._kpi_cards[0].period_button()
        self._period_menu.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_currency_changed(self, currency: str) -> None:
        if currency == self._chart_currency:
            return
        self._chart_currency = currency
        for toggle in self._currency_toggles:
            if toggle.currency() != currency:
                toggle.set_currency(currency, emit=False)
        if self._view == "detail" and bool(self._detail.get("ready")):
            self._apply_detail_live()

    def _make_currency_toggle(self) -> _CurrencyToggle:
        toggle = _CurrencyToggle()
        toggle.changed.connect(self._on_currency_changed)
        self._currency_toggles.append(toggle)
        return toggle

    def _chart_shell(
        self,
        title: str,
        body: QWidget,
        *,
        with_currency: bool = True,
        title_out: list | None = None,
    ) -> QFrame:
        card = _card()
        card.setMinimumHeight(260)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(14, 12, 14, 12)
        vl.setSpacing(6)
        header = QHBoxLayout()
        title_lbl = _chart_title(title)
        if title_out is not None:
            title_out.append(title_lbl)
        header.addWidget(title_lbl)
        header.addStretch()
        if with_currency:
            header.addWidget(self._make_currency_toggle())
        vl.addLayout(header)
        vl.addWidget(body, 1)
        return card

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
        left.addWidget(_lbl("Trucks", size=22, weight=700))
        self._subtitle = _lbl(
            "Fleet Master spend by truck — TZS, USD, and ZMW.",
            size=13,
            color=_T2,
        )
        left.addWidget(self._subtitle)
        header.addLayout(left, 1)

        self._back_btn = QPushButton("← Fleet")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setFixedHeight(34)
        self._back_btn.setStyleSheet(self._menu_btn_style())
        self._back_btn.clicked.connect(self._on_back)
        self._back_btn.hide()
        header.addWidget(self._back_btn)

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

        self._period_btn = QPushButton("Full year")
        self._period_btn.setCursor(Qt.PointingHandCursor)
        self._period_btn.setFixedHeight(34)
        self._period_btn.setStyleSheet(self._menu_btn_style())
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
        self._period_btn.setMenu(self._period_menu)
        header.addWidget(self._period_btn)
        vl.addLayout(header)

        self._filter_bar = QWidget()
        self._filter_bar.setStyleSheet("background: transparent; border: none;")
        filter_row = QHBoxLayout(self._filter_bar)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search truck / plate…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedHeight(34)
        self._search_edit.setFixedWidth(260)
        self._search_edit.setStyleSheet(self._filter_input_style())
        self._search_edit.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self._search_edit)

        self._kind_combo = QComboBox()
        self._kind_combo.setFixedHeight(34)
        self._kind_combo.setMinimumWidth(160)
        self._kind_combo.setStyleSheet(self._filter_combo_style())
        for key, label in _KIND_FILTERS:
            self._kind_combo.addItem(label, key)
        idx = self._kind_combo.findData(self._fleet_kind)
        self._kind_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._kind_combo.currentIndexChanged.connect(self._on_kind_combo_changed)
        filter_row.addWidget(self._kind_combo)
        filter_row.addStretch(1)
        vl.addWidget(self._filter_bar)

        self._status_lbl = _lbl("", size=12, color=_AMBER, wrap=True)
        self._status_lbl.hide()
        vl.addWidget(self._status_lbl)

        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self._build_empty())
        self._body_stack.addWidget(self._build_table_card())
        self._body_stack.addWidget(self._build_detail())
        vl.addWidget(self._body_stack, 1)

        scroll.setWidget(content)
        root.addWidget(scroll)

    def _build_empty(self) -> QWidget:
        page = QFrame()
        page.setObjectName("analyticsCard")
        page.setStyleSheet(
            "QFrame#analyticsCard {"
            "  background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px;"
            "}"
        )
        lay = QVBoxLayout(page)
        lay.addStretch()
        msg = _lbl(
            "No truck spend for this period yet.",
            size=14,
            color=_T2,
            wrap=True,
        )
        msg.setAlignment(Qt.AlignCenter)
        lay.addWidget(msg)
        lay.addStretch()
        return page

    def _build_table_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("analyticsCard")
        card.setStyleSheet(
            "QFrame#analyticsCard {"
            "  background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 12px;"
            "}"
        )
        vl = QVBoxLayout(card)
        vl.setContentsMargins(12, 12, 12, 12)
        vl.setSpacing(8)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(list(_COLS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setFocusPolicy(Qt.StrongFocus)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_SN, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_TRUCK, QHeaderView.Stretch
        )
        for col in range(len(_COLS)):
            if col in {_COL_SN, _COL_TRUCK}:
                continue
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
            f"QTableWidget::item:selected {{ background: {_BLUE_L}; color: {_NAVY}; }}"
        )
        self._table.cellClicked.connect(self._on_row_clicked)
        vl.addWidget(self._table, 1)
        return card

    def _build_detail(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG}; border: none;")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(14)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self._kpi_total = _KPICard("mdi.cash-multiple", _NAVY, "#E8EEF4", "—", "Total")
        self._kpi_avg = _KPICard("mdi.calendar-clock", _BLUE, _BLUE_L, "—", "Avg / day")
        self._kpi_count = _KPICard("mdi.counter", _GREEN, _GREEN_L, "—", "Transactions")
        self._kpi_yoy = _KPICard("mdi.trending-up", _AMBER, _AMBER_L, "—", "YoY change")
        self._kpi_cards = [
            self._kpi_total, self._kpi_avg, self._kpi_count, self._kpi_yoy
        ]
        for card in self._kpi_cards:
            card.period_menu_requested.connect(self._popup_period_menu)
            kpi_row.addWidget(card)
        vl.addLayout(kpi_row)

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        self._line = _LineChartWidget()
        row1.addWidget(self._chart_shell("Spend Trend (YoY)", self._line), 1)
        self._treemap = _TreemapWidget()
        mix_titles: list[QLabel] = []
        row1.addWidget(
            self._chart_shell(
                "Spend by Category", self._treemap, title_out=mix_titles
            ),
            1,
        )
        self._mix_title = mix_titles[0] if mix_titles else None
        vl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        self._desc_bars = _VBarChartWidget()
        desc_titles: list[QLabel] = []
        row2.addWidget(
            self._chart_shell(
                "Top descriptions", self._desc_bars, title_out=desc_titles
            ),
            1,
        )
        self._desc_title = desc_titles[0] if desc_titles else None
        self._deltas = _HBarChartWidget()
        delta_card = _card()
        delta_card.setMinimumHeight(260)
        dvl = QVBoxLayout(delta_card)
        dvl.setContentsMargins(14, 12, 14, 12)
        dvl.setSpacing(6)
        dheader = QHBoxLayout()
        self._deltas_title = _chart_title("Where spend rose / fell (YoY)")
        dheader.addWidget(self._deltas_title)
        dheader.addStretch()
        mode_wrap = QHBoxLayout()
        mode_wrap.setSpacing(4)
        mode_group = QButtonGroup(delta_card)
        mode_group.setExclusive(True)
        self._delta_mode_btns = []
        for key, label in (("yoy", "YoY"), ("mom", "MoM")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setProperty("delta_mode", key)
            btn.setChecked(key == self._delta_mode)
            btn.setStyleSheet(self._delta_mode_btn_style())
            btn.clicked.connect(lambda _=False, k=key: self._on_delta_mode(k))
            mode_group.addButton(btn)
            mode_wrap.addWidget(btn)
            self._delta_mode_btns.append(btn)
        dheader.addLayout(mode_wrap)
        dheader.addWidget(self._make_currency_toggle())
        dvl.addLayout(dheader)
        dvl.addWidget(self._deltas, 1)
        row2.addWidget(delta_card, 1)

        receipt_card = _card()
        receipt_card.setMinimumHeight(260)
        rvl = QVBoxLayout(receipt_card)
        rvl.setContentsMargins(14, 12, 14, 12)
        rvl.setSpacing(6)
        rvl.addWidget(_chart_title("Receipt status"))
        self._receipts = _DonutChart()
        rvl.addWidget(self._receipts, 1, Qt.AlignHCenter)
        legend_wrap = QWidget()
        legend_wrap.setStyleSheet("background: transparent; border: none;")
        self._receipts_legend = QVBoxLayout(legend_wrap)
        self._receipts_legend.setContentsMargins(0, 0, 0, 0)
        rvl.addWidget(legend_wrap)
        row2.addWidget(receipt_card, 1)
        vl.addLayout(row2)
        return page

    @staticmethod
    def _filter_input_style() -> str:
        return (
            f"QLineEdit {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 12px; font-family: 'Segoe UI'; font-size: 13px; color: {_T1};"
            f"}}"
            f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
        )

    @staticmethod
    def _filter_combo_style() -> str:
        return (
            f"QComboBox {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 10px; font-family: 'Segoe UI'; font-size: 13px; color: {_T1};"
            f"}}"
            f"QComboBox:focus {{ border-color: {_BLUE}; }}"
            "QComboBox::drop-down { border: none; width: 22px; }"
            f"QComboBox QAbstractItemView {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER};"
            f"  selection-background-color: {_BLUE_L}; selection-color: {_NAVY};"
            f"  outline: none; padding: 4px;"
            f"}}"
        )

    @staticmethod
    def _delta_mode_btn_style() -> str:
        return f"""
            QPushButton {{
                background: {_WHITE};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                color: {_T2};
                font-size: 11px;
                font-weight: 600;
                font-family: 'Segoe UI';
                padding: 0 10px;
            }}
            QPushButton:hover {{ background: #F8FAFC; color: {_T1}; }}
            QPushButton:checked {{
                background: {_BLUE_L};
                border-color: {_BLUE};
                color: {_NAVY};
            }}
        """

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
