"""Batch review when register rows match recent transactions.

Each match is shown as a compact pair: the new row on top, the existing
expense underneath (expense/saved tint) so both can be compared at a glance.

Pills filter by source (All / In system / Same table) plus optional
item/description facets. A compact Trucks badge shows how many distinct
trucks are in the current list. Lookback days/months can request a rescan.
"""

from __future__ import annotations

from typing import Dict, List, Set

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.duplicate_review import (
    DuplicateReviewItem,
    LookbackOverrides,
    convert_lookback_value,
    filter_duplicate_items,
    format_amount_label,
    format_existing_date,
    format_lookback_label,
    group_counts,
    lookback_days_from_unit,
    normalize_lookback_overrides,
)
from tahmeed.ui.dialogs.lookback_overrides_dialog import LookbackOverridesDialog

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BORDER = "#E5E7EB"
_AMBER = "#B45309"
_NEW_BG = "#FEE2E2"       # light red — candidate being saved
_EXISTING_BG = "#fff8f0"  # matches register saved/expense row tint
_ROW_H = 22

_HEADERS = (
    "Save",
    "",
    "#",
    "Date",
    "Description",
    "Truck",
    "Amount",
    "Item",
)


def _cell(
    text: object,
    *,
    bg: str,
    align=Qt.AlignLeft | Qt.AlignVCenter,
    bold: bool = False,
) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    item.setBackground(QBrush(QColor(bg)))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    return item


class _FilterPill(QPushButton):
    """Compact exclusive filter pill with an optional count badge."""

    def __init__(self, label: str, count: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_label = label
        self._count = int(count)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)
        self._refresh_text()
        self.toggled.connect(self._apply_style)
        self._apply_style(False)

    def set_count(self, count: int) -> None:
        self._count = int(count)
        self._refresh_text()

    def _refresh_text(self) -> None:
        if self._count > 0:
            shown = "999+" if self._count > 999 else str(self._count)
            self.setText(f"{self._base_label}  {shown}")
        else:
            self.setText(self._base_label)

    def _apply_style(self, checked: bool = False) -> None:
        checked = self.isChecked()
        if checked:
            self.setStyleSheet(
                f"QPushButton{{background:{_BLUE};color:#FFF;border:1px solid {_BLUE};"
                "border-radius:13px;padding:2px 12px;font-size:11px;font-weight:600;"
                "font-family:'Segoe UI',sans-serif;}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
                "border-radius:13px;padding:2px 12px;font-size:11px;font-weight:600;"
                "font-family:'Segoe UI',sans-serif;}"
                f"QPushButton:hover{{background:{_BG};}}"
            )


class _ContextChip(QPushButton):
    """Toggleable item/description chip with count."""

    def __init__(self, kind: str, label: str, count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.facet_value = label
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(22)
        shown = "999+" if count > 999 else str(count)
        # Truncate long labels for the chip row.
        display = label if len(label) <= 28 else label[:27] + "…"
        self.setText(f"{display} ({shown})")
        self.setToolTip(f"{kind.title()}: {label} ({count})")
        self.toggled.connect(self._apply_style)
        self._apply_style()

    def _apply_style(self, *_args) -> None:
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton{{background:#E8F1FA;color:{_BLUE};border:1px solid {_BLUE};"
                "border-radius:3px;padding:1px 8px;font-size:10px;font-weight:600;"
                "font-family:'Segoe UI',sans-serif;}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton{{background:{_BG};color:{_T2};border:1px solid {_BORDER};"
                "border-radius:3px;padding:1px 8px;font-size:10px;"
                "font-family:'Segoe UI',sans-serif;}"
                f"QPushButton:hover{{color:{_T1};border-color:#D1D5DB;}}"
            )


class DuplicateReviewDialog(QDialog):
    """Review possible duplicates — new row + matching existing expense."""

    def __init__(
        self,
        items: List[DuplicateReviewItem],
        *,
        dup_days: int,
        parent: QWidget | None = None,
        pending_total: int | None = None,
        initial_checked: Set[int] | None = None,
        lookback_value: int | None = None,
        lookback_unit: str = "days",
        allow_rescan: bool = True,
        review_mode: str = "insert",
        lookback_overrides: object = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._dup_days = max(1, min(365, int(dup_days)))
        self._lookback_overrides = normalize_lookback_overrides(lookback_overrides)
        self._lookback_overrides_changed = False
        self._lookback_unit = (
            "months"
            if (lookback_unit or "days").strip().casefold().startswith("month")
            else "days"
        )
        _month_cap = 12
        _day_cap = 365
        if lookback_value is not None:
            self._lookback_value = max(1, int(lookback_value))
        elif self._lookback_unit == "months":
            self._lookback_value = max(1, (self._dup_days + 29) // 30)
        else:
            self._lookback_value = self._dup_days
        if self._lookback_unit == "months":
            self._lookback_value = max(1, min(_month_cap, self._lookback_value))
        else:
            self._lookback_value = max(1, min(_day_cap, self._lookback_value))
        self._pending_total = (
            int(pending_total)
            if pending_total is not None
            else len(self._items)
        )
        self._allow_rescan = bool(allow_rescan)
        # "edit" = saving changes to already-saved rows (defer leaves them dirty).
        self._review_mode = (
            "edit"
            if str(review_mode or "").strip().casefold() == "edit"
            else "insert"
        )
        self._save_anyway_rows: Set[int] = set()
        self._keep_for_later = False
        self._requested_rescan_days: int | None = None
        self._checked_by_row: Dict[int, bool] = {
            item.row: False for item in self._items
        }
        if initial_checked:
            for row in initial_checked:
                if row in self._checked_by_row:
                    self._checked_by_row[row] = True

        self._source_filter: str = "all"
        self._item_filter: str | None = None
        self._desc_filter: str | None = None
        self._visible_items: List[DuplicateReviewItem] = []
        self._checks: list[QCheckBox] = []
        self._source_pills: Dict[str, _FilterPill] = {}
        self._truck_count_badge: QLabel | None = None

        self.setWindowTitle(
            "Possible duplicates (edited rows)"
            if self._review_mode == "edit"
            else "Possible duplicates"
        )
        self.setMinimumSize(780, 420)
        self.resize(900, 480)
        self.setStyleSheet(f"background:{_WHITE}; color:{_T1};")
        self._build()
        self._rebuild_filters_and_table()

    def save_anyway_rows(self) -> Set[int]:
        return set(self._save_anyway_rows)

    def deferred_duplicate_rows(self) -> Set[int]:
        """Dup rows intentionally left on the table (not saved in this pass)."""
        all_dup = {item.row for item in self._items}
        return all_dup - set(self._save_anyway_rows)

    def kept_for_later(self) -> bool:
        """True when the cashier chose Keep for later (defer all dups)."""
        return bool(self._keep_for_later)

    def requested_rescan_days(self) -> int | None:
        """When set, parent should re-scan with this day window and re-open."""
        return self._requested_rescan_days

    def checked_rows_snapshot(self) -> Set[int]:
        """Rows currently ticked Save — used to preserve state across rescan."""
        self._sync_visible_checks_into_map()
        return {row for row, on in self._checked_by_row.items() if on}

    def lookback_unit(self) -> str:
        return self._lookback_unit

    def lookback_value(self) -> int:
        return int(self._lookback_value)

    def lookback_overrides(self) -> LookbackOverrides:
        return normalize_lookback_overrides(self._lookback_overrides)

    def lookback_overrides_changed(self) -> bool:
        """True when Custom lookback was saved — parent should persist settings."""
        return bool(self._lookback_overrides_changed)

    def _looks_like_reimport(self) -> bool:
        n = len(self._items)
        if n < 2:
            return False
        return n >= max(2, int(self._pending_total * 0.8))

    def _window_label(self) -> str:
        return format_lookback_label(
            self._dup_days,
            unit=self._lookback_unit,
            value=self._lookback_value,
        )

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._title = QLabel()
        self._title.setStyleSheet(
            f"font-size:14px;font-weight:700;color:{_T1};"
            "font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(self._title)

        self._subtitle = QLabel()
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(
            f"font-size:11px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(self._subtitle)
        self._refresh_title_copy()

        # Lookback controls — unit change converts the spin so Months ≠ same number of Days
        look_row = QHBoxLayout()
        look_row.setSpacing(6)
        look_lbl = QLabel("Lookback")
        look_lbl.setStyleSheet(
            f"font-size:11px;font-weight:600;color:{_T2};"
            "font-family:'Segoe UI',sans-serif;"
        )
        look_row.addWidget(look_lbl)
        self._lookback_spin = QSpinBox()
        self._lookback_spin.setFixedWidth(64)
        self._lookback_spin.setFixedHeight(26)
        self._lookback_spin.setStyleSheet(
            f"QSpinBox{{border:1px solid {_BORDER};border-radius:3px;padding:2px 4px;"
            "font-size:11px;font-family:'Segoe UI';}"
        )
        look_row.addWidget(self._lookback_spin)
        self._lookback_unit_combo = QComboBox()
        self._lookback_unit_combo.addItem("Days", "days")
        self._lookback_unit_combo.addItem("Months", "months")
        self._lookback_unit_combo.setCurrentIndex(
            1 if self._lookback_unit == "months" else 0
        )
        self._lookback_unit_combo.setFixedHeight(26)
        self._lookback_unit_combo.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:3px;padding:2px 8px;"
            "font-size:11px;font-family:'Segoe UI';min-width:84px;}"
        )
        look_row.addWidget(self._lookback_unit_combo)
        self._lookback_hint = QLabel("")
        self._lookback_hint.setStyleSheet(
            f"font-size:11px;font-weight:600;color:{_BLUE};"
            "font-family:'Segoe UI',sans-serif;"
        )
        look_row.addWidget(self._lookback_hint)
        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.setCursor(Qt.PointingHandCursor)
        self._rescan_btn.setFixedHeight(26)
        self._rescan_btn.setEnabled(self._allow_rescan)
        self._rescan_btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:3px;"
            "padding:2px 12px;font-size:11px;font-weight:600;font-family:'Segoe UI';}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5E8;color:#EEF6FC;}"
        )
        self._rescan_btn.clicked.connect(self._request_rescan)
        look_row.addWidget(self._rescan_btn)
        self._custom_lookback_btn = QPushButton("Custom…")
        self._custom_lookback_btn.setCursor(Qt.PointingHandCursor)
        self._custom_lookback_btn.setFixedHeight(26)
        self._custom_lookback_btn.setEnabled(self._allow_rescan)
        self._custom_lookback_btn.setToolTip(
            "Set lookback days per item or description (remembered for later days)"
            if self._allow_rescan
            else "Custom lookback not available for this entry"
        )
        self._custom_lookback_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_BLUE};border:1px solid {_BORDER};"
            "border-radius:3px;padding:2px 10px;font-size:11px;font-weight:600;"
            "font-family:'Segoe UI';}"
            f"QPushButton:hover{{background:{_BG};}}"
            "QPushButton:disabled{color:#93C5E8;border-color:#E5E7EB;}"
        )
        self._custom_lookback_btn.clicked.connect(self._open_custom_lookback)
        look_row.addWidget(self._custom_lookback_btn)
        look_row.addStretch()
        tip = QLabel("Same truck/service on different days can be legitimate.")
        tip.setStyleSheet(
            f"font-size:11px;color:{_AMBER};font-family:'Segoe UI',sans-serif;"
        )
        look_row.addWidget(tip)
        root.addLayout(look_row)

        self._suppress_lookback_unit_change = True
        self._apply_lookback_spin_range(self._lookback_unit)
        self._lookback_spin.setValue(self._lookback_value)
        self._suppress_lookback_unit_change = False
        self._lookback_spin.valueChanged.connect(self._on_lookback_controls_changed)
        self._lookback_unit_combo.currentIndexChanged.connect(
            self._on_lookback_unit_changed
        )
        self._refresh_lookback_hint()

        # Source pills + compact trucks counter (sidebar-style badge)
        self._source_row = QHBoxLayout()
        self._source_row.setSpacing(6)
        root.addLayout(self._source_row)

        # Context chips (item / description)
        self._context_frame = QFrame()
        self._context_frame.setStyleSheet("QFrame{background:transparent;border:none;}")
        self._context_layout = QVBoxLayout(self._context_frame)
        self._context_layout.setContentsMargins(0, 0, 0, 0)
        self._context_layout.setSpacing(4)
        root.addWidget(self._context_frame)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        self._select_all = QPushButton("Select all")
        self._select_none = QPushButton("Select none")
        for btn in (self._select_all, self._select_none):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{_BLUE};border:1px solid {_BORDER};"
                "border-radius:3px;padding:2px 10px;font-size:11px;font-family:'Segoe UI';}"
                f"QPushButton:hover{{background:{_BG};}}"
            )
        self._select_all.clicked.connect(self._check_all_visible)
        self._select_none.clicked.connect(self._uncheck_all_visible)
        toggle_row.addWidget(self._select_all)
        toggle_row.addWidget(self._select_none)
        toggle_row.addStretch()
        self._filter_hint = QLabel("")
        self._filter_hint.setStyleSheet(
            f"font-size:11px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        toggle_row.addWidget(self._filter_hint)
        root.addLayout(toggle_row)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setShowGrid(True)
        self._table.setStyleSheet(
            f"QTableWidget{{border:1px solid {_BORDER};gridline-color:{_BORDER};"
            "font-family:'Segoe UI';font-size:11px;}"
            "QHeaderView::section{background:#F9FAFB;padding:4px 6px;border:none;"
            f"border-bottom:1px solid {_BORDER};font-weight:600;color:{_T2};"
            "font-size:11px;}"
        )
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        root.addWidget(self._table, 1)

        buttons = QDialogButtonBox()
        save_btn = buttons.addButton("Save checked", QDialogButtonBox.AcceptRole)
        if self._review_mode == "edit":
            keep_btn = buttons.addButton(
                "Skip for now", QDialogButtonBox.ActionRole
            )
            keep_btn.setToolTip(
                "Leave these edited rows unsaved (still dirty). "
                "Fix them or Save again later."
            )
        else:
            keep_btn = buttons.addButton(
                "Move to Skipped", QDialogButtonBox.ActionRole
            )
            keep_btn.setToolTip(
                "Move these possible duplicates to the Skipped tab. "
                "Revive them onto My entries later, or Discard."
            )
        cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        for btn, style in (
            (
                save_btn,
                f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
                "padding:6px 14px;font-weight:600;font-family:'Segoe UI';font-size:12px;}"
                "QPushButton:hover{background:#005EA3;}",
            ),
            (
                keep_btn,
                f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
                "border-radius:4px;padding:6px 14px;font-family:'Segoe UI';font-size:12px;}"
                f"QPushButton:hover{{background:{_BG};}}",
            ),
            (
                cancel_btn,
                "QPushButton{background:transparent;color:#DC2626;border:1px solid #FECACA;"
                "border-radius:4px;padding:6px 14px;font-family:'Segoe UI';font-size:12px;}"
                "QPushButton:hover{background:#FEF2F2;}",
            ),
        ):
            btn.setStyleSheet(style)
        save_btn.clicked.connect(self._accept_checked)
        keep_btn.clicked.connect(self._accept_keep_for_later)
        cancel_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _refresh_title_copy(self) -> None:
        n = len(self._items)
        window = self._window_label()
        reimport = self._looks_like_reimport()
        if self._review_mode == "edit":
            self._title.setText(
                f"{n} edited row{'s' if n != 1 else ''} may duplicate "
                f"past expenses · checked {window}"
            )
            self._title.setStyleSheet(
                f"font-size:14px;font-weight:700;color:{_T1};"
                "font-family:'Segoe UI',sans-serif;"
            )
            self._subtitle.setText(
                "Each pair shows the edited row (red) above the matching expense "
                "(cream). Tick Save to keep the edit anyway — it will be flagged "
                "for Verify → Issues. Skip for now leaves those edits unsaved."
            )
            return
        if reimport:
            self._title.setText(
                f"Looks like a reimport — {n} of {self._pending_total} new "
                f"row{'s' if self._pending_total != 1 else ''} already exist "
                f"({window})"
            )
            self._title.setStyleSheet(
                f"font-size:14px;font-weight:700;color:{_AMBER};"
                "font-family:'Segoe UI',sans-serif;"
            )
            self._subtitle.setText(
                "Most of these rows already match expenses in the system — "
                "often from re-importing the same Excel file. Prefer "
                "Move to Skipped unless you are sure each new row is a separate "
                "expense. Use pills to focus by item or description. "
                "Each pair shows the new row (red) above the existing expense (cream)."
            )
        else:
            self._title.setText(
                f"{n} possible duplicate{'s' if n != 1 else ''} · checked {window}"
            )
            self._title.setStyleSheet(
                f"font-size:14px;font-weight:700;color:{_T1};"
                "font-family:'Segoe UI',sans-serif;"
            )
            self._subtitle.setText(
                "Each pair shows the new row (red) above the existing expense (cream). "
                "Filter by source, item, or description. Tick Save to keep the "
                "new row anyway — it will be flagged for Verify → Issues. Unchecked "
                "pairs (or Move to Skipped) go to the Skipped tab."
            )

    def _clear_layout(self, layout: QHBoxLayout | QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            child = item.layout()
            if child is not None:
                self._clear_layout(child)

    def _rebuild_filters_and_table(self) -> None:
        self._sync_visible_checks_into_map()
        self._rebuild_source_pills()
        self._rebuild_context_chips()
        self._refill_table()

    def _source_base_items(self) -> List[DuplicateReviewItem]:
        return filter_duplicate_items(self._items, source=self._source_filter)  # type: ignore[arg-type]

    def _rebuild_source_pills(self) -> None:
        self._clear_layout(self._source_row)
        self._source_pills.clear()
        self._truck_count_badge = None
        counts = group_counts(self._items)["source"]
        n_all = len(self._items)
        n_db = int(counts.get("db", 0))
        n_table = int(counts.get("table", 0))
        specs = (
            ("all", "All", n_all),
            ("db", "In system", n_db),
            ("table", "Same table", n_table),
        )
        for key, label, count in specs:
            pill = _FilterPill(label, count)
            pill.setChecked(self._source_filter == key)
            pill.clicked.connect(
                lambda checked=False, k=key: self._on_source_pill(k)
            )
            self._source_pills[key] = pill
            self._source_row.addWidget(pill)

        self._source_row.addStretch()
        self._add_trucks_counter()

    def _add_trucks_counter(self) -> None:
        """Compact Trucks label + sidebar-style count badge (no per-truck pills)."""
        base = self._source_base_items()
        n_trucks = len(group_counts(base)["truck"])
        trucks_lbl = QLabel("Trucks")
        trucks_lbl.setStyleSheet(
            f"font-size:11px;font-weight:600;color:{_T2};"
            "font-family:'Segoe UI',sans-serif;"
        )
        self._source_row.addWidget(trucks_lbl)
        badge = QLabel("0")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedHeight(18)
        badge.setMinimumWidth(24)
        badge.setStyleSheet(
            "background:#B45309;color:#FFFBEB;border-radius:9px;"
            "font-size:10px;font-weight:700;font-family:'Segoe UI';padding:0 6px;"
        )
        badge.setToolTip(
            f"{n_trucks} distinct truck{'s' if n_trucks != 1 else ''} "
            "in the current filter"
        )
        shown = "999+" if n_trucks > 999 else str(n_trucks)
        badge.setText(shown)
        badge.setVisible(n_trucks > 0)
        trucks_lbl.setVisible(n_trucks > 0)
        self._truck_count_badge = badge
        self._source_row.addWidget(badge)

    def _rebuild_context_chips(self) -> None:
        self._clear_layout(self._context_layout)
        base = self._source_base_items()
        counts = group_counts(base)
        items_c = counts["item"]
        descs_c = counts["description"]

        # Drop facet filters that no longer exist under the source filter.
        if self._item_filter is not None and self._item_filter not in items_c:
            self._item_filter = None
        if self._desc_filter is not None and self._desc_filter not in descs_c:
            self._desc_filter = None

        if not base:
            self._context_frame.hide()
            return

        def _add_chip_row(title: str, kind: str, counter) -> None:
            if not counter:
                return
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel(title)
            lbl.setStyleSheet(
                f"font-size:10px;font-weight:600;color:{_T2};"
                "font-family:'Segoe UI',sans-serif;"
            )
            row.addWidget(lbl)
            active = self._item_filter if kind == "item" else self._desc_filter
            for label, count in sorted(
                counter.items(), key=lambda kv: (-kv[1], kv[0].casefold())
            )[:12]:
                chip = _ContextChip(kind, label, int(count))
                override_days = (self._lookback_overrides.get(kind) or {}).get(
                    label.casefold()
                )
                if override_days is not None:
                    chip.setText(f"{chip.text()} · {override_days}d")
                    chip.setToolTip(
                        f"{kind.title()}: {label} ({count}) — custom lookback "
                        f"{override_days} day{'s' if override_days != 1 else ''}"
                    )
                chip.setChecked(active == label)
                chip.clicked.connect(
                    lambda checked=False, k=kind, lab=label: self._on_context_chip(k, lab)
                )
                row.addWidget(chip)
            row.addStretch()
            self._context_layout.addLayout(row)

        _add_chip_row("Items", "item", items_c)
        _add_chip_row("Descriptions", "description", descs_c)
        self._context_frame.setVisible(self._context_layout.count() > 0)

    def _filtered_items(self) -> List[DuplicateReviewItem]:
        return filter_duplicate_items(
            self._items,
            source=self._source_filter,  # type: ignore[arg-type]
            item=self._item_filter,
            description=self._desc_filter,
        )

    def _refill_table(self) -> None:
        self._visible_items = self._filtered_items()
        n_vis = len(self._visible_items)
        n_all = len(self._items)
        if n_vis == n_all:
            self._filter_hint.setText(f"Showing all {n_all}")
        else:
            self._filter_hint.setText(f"Showing {n_vis} of {n_all}")

        self._table.setRowCount(n_vis * 2)
        self._checks = []
        for i, item in enumerate(self._visible_items):
            new_r = i * 2
            old_r = new_r + 1
            self._fill_new_row(new_r, item)
            self._fill_existing_row(old_r, item)
            self._table.setRowHeight(new_r, _ROW_H)
            self._table.setRowHeight(old_r, _ROW_H)

    def _on_source_pill(self, key: str) -> None:
        if key == self._source_filter:
            # Keep exclusive — re-check the active pill.
            pill = self._source_pills.get(key)
            if pill is not None:
                pill.setChecked(True)
            return
        self._source_filter = key
        self._item_filter = None
        self._desc_filter = None
        self._rebuild_filters_and_table()

    def _on_context_chip(self, kind: str, label: str) -> None:
        if kind == "item":
            self._item_filter = None if self._item_filter == label else label
        else:
            self._desc_filter = None if self._desc_filter == label else label
        self._rebuild_filters_and_table()

    def _fill_new_row(self, r: int, item: DuplicateReviewItem) -> None:
        wrap = QWidget()
        wrap.setStyleSheet(f"background:{_NEW_BG};")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setToolTip("Save this new row anyway and flag as possible duplicate")
        cb.setChecked(bool(self._checked_by_row.get(item.row, False)))
        cb.stateChanged.connect(
            lambda state, row=item.row: self._checked_by_row.__setitem__(
                row, int(state) == int(Qt.CheckState.Checked)
            )
        )
        lay.addWidget(cb)
        self._checks.append(cb)
        self._table.setCellWidget(r, 0, wrap)

        self._table.setItem(
            r, 1, _cell("New", bg=_NEW_BG, bold=True, align=Qt.AlignCenter)
        )
        self._table.setItem(
            r, 2, _cell(str(item.row_display), bg=_NEW_BG, align=Qt.AlignCenter)
        )
        self._table.setItem(
            r,
            3,
            _cell(item.date_label or "—", bg=_NEW_BG, align=Qt.AlignCenter),
        )
        self._table.setItem(r, 4, _cell(item.description or "—", bg=_NEW_BG))
        self._table.setItem(r, 5, _cell(item.truck_number or "—", bg=_NEW_BG))
        self._table.setItem(r, 6, _cell(item.amount_label, bg=_NEW_BG))
        self._table.setItem(r, 7, _cell(item.item or "—", bg=_NEW_BG))

    def _fill_existing_row(self, r: int, item: DuplicateReviewItem) -> None:
        bg = _EXISTING_BG
        empty = QWidget()
        empty.setStyleSheet(f"background:{bg};")
        self._table.setCellWidget(r, 0, empty)

        ex = item.existing
        self._table.setItem(
            r,
            1,
            _cell(
                item.existing_label or "Existing",
                bg=bg,
                bold=True,
                align=Qt.AlignCenter,
            ),
        )
        self._table.setItem(r, 2, _cell("—", bg=bg, align=Qt.AlignCenter))
        self._table.setItem(
            r, 3, _cell(format_existing_date(ex.date), bg=bg, align=Qt.AlignCenter)
        )
        self._table.setItem(r, 4, _cell(ex.description or "—", bg=bg))
        self._table.setItem(r, 5, _cell(ex.truck_number or "—", bg=bg))
        self._table.setItem(r, 6, _cell(format_amount_label(ex), bg=bg))
        self._table.setItem(r, 7, _cell(ex.item or "—", bg=bg))

    def _sync_visible_checks_into_map(self) -> None:
        for cb, item in zip(self._checks, self._visible_items):
            self._checked_by_row[item.row] = cb.isChecked()

    def _check_all_visible(self) -> None:
        for cb in self._checks:
            cb.setChecked(True)
        for item in self._visible_items:
            self._checked_by_row[item.row] = True

    def _uncheck_all_visible(self) -> None:
        for cb in self._checks:
            cb.setChecked(False)
        for item in self._visible_items:
            self._checked_by_row[item.row] = False

    def _current_lookback_unit(self) -> str:
        return str(self._lookback_unit_combo.currentData() or "days")

    def _apply_lookback_spin_range(self, unit: str) -> None:
        months = (unit or "days").strip().casefold().startswith("month")
        self._lookback_spin.setRange(1, 12 if months else 365)

    def _on_lookback_unit_changed(self, *_args) -> None:
        if getattr(self, "_suppress_lookback_unit_change", False):
            return
        new_unit = self._current_lookback_unit()
        old_unit = self._lookback_unit
        old_value = int(self._lookback_spin.value())
        converted = convert_lookback_value(
            old_value, from_unit=old_unit, to_unit=new_unit
        )
        self._suppress_lookback_unit_change = True
        self._apply_lookback_spin_range(new_unit)
        self._lookback_spin.setValue(converted)
        self._lookback_unit = new_unit
        self._lookback_value = converted
        self._suppress_lookback_unit_change = False
        self._refresh_lookback_hint()

    def _on_lookback_controls_changed(self, *_args) -> None:
        if getattr(self, "_suppress_lookback_unit_change", False):
            return
        self._lookback_value = int(self._lookback_spin.value())
        self._lookback_unit = self._current_lookback_unit()
        self._refresh_lookback_hint()

    def _refresh_lookback_hint(self) -> None:
        value = int(self._lookback_spin.value())
        unit = self._current_lookback_unit()
        effective = lookback_days_from_unit(value, unit)
        if unit.startswith("month"):
            self._lookback_hint.setText(f"= {effective} days")
            self._lookback_hint.show()
        else:
            self._lookback_hint.clear()
            self._lookback_hint.hide()
        if self._allow_rescan:
            label = format_lookback_label(
                effective, unit=unit, value=value
            )
            self._rescan_btn.setToolTip(
                f"Re-check duplicates for {label} ({effective} day window)"
            )
        else:
            self._rescan_btn.setToolTip("Rescan not available for this entry")

    def _effective_rescan_days(self) -> int:
        value = int(self._lookback_spin.value())
        unit = self._current_lookback_unit()
        return lookback_days_from_unit(value, unit)

    def _open_custom_lookback(self) -> None:
        if not self._allow_rescan:
            return
        dlg = LookbackOverridesDialog(
            self._items,
            default_days=self._effective_rescan_days(),
            overrides=self._lookback_overrides,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        self._lookback_overrides = dlg.overrides()
        self._lookback_overrides_changed = True
        self._sync_visible_checks_into_map()
        self._lookback_value = int(self._lookback_spin.value())
        self._lookback_unit = self._current_lookback_unit()
        self._requested_rescan_days = self._effective_rescan_days()
        self._keep_for_later = False
        self._save_anyway_rows = set()
        self.accept()

    def _request_rescan(self) -> None:
        if not self._allow_rescan:
            return
        self._sync_visible_checks_into_map()
        self._lookback_value = int(self._lookback_spin.value())
        self._lookback_unit = self._current_lookback_unit()
        self._requested_rescan_days = self._effective_rescan_days()
        self._keep_for_later = False
        self._save_anyway_rows = set()
        self.accept()

    def _accept_checked(self) -> None:
        self._sync_visible_checks_into_map()
        self._requested_rescan_days = None
        self._keep_for_later = False
        self._save_anyway_rows = {
            row for row, on in self._checked_by_row.items() if on
        }
        self.accept()

    def _accept_keep_for_later(self) -> None:
        """Defer all duplicate rows — leave them on the table for a later Save."""
        self._sync_visible_checks_into_map()
        self._requested_rescan_days = None
        self._keep_for_later = True
        self._save_anyway_rows = set()
        self.accept()

    # Back-compat alias for older callers / tests.
    def _accept_skip_all(self) -> None:
        self._accept_keep_for_later()

    # Back-compat for tests that poked private check helpers.
    def _check_all(self) -> None:
        self._check_all_visible()

    def _uncheck_all(self) -> None:
        self._uncheck_all_visible()
