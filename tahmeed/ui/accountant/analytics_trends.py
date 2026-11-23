"""Analytics → Trends: source picker + deep-dive + cross-source compare."""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QMenu, QComboBox,
    QStackedWidget,
)

from tahmeed.ui.widgets.checkable_multi_combo import CheckableMultiCombo
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.overview_geometry import OverviewGeometryContent

# Reuse chart kit + tokens from the main Analytics page.
from tahmeed.ui.accountant.analytics import (
    _BG, _WHITE, _BORDER, _NAVY, _BLUE, _BLUE_L, _GREEN, _GREEN_L,
    _AMBER, _AMBER_L, _RED, _ORANGE, _T1, _T2, _TM, _TITLE,
    _TREEMAP_COLORS, _MONTH_ABBR, _CURRENCY_KEY,
    _lbl, _card, _chart_title, _fmt_currency_short, _chart_values,
    _CurrencyToggle, _KPICard, _LineChartWidget, _TreemapWidget,
    _VBarChartWidget, _HBarChartWidget, _DonutChart, _donut_legend,
)

_COMPARE_MAX = 4


class AnalyticsTrendsPage(QWidget):
    """Trends shell: analyze one source, or compare 2–4 ready sources."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        today = date.today()
        self._year = today.year
        self._period = "year"
        self._month = today.month
        self._source = "master"
        self._compare_keys: list[str] = []
        self._mode = "analyze"  # analyze | compare
        self._chart_currency = "TZS"
        self._loading = False
        self._sources_loaded = False
        self._data: dict = {}
        self._groups: list[dict] = []
        self._source_meta: dict[str, dict] = {}

        self._mode_btns: list[QPushButton] = []
        self._picker_stack: Optional[QStackedWidget] = None
        self._source_combo: Optional[QComboBox] = None
        self._compare_combo: Optional[CheckableMultiCombo] = None
        self._load_timer: Optional[QTimer] = None
        self._year_btn: Optional[QPushButton] = None
        self._period_btn: Optional[QPushButton] = None
        self._period_menu: Optional[QMenu] = None
        self._body_stack: Optional[QStackedWidget] = None
        self._empty_lbl: Optional[QLabel] = None
        self._pending_lbl: Optional[QLabel] = None

        self._kpi_total: Optional[_KPICard] = None
        self._kpi_avg: Optional[_KPICard] = None
        self._kpi_count: Optional[_KPICard] = None
        self._kpi_yoy: Optional[_KPICard] = None
        self._kpi_cards: list[_KPICard] = []

        self._line: Optional[_LineChartWidget] = None
        self._treemap: Optional[_TreemapWidget] = None
        self._mix_title: Optional[QLabel] = None
        self._trucks_title: Optional[QLabel] = None
        self._subtitle: Optional[QLabel] = None
        self._trucks: Optional[_VBarChartWidget] = None
        self._deltas: Optional[_HBarChartWidget] = None
        self._receipts: Optional[_DonutChart] = None
        self._receipts_legend: Optional[QVBoxLayout] = None
        self._receipts_title: Optional[QLabel] = None
        self._status_lbl: Optional[QLabel] = None
        self._currency_toggles: list[_CurrencyToggle] = []

        self._cmp_kpi_row: Optional[QHBoxLayout] = None
        self._cmp_kpi_cards: list[_KPICard] = []
        self._cmp_line: Optional[_LineChartWidget] = None
        self._cmp_rank: Optional[_HBarChartWidget] = None

        self.setStyleSheet(
            f"AnalyticsTrendsPage {{ background: {_BG}; border: none; }}"
            "AnalyticsTrendsPage QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._bootstrap())

    async def _bootstrap(self) -> None:
        if not self._sources_loaded:
            await self._load_sources()
        await self._load()

    def _ready_source_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for group in self._groups:
            for src in list(group.get("sources") or []):
                if not bool(src.get("ready")):
                    continue
                key = str(src.get("key") or "")
                label = str(src.get("label") or key)
                if key:
                    pairs.append((label, key))
        return pairs

    async def _load_sources(self) -> None:
        from tahmeed.services.accountant_service import get_analytics_trend_sources
        try:
            payload = await get_analytics_trend_sources()
            self._groups = list(payload.get("groups") or [])
            self._source_meta = {}
            if self._source_combo is not None:
                self._source_combo.blockSignals(True)
                self._source_combo.clear()
                for group in self._groups:
                    title = str(group.get("group") or "")
                    sources = list(group.get("sources") or [])
                    if not sources:
                        continue
                    # Visual group separator via disabled item
                    idx = self._source_combo.count()
                    self._source_combo.addItem(f"—— {title} ——")
                    self._source_combo.model().item(idx).setEnabled(False)
                    for src in sources:
                        key = str(src.get("key") or "")
                        label = str(src.get("label") or key)
                        ready = bool(src.get("ready"))
                        phase = int(src.get("phase") or 0)
                        mark = "" if ready else f"  (Phase {phase})"
                        self._source_combo.addItem(f"{label}{mark}", key)
                        self._source_meta[key] = dict(src)
                # Select master
                for i in range(self._source_combo.count()):
                    if self._source_combo.itemData(i) == "master":
                        self._source_combo.setCurrentIndex(i)
                        break
                self._source_combo.blockSignals(False)
            if self._compare_combo is not None:
                pairs = self._ready_source_pairs()
                keep = [k for k in self._compare_keys if any(k == p[1] for p in pairs)]
                self._compare_combo.set_options_with_keys(
                    pairs, keep_selected=False, emit=False
                )
                self._compare_combo.set_selected_keys(keep, emit=False)
                self._compare_keys = list(self._compare_combo.selected_values())
            self._sources_loaded = True
            self._sync_mode_buttons()
        except Exception:
            self._sources_loaded = False

    async def _load(self) -> None:
        if self._loading:
            return
        self._sync_mode_buttons()
        self._loading = True
        mode = self._mode
        show_widget_loading(
            self,
            "Comparing sources…" if mode == "compare" else "Loading trends…",
        )
        from tahmeed.services.accountant_service import (
            get_analytics_trends,
            get_analytics_trends_compare,
        )
        try:
            month = self._month if self._period == "month" else None
            if mode == "compare":
                keys = list(self._compare_keys)[:_COMPARE_MAX]
                if len(keys) < 2:
                    self._data = {
                        "ready": False,
                        "mode": "compare",
                        "label": "Compare",
                        "message": "Select 2–4 ready sources to compare.",
                    }
                else:
                    self._data = await get_analytics_trends_compare(
                        keys,
                        self._year,
                        period=self._period,
                        month=month,
                    )
            else:
                self._data = await get_analytics_trends(
                    self._source,
                    self._year,
                    period=self._period,
                    month=month,
                )
            self._apply()
        except Exception as exc:
            self._data = {
                "ready": False,
                "mode": mode,
                "label": self._source if mode != "compare" else "Compare",
                "message": f"Could not load trends.\n{exc}",
            }
            self._apply()
        finally:
            self._loading = False
            hide_widget_loading(self)

    def _apply(self) -> None:
        ready = bool(self._data.get("ready"))
        if not self._body_stack:
            return
        mode = str(self._data.get("mode") or self._mode)
        if not ready:
            msg = str(self._data.get("message") or "Coming soon.")
            label = str(self._data.get("label") or self._source)
            phase = int(self._data.get("phase") or 0)
            if mode == "compare":
                if self._empty_lbl:
                    self._empty_lbl.setText(msg)
                self._body_stack.setCurrentIndex(0)
            else:
                if self._pending_lbl:
                    self._pending_lbl.setText(
                        f"{label}\n\n{msg}"
                        + (f"\n(Planned Phase {phase})" if phase else "")
                    )
                self._body_stack.setCurrentIndex(1)
            self._update_chrome()
            return

        if mode == "compare":
            self._body_stack.setCurrentIndex(3)
            self._update_chrome()
            self._apply_compare()
            return

        self._body_stack.setCurrentIndex(2)
        self._update_chrome()
        self._apply_live()

    def _update_chrome(self) -> None:
        if self._year_btn:
            self._year_btn.setText(f"Year {self._year}")
        if self._period_btn:
            if self._period == "month":
                self._period_btn.setText(f"{_MONTH_ABBR[self._month - 1]} only")
            else:
                self._period_btn.setText("Full year")
        self._sync_mode_buttons()
        if self._picker_stack is not None:
            self._picker_stack.setCurrentIndex(1 if self._mode == "compare" else 0)
        if self._subtitle:
            mode = str(self._data.get("mode") or self._mode)
            if mode == "compare":
                n = len(self._data.get("sources") or self._compare_keys or [])
                label = str(self._data.get("label") or "—")
                self._subtitle.setText(
                    f"Compare {n} sources · {label} — overlay trend + ranked totals."
                )
            else:
                src_label = str(self._data.get("label") or self._source)
                kind = str(self._data.get("kind") or "")
                if kind == "item":
                    self._subtitle.setText(
                        f"ITEMS · {src_label} — trend, description mix, trucks, YoY deltas, receipts."
                    )
                elif kind == "master":
                    self._subtitle.setText(
                        "Master Expenses — trend, category mix, trucks, YoY deltas, receipts."
                    )
                elif kind == "fuel":
                    if str(self._data.get("source") or "") == "diesel_cash":
                        self._subtitle.setText(
                            "Fuel · Diesel Cash — money + litres (from description), items, trucks, receipts."
                        )
                    else:
                        self._subtitle.setText(
                            f"Fuel · {src_label} — money + litres trends, stations, trucks, destinations."
                        )
                elif kind == "separate":
                    native = str(self._data.get("native_currency") or "").upper() or "—"
                    self._subtitle.setText(
                        f"{src_label} · {native} — signature mix, trucks/plates, YoY deltas."
                    )
                elif kind == "insurance":
                    self._subtitle.setText(
                        f"{src_label} · TZS premiums — cards/status, trucks, cover months, YoY."
                    )
                elif kind == "outlier":
                    if str(self._data.get("source") or "") == "sm_burhani":
                        self._subtitle.setText(
                            f"{src_label} · USD — bonds/RPA, stations, confirmation, YoY."
                        )
                    else:
                        self._subtitle.setText(
                            f"{src_label} · USD — periods, trucks, invoice variance, YoY."
                        )
                elif kind == "ledger":
                    self._subtitle.setText(
                        f"{src_label} · USD ledger — called out, money in, balance, trucks, descriptions."
                    )
                else:
                    self._subtitle.setText(
                        "Pick a monetary tab — or Compare 2–4 ready sources."
                    )
        self._update_status()

    def _update_status(self) -> None:
        if not self._status_lbl:
            return
        if not bool(self._data.get("ready")):
            self._status_lbl.setText("")
            self._status_lbl.hide()
            return
        kpis = self._data.get("kpis") or {}
        count = int(kpis.get("count") or 0)
        totals = (kpis.get("totals") or {})
        sparse = bool(self._data.get("sparse")) or (
            count == 0
            and not any(float(totals.get(k) or 0.0) for k in ("tzs", "usd", "zmw"))
        )
        if sparse:
            period = str(self._data.get("period_label") or self._year)
            self._status_lbl.setText(
                f"No activity in {period} for this source — charts stay empty until data lands."
            )
            self._status_lbl.show()
        else:
            self._status_lbl.setText("")
            self._status_lbl.hide()

    def _chart_empty_hints(self) -> dict[str, str]:
        mix = str(self._data.get("mix_label") or "Spend by Category")
        trucks = str(self._data.get("trucks_label") or "Spend by Truck")
        slice_label = str(self._data.get("slice_label") or "").strip()
        kind = str(self._data.get("kind") or "")
        if kind == "fuel" and str(self._data.get("source") or "") != "diesel_cash":
            third = "No destination spend yet."
        elif slice_label:
            third = f"No {slice_label.lower()} yet."
        else:
            third = "No receipt data yet."
        return {
            "trend": "No spend for this period.",
            "mix": f"No {mix.lower()} yet.",
            "trucks": f"No {trucks.lower()} yet.",
            "deltas": "No YoY change in this period.",
            "third": third,
        }

    def _apply_live(self) -> None:
        kind = str(self._data.get("kind") or "")
        native = str(self._data.get("native_currency") or "").upper()
        if (
            kind in ("fuel", "separate", "ledger", "insurance", "outlier")
            and native in _CURRENCY_KEY
            and native != self._chart_currency
        ):
            self._chart_currency = native
            for toggle in self._currency_toggles:
                if toggle.currency() != native:
                    toggle.set_currency(native, emit=False)

        kpis = self._data.get("kpis") or {}
        totals = kpis.get("totals") or {}
        prior = kpis.get("prior_totals") or {}
        avg = kpis.get("avg_per_day") or {}
        days = int(self._data.get("days") or 1)
        label = str(self._data.get("period_label") or self._year)
        cur = self._chart_currency
        key = _CURRENCY_KEY.get(cur, "tzs")
        mix_label = str(self._data.get("mix_label") or "Spend by Category")
        if self._mix_title:
            self._mix_title.setText(mix_label)
        if self._trucks_title:
            self._trucks_title.setText(
                str(self._data.get("trucks_label") or "Spend by Truck")
            )
        if self._receipts_title:
            dests = list(self._data.get("destinations") or [])
            slice_label = str(self._data.get("slice_label") or "").strip()
            if dests and slice_label:
                self._receipts_title.setText(slice_label)
            elif kind == "fuel" and dests:
                self._receipts_title.setText("Spend by Destination")
            else:
                self._receipts_title.setText("Receipt status")

        if self._kpi_total:
            if kind == "ledger":
                self._kpi_total.set_value(
                    _fmt_currency_short(cur, float(kpis.get("money_out") or 0))
                )
                self._kpi_total.set_label(f"Called Out · {label}")
            else:
                self._kpi_total.set_value(
                    _fmt_currency_short(cur, float(totals.get(key) or 0))
                )
                self._kpi_total.set_label(f"Total · {label}")
            self._kpi_total.set_period_text(self._period_chip())
        if self._kpi_avg:
            if kind == "fuel":
                litres_avg = float(kpis.get("avg_liters_per_day") or 0.0)
                self._kpi_avg.set_value(f"{litres_avg:,.0f} L")
                self._kpi_avg.set_label(f"Avg litres / day · {days}d")
            elif kind == "ledger":
                self._kpi_avg.set_value(
                    _fmt_currency_short(cur, float(kpis.get("money_in_abs") or 0))
                )
                self._kpi_avg.set_label("Money In")
            else:
                self._kpi_avg.set_value(_fmt_currency_short(cur, float(avg.get(key) or 0)))
                self._kpi_avg.set_label(f"Avg / day · {days}d")
            self._kpi_avg.set_period_text(self._period_chip())
        if self._kpi_count:
            if kind == "fuel":
                litres = float(kpis.get("liters") or 0.0)
                self._kpi_count.set_value(f"{litres:,.0f} L")
                self._kpi_count.set_label(
                    f"Litres · {int(kpis.get('count') or 0):,} fills"
                )
            elif kind == "ledger":
                bal = float(kpis.get("balance") or 0.0)
                sign = "+" if bal > 0 else ""
                self._kpi_count.set_value(f"{sign}{_fmt_currency_short(cur, bal)}")
                self._kpi_count.set_label(
                    f"Balance · {int(kpis.get('count') or 0):,} txs"
                )
            else:
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
            self._kpi_yoy.set_label(
                f"{'Called Out YoY' if kind == 'ledger' else 'YoY'} vs {self._year - 1} · {pct:+.0f}%"
            )
            self._kpi_yoy.set_period_text(self._period_chip())

        self._update_status()
        self._refresh_charts()

    def _period_chip(self) -> str:
        if self._period == "month":
            return f"{_MONTH_ABBR[self._month - 1]} only"
        return "Full year"

    def _refresh_charts(self) -> None:
        self._refresh_trend()
        self._refresh_categories()
        self._refresh_trucks()
        self._refresh_deltas()
        self._refresh_receipts()

    def _refresh_trend(self) -> None:
        trend = list(self._data.get("month_trend") or [])
        prior = list(self._data.get("prior_month_trend") or [])
        today = date.today()
        end_month = today.month if self._year == today.year else 12
        months = list(_MONTH_ABBR[:end_month])
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        kind = str(self._data.get("kind") or "")

        def _raw(rows: list[dict], field: str) -> list[float]:
            out = []
            for m in range(1, end_month + 1):
                row = next((r for r in rows if int(r.get("month") or 0) == m), None)
                out.append(float((row or {}).get(field) or 0.0))
            return out

        cur_vals, _ = _chart_values(self._chart_currency, _raw(trend, key))
        prior_vals, _ = _chart_values(self._chart_currency, _raw(prior, key))
        series = [
            (str(self._year - 1), _ORANGE, prior_vals),
            (str(self._year), _BLUE, cur_vals),
        ]
        if kind == "fuel":
            # Overlay litres on a readable scale (thousands when large).
            lit_cur = _raw(trend, "liters")
            lit_prior = _raw(prior, "liters")
            max_l = max(lit_cur + lit_prior, default=0.0)
            scale = 1000.0 if max_l >= 10_000 else 1.0
            unit = "kL" if scale > 1 else "L"
            series.extend(
                [
                    (f"{self._year - 1} {unit}", _AMBER, [v / scale for v in lit_prior]),
                    (f"{self._year} {unit}", _GREEN, [v / scale for v in lit_cur]),
                ]
            )
        elif kind == "ledger":
            # Overlay Money In as absolute USD (stored signed-negative).
            in_cur = [abs(v) for v in _raw(trend, "money_in")]
            in_prior = [abs(v) for v in _raw(prior, "money_in")]
            series.extend(
                [
                    (f"{self._year - 1} In", _AMBER, in_prior),
                    (f"{self._year} In", _GREEN, in_cur),
                ]
            )
        hints = self._chart_empty_hints()
        if self._line:
            self._line.set_data(
                months,
                series,
                empty=hints["trend"],
            )

    def _refresh_categories(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        kind = str(self._data.get("kind") or "")
        hints = self._chart_empty_hints()
        # Fuel uses stations as the mix; backend also mirrors them into categories.
        rows = (
            list(self._data.get("stations") or self._data.get("categories") or [])
            if kind == "fuel"
            else list(self._data.get("categories") or [])
        )
        items = []
        for i, cat in enumerate(rows):
            amt = float(cat.get(key) or 0.0)
            if amt <= 0:
                continue
            items.append((
                str(cat.get("name") or ("—" if kind == "fuel" else "Uncategorised")),
                amt,
                _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)],
            ))
        if self._treemap:
            self._treemap.set_data(items, empty=hints["mix"])

    def _refresh_trucks(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        hints = self._chart_empty_hints()
        trucks = list(self._data.get("trucks") or [])
        labels = [str(t.get("name") or "—") for t in trucks]
        raw = [float(t.get(key) or 0.0) for t in trucks]
        scaled, _ = _chart_values(self._chart_currency, raw)
        if self._trucks:
            self._trucks.set_data(labels, scaled, empty=hints["trucks"])

    def _refresh_deltas(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        hints = self._chart_empty_hints()
        yoy = ((self._data.get("deltas") or {}).get("yoy") or {}).get(key) or []
        today = date.today()
        end_month = today.month if self._year == today.year else 12
        rows: list[tuple[str, float]] = []
        raw_deltas: list[float] = []
        for row in yoy:
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
        if self._deltas:
            self._deltas.set_data(scaled_rows, empty=hints["deltas"])

    def _refresh_receipts(self) -> None:
        kind = str(self._data.get("kind") or "")
        dests = list(self._data.get("destinations") or [])
        slice_label = str(self._data.get("slice_label") or "").strip()
        hints = self._chart_empty_hints()
        empty_msg = hints["third"]
        slices: list[tuple[str, float, str, str]] = []
        if dests and (
            kind in ("fuel", "separate", "ledger", "insurance", "outlier")
            or slice_label
        ):
            key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
            amounts = [
                (str(d.get("name") or "—"), float(d.get(key) or 0.0))
                for d in dests
            ]
            total = sum(a for _, a in amounts)
            if total > 0:
                for i, (name, amt) in enumerate(amounts):
                    if amt <= 0:
                        continue
                    slices.append((
                        name,
                        amt / total,
                        _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)],
                        _fmt_currency_short(self._chart_currency, amt),
                    ))
        else:
            receipts = self._data.get("receipts") or {}
            received = int(receipts.get("received") or 0)
            pending = int(receipts.get("pending") or 0)
            missing = int(receipts.get("missing") or 0)
            total_n = received + pending + missing
            if total_n > 0:
                total = max(1, total_n)
                if received:
                    slices.append(("Received", received / total, _GREEN, f"{received:,}"))
                if pending:
                    slices.append(("Pending", pending / total, _AMBER, f"{pending:,}"))
                if missing:
                    slices.append(("Missing", missing / total, _RED, f"{missing:,}"))
        if self._receipts:
            self._receipts.set_data(slices, empty=empty_msg)
        if self._receipts_legend:
            while self._receipts_legend.count():
                item = self._receipts_legend.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            if slices:
                self._receipts_legend.addWidget(_donut_legend(slices))
            else:
                self._receipts_legend.addWidget(
                    _lbl(empty_msg, size=12, color=_TM)
                )

    def _on_source_changed(self, _index: int = 0) -> None:
        if not self._source_combo:
            return
        key = self._source_combo.currentData()
        if not key or key == self._source:
            return
        self._source = str(key)
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    def _on_mode(self, mode: str) -> None:
        mode = "compare" if mode == "compare" else "analyze"
        if mode == self._mode:
            return
        self._mode = mode
        self._sync_mode_buttons()
        if self._picker_stack is not None:
            self._picker_stack.setCurrentIndex(1 if mode == "compare" else 0)
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    def _sync_mode_buttons(self) -> None:
        can_compare = len(self._ready_source_pairs()) >= 2
        for btn in self._mode_btns:
            key = str(btn.property("mode_key") or "")
            btn.setChecked(key == self._mode)
            if key == "compare":
                btn.setEnabled(can_compare or self._mode == "compare")

    def _on_compare_changed(self) -> None:
        if not self._compare_combo:
            return
        keys = list(self._compare_combo.selected_values())
        if keys == self._compare_keys:
            return
        self._compare_keys = keys
        if self._mode != "compare":
            return
        if self._load_timer is None:
            self._load_timer = QTimer(self)
            self._load_timer.setSingleShot(True)
            self._load_timer.timeout.connect(self._debounced_load)
        self._load_timer.start(350)

    def _debounced_load(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    def _on_year_selected(self, year: int) -> None:
        if year == self._year:
            return
        self._year = year
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

    def _on_period_selected(self, period: str, month: int | None = None) -> None:
        period = "month" if period == "month" else "year"
        if period == self._period and (period == "year" or month == self._month):
            return
        self._period = period
        if period == "month" and month is not None:
            self._month = month
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load())

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
        if bool(self._data.get("ready")):
            if str(self._data.get("mode") or self._mode) == "compare":
                self._apply_compare()
            else:
                self._apply_live()

    def _apply_compare(self) -> None:
        series = list(self._data.get("series") or [])
        ranked = list(self._data.get("ranked") or [])
        cur = self._chart_currency
        key = _CURRENCY_KEY.get(cur, "tzs")
        label = str(self._data.get("period_label") or self._year)

        while self._cmp_kpi_cards and len(self._cmp_kpi_cards) > len(series):
            card = self._cmp_kpi_cards.pop()
            if self._cmp_kpi_row is not None:
                self._cmp_kpi_row.removeWidget(card)
            card.deleteLater()
        while len(self._cmp_kpi_cards) < len(series) and self._cmp_kpi_row is not None:
            card = _KPICard("mdi.circle", _BLUE, _BLUE_L, "—", "Source")
            card.period_menu_requested.connect(self._popup_period_menu)
            self._cmp_kpi_cards.append(card)
            self._cmp_kpi_row.addWidget(card)
        for i, row in enumerate(series):
            if i >= len(self._cmp_kpi_cards):
                break
            card = self._cmp_kpi_cards[i]
            totals = row.get("totals") or {}
            prior = row.get("prior_totals") or {}
            amt = float(totals.get(key) or 0.0)
            prior_amt = float(prior.get(key) or 0.0)
            delta = amt - prior_amt
            sign = "+" if delta > 0 else ""
            card.set_value(_fmt_currency_short(cur, amt))
            card.set_label(str(row.get("label") or f"Source {i + 1}"))
            card.set_period_text(
                f"{label} · YoY {sign}{_fmt_currency_short(cur, delta)}"
            )

        today = date.today()
        end_month = today.month if self._year == today.year else 12
        months = list(_MONTH_ABBR[:end_month])
        chart_series: list[tuple[str, str, list[float]]] = []
        for row in series:
            trend = list(row.get("month_trend") or [])
            raw = []
            for m in range(1, end_month + 1):
                hit = next((r for r in trend if int(r.get("month") or 0) == m), None)
                raw.append(float((hit or {}).get(key) or 0.0))
            vals, _ = _chart_values(cur, raw)
            chart_series.append(
                (
                    str(row.get("label") or row.get("key") or "—"),
                    str(row.get("color") or _BLUE),
                    vals,
                )
            )
        if self._cmp_line:
            self._cmp_line.set_data(
                months, chart_series, empty="No spend for this period."
            )

        rank_rows: list[tuple[str, float]] = []
        raw_vals: list[float] = []
        for row in ranked:
            amt = float(row.get(key) or 0.0)
            if abs(amt) < 1e-9:
                continue
            rank_rows.append((str(row.get("label") or "—"), abs(amt)))
            raw_vals.append(abs(amt))
        scaled, _ = _chart_values(cur, raw_vals)
        scaled_rows = [
            (lab, scaled[i] if i < len(scaled) else 0.0)
            for i, (lab, _) in enumerate(rank_rows)
        ]
        if self._cmp_rank:
            self._cmp_rank.set_data(
                scaled_rows, empty="No totals to rank for this selection."
            )

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

        # Header
        header = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(_lbl("Trends", size=22, weight=700))
        self._subtitle = _lbl(
            "Pick a monetary tab — all monetary sources are live.",
            size=13,
            color=_T2,
        )
        left.addWidget(self._subtitle)
        header.addLayout(left, 1)

        for key, label in (("analyze", "Analyze"), ("compare", "Compare")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setProperty("mode_key", key)
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
                f"  padding: 0 12px; font-size: 13px; font-weight: 600; color: {_T1};"
                f"}}"
                f"QPushButton:checked {{ background: {_AMBER_L}; border-color: {_AMBER}; color: {_NAVY}; }}"
                f"QPushButton:disabled {{ color: {_TM}; background: #F8FAFC; }}"
            )
            btn.clicked.connect(lambda _=False, k=key: self._on_mode(k))
            self._mode_btns.append(btn)
            header.addWidget(btn)
        if self._mode_btns:
            self._mode_btns[0].setChecked(True)

        self._picker_stack = QStackedWidget()
        self._picker_stack.setFixedHeight(34)

        self._source_combo = QComboBox()
        self._source_combo.setMinimumWidth(240)
        self._source_combo.setFixedHeight(34)
        self._source_combo.setStyleSheet(
            f"QComboBox {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 10px; font-size: 13px; font-family: 'Segoe UI'; color: {_T1};"
            f"}}"
            "QComboBox::drop-down { border: none; width: 24px; }"
            f"QComboBox QAbstractItemView {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; selection-background-color: {_BLUE_L};"
            f"}}"
        )
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._picker_stack.addWidget(self._source_combo)

        self._compare_combo = CheckableMultiCombo(
            all_label="Select sources…",
            noun_plural="sources",
            parent=self,
        )
        self._compare_combo.setMinimumWidth(260)
        self._compare_combo.setFixedHeight(34)
        self._compare_combo.setStyleSheet(
            f"QComboBox {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 10px; font-size: 13px; font-family: 'Segoe UI'; color: {_T1};"
            f"}}"
            "QComboBox::drop-down { border: none; width: 24px; }"
        )
        self._compare_combo.selectionChanged.connect(self._on_compare_changed)
        self._picker_stack.addWidget(self._compare_combo)
        header.addWidget(self._picker_stack)

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

        self._status_lbl = _lbl("", size=12, color=_AMBER, wrap=True)
        self._status_lbl.hide()
        vl.addWidget(self._status_lbl)

        # Body stack: empty | pending | analyze live | compare
        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self._build_empty())
        self._body_stack.addWidget(self._build_pending())
        self._body_stack.addWidget(self._build_master())
        self._body_stack.addWidget(self._build_compare())
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
        vl = QVBoxLayout(page)
        vl.addStretch()
        self._empty_lbl = _lbl(
            "Choose a monetary source to analyze, or Compare 2–4 ready sources.",
            size=14,
            color=_T2,
            wrap=True,
        )
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        vl.addWidget(self._empty_lbl)
        vl.addStretch()
        return page

    def _build_pending(self) -> QWidget:
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
        self._pending_lbl = _lbl("Coming soon", size=16, color=_T2, wrap=True)
        self._pending_lbl.setAlignment(Qt.AlignCenter)
        vl.addWidget(self._pending_lbl)
        vl.addStretch()
        return page

    def _build_master(self) -> QWidget:
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
        self._kpi_cards = [self._kpi_total, self._kpi_avg, self._kpi_count, self._kpi_yoy]
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
        self._trucks = _VBarChartWidget()
        truck_titles: list[QLabel] = []
        row2.addWidget(
            self._chart_shell(
                "Spend by Truck", self._trucks, title_out=truck_titles
            ),
            1,
        )
        self._trucks_title = truck_titles[0] if truck_titles else None
        self._deltas = _HBarChartWidget()
        row2.addWidget(self._chart_shell("Where spend rose / fell (YoY)", self._deltas), 1)

        receipt_card = _card()
        receipt_card.setMinimumHeight(260)
        rvl = QVBoxLayout(receipt_card)
        rvl.setContentsMargins(14, 12, 14, 12)
        rvl.setSpacing(6)
        self._receipts_title = _chart_title("Receipt status")
        rvl.addWidget(self._receipts_title)
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

    def _build_compare(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG}; border: none;")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(14)

        self._cmp_kpi_row = QHBoxLayout()
        self._cmp_kpi_row.setSpacing(10)
        vl.addLayout(self._cmp_kpi_row)

        row = QHBoxLayout()
        row.setSpacing(12)
        self._cmp_line = _LineChartWidget()
        row.addWidget(
            self._chart_shell("Spend trend (overlay)", self._cmp_line), 2
        )
        self._cmp_rank = _HBarChartWidget()
        row.addWidget(
            self._chart_shell("Ranked totals", self._cmp_rank), 1
        )
        vl.addLayout(row)
        return page

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
