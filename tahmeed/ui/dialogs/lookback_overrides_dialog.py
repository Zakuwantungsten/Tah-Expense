"""Edit persisted per-item / per-description soft-dup lookback days."""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    clamp_lookback_days,
    description_facet_label,
    empty_lookback_overrides,
    item_facet_label,
    normalize_lookback_overrides,
)

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BORDER = "#E5E7EB"
_ROW_H = 22


class LookbackOverridesDialog(QDialog):
    """Switch Item ↔ Description, list values from this review, set days.

    Saved overrides persist via settings so the same item/description keeps
    its window on later days until changed — even if it is absent tomorrow.
    """

    def __init__(
        self,
        items: Sequence[DuplicateReviewItem],
        *,
        default_days: int,
        overrides: object = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._default_days = clamp_lookback_days(default_days)
        self._overrides = normalize_lookback_overrides(overrides)
        self._item_labels = self._collect_labels(items, kind="item")
        self._desc_labels = self._collect_labels(items, kind="description")
        self._spins: Dict[Tuple[str, str], QSpinBox] = {}
        self._draft: LookbackOverrides = {
            "item": dict(self._overrides["item"]),
            "description": dict(self._overrides["description"]),
        }

        self.setWindowTitle("Custom lookback")
        self.setMinimumSize(520, 360)
        self.resize(560, 420)
        self.setStyleSheet(f"background:{_WHITE};color:{_T1};")
        self._build()
        self._refill_table()

    @staticmethod
    def _collect_labels(
        items: Sequence[DuplicateReviewItem], *, kind: str
    ) -> List[str]:
        seen: Dict[str, str] = {}
        for entry in items:
            if kind == "item":
                label = item_facet_label(entry.item)
            else:
                label = description_facet_label(entry.description)
            key = label.casefold()
            if key and key not in seen:
                seen[key] = label
        return sorted(seen.values(), key=lambda s: s.casefold())

    def overrides(self) -> LookbackOverrides:
        """Normalized overrides after Accept (only custom values kept)."""
        return normalize_lookback_overrides(self._draft)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("Custom lookback by item or description")
        title.setStyleSheet(
            f"font-size:14px;font-weight:700;color:{_T1};"
            "font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(title)

        sub = QLabel(
            "Switch Item or Description to list values from this review. "
            "Set days per row — remembered on later days when that value "
            f"appears again. Default is the global lookback ({self._default_days} days)."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size:11px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(sub)

        kind_row = QHBoxLayout()
        kind_row.setSpacing(6)
        kind_lbl = QLabel("Apply to")
        kind_lbl.setStyleSheet(
            f"font-size:11px;font-weight:600;color:{_T2};"
            "font-family:'Segoe UI',sans-serif;"
        )
        kind_row.addWidget(kind_lbl)
        self._kind_combo = QComboBox()
        self._kind_combo.addItem("Item", "item")
        self._kind_combo.addItem("Description", "description")
        self._kind_combo.setFixedHeight(24)
        self._kind_combo.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:3px;padding:1px 8px;"
            "font-size:11px;font-family:'Segoe UI';min-width:120px;}"
        )
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        kind_row.addWidget(self._kind_combo)
        kind_row.addStretch()
        root.addLayout(kind_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["SN", "Value", "Days", ""])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setShowGrid(True)
        self._table.setStyleSheet(
            f"QTableWidget{{border:1px solid {_BORDER};gridline-color:{_BORDER};"
            "font-family:'Segoe UI';font-size:11px;}"
            "QHeaderView::section{background:#F9FAFB;padding:2px 6px;border:none;"
            f"border-bottom:1px solid {_BORDER};font-weight:600;color:{_T2};"
            "font-size:11px;}"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        root.addWidget(self._table, 1)

        buttons = QDialogButtonBox()
        save_btn = buttons.addButton("Save & Rescan", QDialogButtonBox.AcceptRole)
        cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        save_btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "padding:6px 14px;font-weight:600;font-family:'Segoe UI';font-size:12px;}"
            "QPushButton:hover{background:#005EA3;}"
        )
        cancel_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:4px;padding:6px 14px;font-family:'Segoe UI';font-size:12px;}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        save_btn.clicked.connect(self._accept)
        cancel_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _current_kind(self) -> str:
        return str(self._kind_combo.currentData() or "item")

    def _labels_for_kind(self, kind: str) -> List[str]:
        base = self._item_labels if kind == "item" else self._desc_labels
        # Include saved override keys not present in this scan (next-day recall).
        extra_keys = set(self._draft.get(kind, {}) or {})
        by_key = {label.casefold(): label for label in base}
        for key in sorted(extra_keys):
            if key not in by_key:
                by_key[key] = key
        return sorted(by_key.values(), key=lambda s: s.casefold())

    def _flush_visible_spins_into_draft(self) -> None:
        kind = self._current_kind()
        bucket = dict(self._draft.get(kind) or {})
        for (spin_kind, key), spin in list(self._spins.items()):
            if spin_kind != kind:
                continue
            days = clamp_lookback_days(spin.value())
            if days == self._default_days:
                bucket.pop(key, None)
            else:
                bucket[key] = days
        self._draft[kind] = bucket

    def _on_kind_changed(self, *_args) -> None:
        self._flush_visible_spins_into_draft()
        self._refill_table()

    def _refill_table(self) -> None:
        kind = self._current_kind()
        labels = self._labels_for_kind(kind)
        bucket = self._draft.get(kind) or {}
        self._spins.clear()
        self._table.clearSpans()
        self._table.setRowCount(len(labels))
        if not labels:
            self._table.setRowCount(1)
            empty = QTableWidgetItem(
                "No values in this review yet — save overrides after a scan."
            )
            empty.setFlags(empty.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(0, 0, empty)
            self._table.setSpan(0, 0, 1, 4)
            self._table.setRowHeight(0, _ROW_H)
            return

        for row, label in enumerate(labels):
            key = label.casefold()
            sn = QTableWidgetItem(str(row + 1))
            sn.setFlags(sn.flags() & ~Qt.ItemIsEditable)
            sn.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 0, sn)

            name = QTableWidgetItem(label)
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 1, name)

            spin = QSpinBox()
            spin.setRange(1, 365)
            spin.setValue(int(bucket.get(key, self._default_days)))
            spin.setFixedWidth(64)
            spin.setFixedHeight(20)
            spin.setStyleSheet(
                f"QSpinBox{{border:1px solid {_BORDER};border-radius:2px;"
                "padding:0 2px;font-size:11px;font-family:'Segoe UI';}"
            )
            spin.setToolTip(
                f"Lookback days for this {kind}. "
                f"Default is {self._default_days}."
            )
            self._spins[(kind, key)] = spin
            self._table.setCellWidget(row, 2, spin)

            clear_btn = QPushButton("Default")
            clear_btn.setCursor(Qt.PointingHandCursor)
            clear_btn.setFixedHeight(20)
            clear_btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{_BLUE};border:1px solid {_BORDER};"
                "border-radius:2px;padding:0 6px;font-size:10px;font-family:'Segoe UI';}"
                f"QPushButton:hover{{background:{_BG};}}"
            )
            clear_btn.setToolTip(f"Reset to global default ({self._default_days} days)")
            clear_btn.clicked.connect(
                lambda _=False, s=spin, k=key, kd=kind: self._reset_row(kd, k, s)
            )
            self._table.setCellWidget(row, 3, clear_btn)
            self._table.setRowHeight(row, _ROW_H)

    def _reset_row(self, kind: str, key: str, spin: QSpinBox) -> None:
        spin.setValue(self._default_days)
        self._draft.get(kind, {}).pop(key, None)

    def _accept(self) -> None:
        self._flush_visible_spins_into_draft()
        cleaned = empty_lookback_overrides()
        for kind in ("item", "description"):
            for key, days in (self._draft.get(kind) or {}).items():
                days_i = clamp_lookback_days(days)
                if days_i != self._default_days:
                    cleaned[kind][key] = days_i
        self._draft = cleaned
        self.accept()
