"""Analytics → Entities (earth tab): Items / Payees / Suppliers deep-dive."""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QMenu, QStackedWidget,
)

from tahmeed.ui.widgets.checkable_multi_combo import CheckableMultiCombo
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


class AnalyticsEntitiesPage(QWidget):
    """Earth-tab shell: bulk-select Items / Payees / Suppliers for combined deep-dive."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        today = date.today()
        self._year = today.year
        self._period = "year"
        self._month = today.month
        self._family = "mixed"
        self._entity_keys: list[str] = []
        self._chart_currency = "TZS"
        self._loading = False
        self._sources_loaded = False
        self._data: dict = {}
        self._families: list[dict] = []
        self._entities: list[dict] = []

        self._entity_combo: Optional[CheckableMultiCombo] = None
        self._load_timer: Optional[QTimer] = None
        self._year_btn: Optional[QPushButton] = None
        self._period_btn: Optional[QPushButton] = None
        self._period_menu: Optional[QMenu] = None
        self._body_stack: Optional[QStackedWidget] = None
        self._subtitle: Optional[QLabel] = None
        self._status_lbl: Optional[QLabel] = None

        self._kpi_total: Optional[_KPICard] = None
        self._kpi_avg: Optional[_KPICard] = None
        self._kpi_count: Optional[_KPICard] = None
        self._kpi_yoy: Optional[_KPICard] = None
        self._kpi_cards: list[_KPICard] = []
        self._line: Optional[_LineChartWidget] = None
        self._treemap: Optional[_TreemapWidget] = None
        self._mix_title: Optional[QLabel] = None
        self._trucks: Optional[_VBarChartWidget] = None
        self._trucks_title: Optional[QLabel] = None
        self._deltas: Optional[_HBarChartWidget] = None
        self._receipts: Optional[_DonutChart] = None
        self._receipts_legend: Optional[QVBoxLayout] = None
        self._receipts_title: Optional[QLabel] = None
        self._currency_toggles: list[_CurrencyToggle] = []

        self.setStyleSheet(
            f"AnalyticsEntitiesPage {{ background: {_BG}; border: none; }}"
            "AnalyticsEntitiesPage QLabel { border: none; }"
        )
        self._build()

    def refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._bootstrap())

    def ensure_loaded(self) -> None:
        if self._data:
            return
        self.refresh()

    async def _bootstrap(self) -> None:
        if not self._sources_loaded:
            await self._load_sources()
        await self._load()

    async def _load_sources(self) -> None:
        from tahmeed.services.accountant_service import get_analytics_entity_sources
        try:
            payload = await get_analytics_entity_sources()
            self._families = list(payload.get("families") or [])
            if not self._families:
                self._sources_loaded = False
                if self._status_lbl:
                    self._status_lbl.setText(
                        "Could not load entity catalog (empty response)."
                    )
                    self._status_lbl.show()
                return
            self._family = "mixed"
            self._populate_entities()
            self._sources_loaded = True
        except Exception as exc:
            self._sources_loaded = False
            if self._status_lbl:
                self._status_lbl.setText(f"Could not load entity catalog.\n{exc}")
                self._status_lbl.show()

    @staticmethod
    def _entity_display_label(ent: dict, family_key: str) -> str:
        raw = str(ent.get("label") or ent.get("key") or "").strip() or "—"
        prefix = {
            "items": "Item",
            "payees": "Payee",
            "suppliers": "Supplier",
        }.get(family_key, "Entity")
        # Avoid double-prefix if backend already stamped one.
        if raw.lower().startswith(f"{prefix.lower()} ·"):
            return raw
        return f"{prefix} · {raw}"

    def _populate_entities(self) -> None:
        # Flatten Items + Payees + Suppliers into one bulk-select catalog.
        flat: list[dict] = []
        for fam in self._families:
            fam_key = str(fam.get("key") or "")
            for ent in list(fam.get("entities") or []):
                key = str(ent.get("key") or "")
                if not key:
                    continue
                flat.append(
                    {
                        **ent,
                        "family": fam_key or str(ent.get("family") or ""),
                        "label": self._entity_display_label(ent, fam_key),
                    }
                )
        self._entities = flat
        if self._entity_combo is None:
            return
        self._entity_combo.configure(
            all_label="Select items, payees, suppliers…",
            noun_plural="entities",
        )
        pairs = [
            (str(ent.get("label") or ent.get("key") or ""), str(ent.get("key") or ""))
            for ent in self._entities
            if str(ent.get("key") or "")
        ]
        keep = [k for k in self._entity_keys if any(k == p[1] for p in pairs)]
        self._entity_combo.set_options_with_keys(pairs, keep_selected=False, emit=False)
        self._entity_combo.set_selected_keys(keep, emit=False)
        self._entity_keys = list(self._entity_combo.selected_values())

    async def _load(self) -> None:
        if self._loading:
            return
        keys = list(self._entity_keys)
        if not keys:
            self._data = {
                "ready": False,
                "mode": "merge",
                "label": "Entities",
                "message": (
                    "Search and tick one or more items, payees, or suppliers "
                    "above to begin deep analysis."
                ),
            }
            self._apply()
            return
        self._loading = True
        show_widget_loading(self, "Loading entity trends…")
        from tahmeed.services.accountant_service import get_analytics_entities
        try:
            month = self._month if self._period == "month" else None
            self._data = await get_analytics_entities(
                "mixed",
                keys,
                self._year,
                period=self._period,
                month=month,
            )
            self._apply()
        except Exception as exc:
            self._data = {
                "ready": False,
                "mode": "merge",
                "label": ", ".join(keys[:3]),
                "message": f"Could not load entity trends.\n{exc}",
            }
            self._apply()
        finally:
            self._loading = False
            hide_widget_loading(self)

    def _family_label(self) -> str:
        fam = str(self._data.get("family") or self._family or "mixed")
        if fam == "mixed":
            return "Entities"
        named = next(
            (f for f in self._families if str(f.get("key") or "") == fam),
            None,
        )
        return str((named or {}).get("label") or fam.title())

    def _apply(self) -> None:
        ready = bool(self._data.get("ready"))
        if not self._body_stack:
            return
        if not ready:
            msg = str(self._data.get("message") or "Select an entity.")
            if self._status_lbl:
                self._status_lbl.setText(msg)
                self._status_lbl.show()
            self._body_stack.setCurrentIndex(0)
            self._update_chrome()
            return
        if self._status_lbl:
            sparse = bool(self._data.get("sparse"))
            if sparse:
                self._status_lbl.setText(
                    f"No activity in {self._data.get('period_label') or self._year} "
                    "for this selection."
                )
                self._status_lbl.show()
            else:
                self._status_lbl.hide()
        self._body_stack.setCurrentIndex(1)
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
        if self._subtitle:
            label = str(self._data.get("label") or "—")
            fam = self._family_label()
            n = len(self._data.get("entities") or self._entity_keys or [])
            mix = str(self._data.get("mix_label") or "mix").lower()
            if n > 1:
                self._subtitle.setText(
                    f"{fam} · {n} selected: {label} — combined deep-dive."
                )
            else:
                self._subtitle.setText(
                    f"{fam} · {label} — deep-dive: {mix}, trucks, YoY deltas, receipts."
                )

    def _apply_live(self) -> None:
        kpis = self._data.get("kpis") or {}
        totals = kpis.get("totals") or {}
        prior = kpis.get("prior_totals") or {}
        avg = kpis.get("avg_per_day") or {}
        days = int(self._data.get("days") or 1)
        label = str(self._data.get("period_label") or self._year)
        cur = self._chart_currency
        key = _CURRENCY_KEY.get(cur, "tzs")
        if self._mix_title:
            self._mix_title.setText(
                str(self._data.get("mix_label") or "Spend by Description")
            )
        if self._trucks_title:
            self._trucks_title.setText(
                str(self._data.get("trucks_label") or "Spend by Truck")
            )
        if self._receipts_title:
            self._receipts_title.setText("Receipt status")
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
        for i, cat in enumerate(self._data.get("categories") or []):
            amt = float(cat.get(key) or 0.0)
            if amt <= 0:
                continue
            items.append((
                str(cat.get("name") or "—"),
                amt,
                _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)],
            ))
        if self._treemap:
            self._treemap.set_data(
                items,
                empty=f"No {str(self._data.get('mix_label') or 'mix').lower()} yet.",
            )

    def _refresh_trucks(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
        trucks = list(self._data.get("trucks") or [])
        labels = [str(t.get("name") or "—") for t in trucks]
        raw = [float(t.get(key) or 0.0) for t in trucks]
        scaled, _ = _chart_values(self._chart_currency, raw)
        if self._trucks:
            self._trucks.set_data(labels, scaled, empty="No truck spend yet.")

    def _refresh_deltas(self) -> None:
        key = _CURRENCY_KEY.get(self._chart_currency, "tzs")
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
            self._deltas.set_data(scaled_rows, empty="No YoY change in this period.")

    def _refresh_receipts(self) -> None:
        receipts = self._data.get("receipts") or {}
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

    def _on_entity_changed(self) -> None:
        if not self._entity_combo:
            return
        keys = list(self._entity_combo.selected_values())
        if keys == self._entity_keys:
            return
        self._entity_keys = keys
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
            self._apply_live()

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
        left.addWidget(_lbl("Entities", size=22, weight=700))
        self._subtitle = _lbl(
            "One filter for Items, Payees, and Suppliers — search, multi-select, combined deep-dive.",
            size=13,
            color=_T2,
        )
        left.addWidget(self._subtitle)
        header.addLayout(left, 1)

        self._entity_combo = CheckableMultiCombo(
            all_label="Select items, payees, suppliers…",
            noun_plural="entities",
            searchable=True,
            parent=self,
        )
        self._entity_combo.setMinimumWidth(360)
        self._entity_combo.setFixedHeight(34)
        self._entity_combo.setStyleSheet(
            f"QComboBox {{"
            f"  background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 8px;"
            f"  padding: 0 10px; font-size: 13px; font-family: 'Segoe UI'; color: {_T1};"
            f"}}"
            "QComboBox::drop-down { border: none; width: 24px; }"
        )
        self._entity_combo.selectionChanged.connect(self._on_entity_changed)
        header.addWidget(self._entity_combo)

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

        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self._build_empty())  # 0
        self._body_stack.addWidget(self._build_live())   # 1
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
        msg = _lbl(
            "Search and tick one or more items, payees, or suppliers. "
            "Multiple selections combine into a single deep-dive.",
            size=14,
            color=_T2,
            wrap=True,
        )
        msg.setAlignment(Qt.AlignCenter)
        vl.addWidget(msg)
        vl.addStretch()
        return page

    def _build_live(self) -> QWidget:
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
                "Spend by Description", self._treemap, title_out=mix_titles
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
        row2.addWidget(
            self._chart_shell("Where spend rose / fell (YoY)", self._deltas), 1
        )

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
