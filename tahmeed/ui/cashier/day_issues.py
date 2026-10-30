"""Day Issues — bulk map unmapped descriptions → Payee + Item.

Mirrors the QuickBooks Expense Automation issues tab (without Ignore):
multi-select descriptions, assign one Payee + Item, apply to open rows.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Tuple

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import (
    QDate, QEvent, QPersistentModelIndex, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QApplication, QCheckBox,
    QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QDateEdit,
)

from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.completer_line_edit import (
    CompleterLineEdit,
    accept_completion,
    hide_completion_popup,
)

from tahmeed.models.category import Category
from tahmeed.models.user import User
from tahmeed.services.category_service import get_payment_target_categories, sort_payment_targets
from tahmeed.services.day_issues_service import (
    DayIssue,
    DayIssuesSnapshot,
    as_of_label,
    collect_day_issues,
    collect_day_issues_all_days,
)
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.mapping_assignment_service import (
    MappingAssignment,
    materialize_mapping_assignment,
    remember_category,
)
from tahmeed.services.payee_service import (
    add_payee, get_payee_names, invalidate_payee_cache,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _table_cell_font, _table_style, _ROW_H,
)
from tahmeed.ui.dialogs.item_dialog import ItemDialog
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.combo_styles import with_combo_popup
from tahmeed.ui.widgets.excel_column_filter import (
    ExcelFilterHeaderView, SORT_DESC,
)
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
# Row click-drag highlight — stronger than shared _table_style (#E8F4FD),
# which barely shows against stripe rows (#F1F5F9 / white).
_SEL_BG  = "#93C5FD"   # blue-300
_SEL_FG  = "#0F172A"
_GOLD    = "#B18E5E"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_NAVY    = "#1B2B4B"

_COL_CHECK = 0
_COL_NUM = 1
_COL_KIND = 2
_COL_DESC = 3
_COL_ROWS = 4
_COL_PAYEE = 5
_COL_ITEM = 6
_COL_DETAIL = 7
# Check | # | Type | Description | Rows | Payee | Item | Detail
_COL_DEFAULTS = [36, 40, 90, 180, 55, 160, 160, 180]
_COL_KEY = "day_issues_table_v3"
_FIXED_COLS = frozenset({_COL_CHECK, _COL_NUM, _COL_ROWS})


@dataclass(frozen=True)
class DayIssuesNavTarget:
    """Deep-link payload for Day Issues (plain date still works elsewhere).

    Verify approve-gate uses this so Day Issues can list the blocked
    descriptions (VALUE filter) and switch to All days when they span
    more than one register day.
    """

    date: Optional[date] = None
    description_keys: Tuple[str, ...] = ()
    all_days: bool = False
    # Register days of the blocked rows; All days scans only these when set.
    days: Tuple[date, ...] = ()


@dataclass
class MappingUndoSnapshot:
    """Enough state to reverse one successful Day Issues map commit."""

    descriptions: List[str]
    dates: List[date]
    # description_key -> prior map tuple, or None if the map was newly created.
    # tuple: (description, category_id, category_name, payee)
    prior_by_key: dict
    # description_key -> ObjectId of the upserted map (for delete when new)
    new_ids_by_key: dict


def split_undo_map_actions(
    snapshot: MappingUndoSnapshot,
) -> Tuple[List[ObjectId], List[Tuple[str, ObjectId, str, str]]]:
    """Return ``(delete_ids, restore_entries)`` for a mapping undo."""
    delete_ids: List[ObjectId] = []
    restore: List[Tuple[str, ObjectId, str, str]] = []
    seen_keys: set = set()
    for desc in snapshot.descriptions:
        key = normalize_description(desc)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        prior = snapshot.prior_by_key.get(key)
        if prior is None:
            mid = snapshot.new_ids_by_key.get(key)
            if mid is not None:
                delete_ids.append(mid)
            continue
        restore.append(prior)
    return delete_ids, restore


_CHECKMARK_PATH: Optional[str] = None


def payee_needs_create(payee: str, known: Sequence[str]) -> bool:
    """True when *payee* is non-empty and not already in the known list."""
    name = " ".join((payee or "").strip().upper().split())
    if not name:
        return False
    known_set = {(" ".join(str(p).strip().upper().split())) for p in known if str(p).strip()}
    return name not in known_set


def _ensure_checkmark_path() -> str:
    """Cached PNG checkmark for faint-blue checked indicators."""
    global _CHECKMARK_PATH
    if _CHECKMARK_PATH and Path(_CHECKMARK_PATH).is_file():
        return _CHECKMARK_PATH
    path = Path(tempfile.gettempdir()) / "tahmeed_day_issues_check.png"
    try:
        pm = qta.icon("mdi.check-bold", color=_BLUE).pixmap(12, 12)
        if pm.isNull():
            raise RuntimeError("empty checkmark pixmap")
        pm.save(str(path), "PNG")
    except Exception:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(_BLUE))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(4, 8, 7, 11)
        painter.drawLine(7, 11, 12, 4)
        painter.end()
        pm.save(str(path), "PNG")
    _CHECKMARK_PATH = path.resolve().as_posix()
    return _CHECKMARK_PATH


def _checkbox_style() -> str:
    url = _ensure_checkmark_path()
    return (
        f"QCheckBox{{font-size:12px;color:{_T1};font-family:'Segoe UI';spacing:8px;}}"
        "QCheckBox::indicator{width:16px;height:16px;border-radius:3px;"
        f"border:1.5px solid #93C5FD;background:{_WHITE};}}"
        f"QCheckBox::indicator:hover{{border-color:{_BLUE};}}"
        f"QCheckBox::indicator:checked{{background:{_BLUE_L};border:1.5px solid {_BLUE};"
        f'image:url("{url}");}}'
        "QCheckBox::indicator:checked:hover{background:#DBEAFE;}"
    )


def _table_checkbox_indicator_qss() -> str:
    url = _ensure_checkmark_path()
    return (
        "QTableWidget::indicator{width:14px;height:14px;border-radius:3px;"
        f"border:1.5px solid #93C5FD;background:{_WHITE};}}"
        f"QTableWidget::indicator:checked{{background:{_BLUE_L};"
        f'border:1.5px solid {_BLUE};image:url("{url}");}}'
    )


def filter_day_issues(
    issues: Sequence[DayIssue],
    *,
    search: str = "",
    value_filter: Optional[set] = None,
) -> List[DayIssue]:
    """Apply search + optional VALUE multi-select (description keys)."""
    needle = (search or "").strip().lower()
    out: List[DayIssue] = []
    for issue in issues:
        if value_filter is not None and issue.description_key not in value_filter:
            continue
        if needle:
            haystack = " ".join(
                [
                    issue.description or "",
                    issue.payee or "",
                    issue.item or "",
                    issue.kind or "",
                ]
            ).lower()
            if needle not in haystack:
                continue
        out.append(issue)
    return out


def resolve_value_filter_selection(
    selected_keys: set,
    all_keys: set,
) -> Optional[set]:
    """Normalize dialog selection: None = show all (no filter)."""
    if not selected_keys or selected_keys >= all_keys:
        return None
    return set(selected_keys)


def value_filter_from_descriptions(
    selected_descriptions: set,
    issues: Sequence[DayIssue],
) -> Optional[set]:
    """Map Excel ▾ display descriptions → description_keys (or None = all)."""
    by_desc = {i.description: i.description_key for i in issues}
    keys = set()
    for text in selected_descriptions or ():
        if text in by_desc:
            keys.add(by_desc[text])
        else:
            keys.add(normalize_description(text))
    all_keys = {i.description_key for i in issues}
    return resolve_value_filter_selection(keys, all_keys)


def description_displays_for_value_filter(
    value_filter: Optional[set],
    issues: Sequence[DayIssue],
) -> set:
    """Display strings for active VALUE keys (empty set = no header filter)."""
    if value_filter is None:
        return set()
    return {
        i.description for i in issues if i.description_key in value_filter
    }


def map_target_issues(
    visible: Sequence[DayIssue],
    *,
    checked_keys: set,
    selected_keys: set,
) -> List[DayIssue]:
    """Union of checked and highlighted (selected) rows among *visible*."""
    wanted = checked_keys | selected_keys
    if not wanted:
        return []
    by_key = {i.description_key: i for i in visible}
    return [by_key[k] for k in by_key if k in wanted]


def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';background:transparent;"
    )
    return w


def _btn(text: str, *, primary: bool = True, height: int = 32) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if primary:
        b.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:#005EA3;}}"
            f"QPushButton:disabled{{background:#93C5FD;}}"
        )
    else:
        b.setStyleSheet(
            f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
            f"QPushButton:disabled{{color:{_TM};}}"
        )
    return b


def _cell(text: str, align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
          bg: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    item.setFont(_table_cell_font())
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _editable_cell(text: str, *, bg: str = "") -> QTableWidgetItem:
    display = "" if text in (None, "—") else str(text)
    item = QTableWidgetItem(display)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setFont(_table_cell_font())
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setFlags(
        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
    )
    return item


# QuickBooks register look: white / pale-blue stripes; the row being edited
# turns white with thin grey cell outlines on Payee / Item.
_QB_STRIPE = "#E6EFFC"
_QB_CELL_BORDER = "#A3A3A3"
_BOX_INSET = 1


def _issues_stripe_bg(row_idx: int) -> str:
    return _QB_STRIPE if row_idx % 2 else _WHITE


def inline_edit_target(row: int, col: int, move: str) -> Tuple[int, int]:
    """Next Payee / Item cell for Tab ("next"), Shift+Tab ("prev") or Enter ("down")."""
    if move == "down":
        return row + 1, col
    if move == "prev":
        if col > _COL_ITEM:
            return row, _COL_ITEM
        if col == _COL_ITEM:
            return row, _COL_PAYEE
        return row - 1, _COL_ITEM
    if col < _COL_PAYEE:
        return row, _COL_PAYEE
    if col == _COL_PAYEE:
        return row, _COL_ITEM
    return row + 1, _COL_PAYEE


def _is_active_edit_row(view, row: int) -> bool:
    return (
        isinstance(view, QAbstractItemView)
        and view.state() == QAbstractItemView.EditingState
        and view.currentIndex().row() == row
    )


class _IssuesRowDelegate(QStyledItemDelegate):
    """Paints the row being edited plain white instead of the selection blue."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if _is_active_edit_row(self.parent(), index.row()):
            option.backgroundBrush = QBrush(QColor(_WHITE))
            option.state &= ~QStyle.State_Selected


class _IssuesCompleterDelegate(_IssuesRowDelegate):
    """Inline Payee / Item editor with contains completer.

    Tab / Shift+Tab / Enter accept the highlighted suggestion, commit, and
    hand ``(row, col, move)`` to ``navigate`` so the next box opens without
    a click.
    """

    def __init__(
        self,
        suggestions_getter,
        parent=None,
        *,
        uppercase: bool = False,
        navigate: Optional[Callable[[int, int, str], None]] = None,
        matcher: Optional[Callable[[str, List[str]], List[str]]] = None,
    ):
        super().__init__(parent)
        self._suggestions_getter = suggestions_getter
        self._uppercase = uppercase
        self._navigate = navigate
        self._matcher = matcher

    def createEditor(self, parent, option, index):
        ed = CompleterLineEdit(
            self._suggestions_getter() or [],
            parent=parent,
            ranked_contains=True,
            matcher=self._matcher,
        )
        ed.setStyleSheet(
            f"QLineEdit {{ color: {_T1}; background: {_WHITE};"
            f" border: 1px solid {_QB_CELL_BORDER}; border-radius: 0px;"
            " padding: 0 3px; }"
        )
        ed._issues_index = QPersistentModelIndex(index)  # type: ignore[attr-defined]
        self._repaint_view()
        return ed

    def destroyEditor(self, editor, index):
        super().destroyEditor(editor, index)
        self._repaint_view()

    def _repaint_view(self) -> None:
        view = self.parent()
        if isinstance(view, QAbstractItemView):
            view.viewport().update()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress and isinstance(obj, CompleterLineEdit):
            key = event.key()
            shift = bool(event.modifiers() & Qt.ShiftModifier)
            move = None
            if key == Qt.Key_Backtab or (key == Qt.Key_Tab and shift):
                move = "prev"
            elif key == Qt.Key_Tab:
                move = "next"
            elif key in (Qt.Key_Return, Qt.Key_Enter):
                move = "down"
            if move is not None:
                completer = getattr(obj, "_completer", None)
                if completer is not None:
                    accept_completion(obj, completer)
                    hide_completion_popup(completer)
                idx = getattr(obj, "_issues_index", None)
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QAbstractItemDelegate.NoHint)
                if self._navigate is not None and idx is not None and idx.isValid():
                    row, col = idx.row(), idx.column()
                    QTimer.singleShot(
                        0, lambda r=row, c=col, m=move: self._navigate(r, c, m)
                    )
                return True
        return super().eventFilter(obj, event)

    def paint(self, painter, option, index):
        if not _is_active_edit_row(self.parent(), index.row()):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        box = option.rect.adjusted(_BOX_INSET, _BOX_INSET, -_BOX_INSET, -_BOX_INSET)
        painter.save()
        painter.setPen(QPen(QColor(_QB_CELL_BORDER), 1))
        painter.setBrush(QBrush(QColor(_WHITE)))
        painter.drawRect(QRectF(box).adjusted(0.5, 0.5, -0.5, -0.5))
        if text:
            text_rect = box.adjusted(4, 0, -3, 0)
            painter.setPen(QColor(_T1))
            painter.setFont(opt.font)
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                opt.fontMetrics.elidedText(text, Qt.ElideRight, text_rect.width()),
            )
        painter.restore()

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        text = (editor.canonical(editor.text()) or editor.text() or "").strip()
        if self._uppercase:
            text = " ".join(text.upper().split())
        model.setData(index, text)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(
            option.rect.adjusted(_BOX_INSET, _BOX_INSET, -_BOX_INSET, -_BOX_INSET)
        )


class _NewPayeeDialog(QDialog):
    """Create a payee name to use in Map Descriptions (persisted on Save Mapping)."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        prefill: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Payee")
        self.setMinimumWidth(360)
        self.setStyleSheet(f"QDialog{{background:{_WHITE};color:{_T1};}}")
        self.result_name: str = ""
        self.result_currency: str = "TZS"

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)
        vl.addWidget(_lbl("New Payee", size=15, weight=700))
        vl.addWidget(_lbl(
            "Added to the Payee list and used for this mapping.",
            size=12, color=_T2,
        ))
        vl.addWidget(_lbl("NAME *", size=12, color=_T2))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("e.g. CITY COUNCIL")
        self._inp.setText(" ".join((prefill or "").upper().split()))
        self._inp.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;"
            "min-height:34px;max-height:34px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._inp.returnPressed.connect(self._accept)
        vl.addWidget(self._inp)

        vl.addWidget(_lbl("Currency", size=12, color=_T2))
        self._currency = QComboBox()
        self._currency.addItems(["TZS", "USD"])
        self._currency.setCurrentText("TZS")
        self._currency.setFixedHeight(34)
        self._currency.setStyleSheet(with_combo_popup(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:22px;}"
        ))
        vl.addWidget(self._currency)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("Add Payee", primary=True)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)
        self._inp.setFocus()
        self._inp.selectAll()

    def _accept(self) -> None:
        name = " ".join(self._inp.text().upper().split())
        if not name:
            QMessageBox.warning(self, "Validation", "Payee name is required.")
            return
        self.result_name = name
        self.result_currency = (self._currency.currentText() or "TZS").strip().upper()
        self.accept()


class MapDescriptionsDialog(QDialog):
    """Map one or many descriptions → the same Payee + Item."""

    def __init__(
        self,
        descriptions: Sequence[str],
        categories: List[Category],
        payees: Sequence[str],
        parent: Optional[QWidget] = None,
        *,
        prefill_payee: str = "",
        prefill_item: str = "",
    ) -> None:
        super().__init__(parent)
        self._descriptions = [d for d in descriptions if str(d).strip()]
        self._categories = sort_payment_targets(list(categories))
        self._payees = [str(p).strip().upper() for p in payees if str(p).strip()]
        self._known_payees_at_open: Set[str] = set(self._payees)
        self._pending_new_payees: Set[str] = set()
        self._pending_new_items: dict[str, dict] = {}
        self._payee_currencies: dict[str, str] = {}
        self.result_payee: str = ""
        self.result_currency: str = "TZS"
        self.result_category: Optional[Category] = None
        self.result_assignment: Optional[MappingAssignment] = None
        self.result_create_payee: bool = False
        n = len(self._descriptions)
        self.setWindowTitle(
            f"Map {n} Description{'s' if n != 1 else ''}"
            if n else "Map Description"
        )
        self.setMinimumWidth(520)
        self.setStyleSheet(f"QDialog{{background:{_WHITE};color:{_T1};}}")
        self._build(prefill_payee=prefill_payee, prefill_item=prefill_item)

    def _build(self, *, prefill_payee: str, prefill_item: str) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        n = len(self._descriptions)
        vl.addWidget(_lbl(
            f"Map {n} description{'s' if n != 1 else ''} → Payee + Item",
            size=15, weight=700,
        ))
        vl.addWidget(_lbl(
            "Type to search. All selected descriptions get the same Payee and Item.",
            size=12, color=_T2,
        ))

        preview = self._descriptions[:8]
        extra = n - len(preview)
        lines = "\n".join(f"• {name}" for name in preview)
        if extra > 0:
            lines += f"\n• … and {extra:,} more"
        summary = _lbl(lines, size=12, color=_T2)
        summary.setWordWrap(True)
        vl.addWidget(summary)

        # CompleterLineEdit (same as table inline editors): Tab accepts the
        # first highlighted suggestion, then advances focus to the next field.
        field_ss = (
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;"
            "min-height:34px;max-height:34px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        vl.addWidget(_lbl("PAYEE *", size=12, color=_T2))
        self._payee = CompleterLineEdit(
            list(self._payees), parent=self, ranked_contains=True,
        )
        self._payee.setStyleSheet(field_ss)
        if prefill_payee:
            payee_pre = " ".join(prefill_payee.upper().split())
            self._payee.setText(payee_pre)
            self._payee._typed = payee_pre
        vl.addWidget(self._payee)

        vl.addWidget(_lbl("ITEM *", size=12, color=_T2))
        self._item = CompleterLineEdit(
            [c.name for c in self._categories],
            parent=self,
            ranked_contains=True,
        )
        self._item.setStyleSheet(field_ss)
        if prefill_item:
            self._item.setText(prefill_item)
            self._item._typed = prefill_item
        vl.addWidget(self._item)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()
        new_payee = _btn("New Payee…", primary=False)
        new_payee.clicked.connect(self._on_new_payee)
        btn_row.addWidget(new_payee)
        new_item = _btn("New Item…", primary=False)
        new_item.clicked.connect(self._on_new_item)
        btn_row.addWidget(new_item)
        save = _btn("Save Mapping", primary=True)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

        self._payee.setFocus()

    def _select_payee(self, name: str, *, mark_create: bool = False) -> None:
        payee = " ".join((name or "").upper().split())
        if not payee:
            return
        if payee not in self._payees:
            self._payees.append(payee)
            self._payee._values = list(self._payees)
            self._payee._model.setStringList(self._payees)
        if mark_create and payee_needs_create(payee, self._known_payees_at_open):
            self._pending_new_payees.add(payee)
        self._payee.setText(payee)
        self._payee._typed = payee

    def _select_item(
        self,
        name: str,
        *,
        fields: Optional[dict] = None,
        mark_create: bool = False,
    ) -> None:
        """Put an item on the suggestion list and into the field immediately."""
        item_name = " ".join((name or "").split())
        if not item_name:
            return
        existing = next(
            (c for c in self._categories if c.name.strip().lower() == item_name.lower()),
            None,
        )
        if existing is None:
            self._categories.append(Category(name=item_name))
            self._categories = sort_payment_targets(self._categories)
        if mark_create and (existing is None or existing._id is None):
            self._pending_new_items[item_name.lower()] = dict(fields or {})
        names = [c.name for c in self._categories]
        self._item._values = list(names)
        self._item._model.setStringList(names)
        self._item.setText(item_name)
        self._item._typed = item_name

    def _on_new_payee(self) -> None:
        typed = " ".join(self._payee.text().upper().split())
        prefill = typed if payee_needs_create(typed, self._known_payees_at_open) else ""
        dlg = _NewPayeeDialog(parent=self, prefill=prefill)
        if dlg.exec() != QDialog.Accepted or not dlg.result_name:
            return
        self._payee_currencies[dlg.result_name] = dlg.result_currency or "TZS"
        self._select_payee(dlg.result_name, mark_create=True)

    def _on_new_item(self) -> None:
        typed = self._item.text().strip()
        known_saved = any(
            c.name.strip().lower() == typed.lower() and c._id is not None
            for c in self._categories
        )
        if typed and not known_saved:
            prefill = typed
        elif len(self._descriptions) == 1:
            prefill = self._descriptions[0]
        else:
            prefill = ""
        dlg = ItemDialog(parent=self, prefill_name=prefill)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dict(dlg.result_data or {})
        name = " ".join((data.get("name") or "").split())
        if not name:
            QMessageBox.warning(self, "New Item", "Item name is required.")
            return
        existing = next(
            (
                c for c in self._categories
                if c.name.strip().lower() == name.lower() and c._id is not None
            ),
            None,
        )
        if existing is not None:
            self._pending_new_items.pop(name.lower(), None)
            self._select_item(existing.name, mark_create=False)
            return
        # Stay open so the new name is a suggestion. Payee is required on Save.
        self._select_item(name, fields=data, mark_create=True)

    def _accept(self) -> None:
        payee = " ".join(self._payee.text().upper().split())
        raw_item = self._item.text().strip()
        item_name = self._item.canonical(raw_item) or raw_item
        if not self._descriptions:
            QMessageBox.warning(self, "Validation", "Select at least one description.")
            return
        if not payee:
            QMessageBox.warning(self, "Validation", "Payee is required.")
            return
        if not item_name:
            QMessageBox.warning(self, "Validation", "Item is required.")
            return
        create_payee = payee_needs_create(payee, self._known_payees_at_open)
        if create_payee and payee not in self._pending_new_payees:
            tip = next(
                (p for p in self._known_payees_at_open if payee in p or p in payee),
                "",
            )
            extra = f"\nDid you mean: {tip}?" if tip else ""
            if QMessageBox.question(
                self,
                "Create payee?",
                f"Payee {payee!r} is not in the Payee list.{extra}\n\n"
                "Create it and save this mapping?",
            ) != QMessageBox.Yes:
                return
            self._pending_new_payees.add(payee)
        cat = next(
            (c for c in self._categories if c.name.strip().lower() == item_name.lower()),
            None,
        )
        pending_fields = self._pending_new_items.get(item_name.lower())
        if pending_fields is None and (cat is None or cat._id is None):
            QMessageBox.warning(
                self,
                "Select Item",
                "Choose an existing item, or click New Item… to create one.",
            )
            return
        self.result_payee = payee
        self.result_create_payee = create_payee
        self.result_currency = self._payee_currencies.get(payee, "TZS")
        if pending_fields is not None:
            self.result_category = cat or Category(name=item_name)
            self.result_assignment = MappingAssignment(
                action="assign",
                description=self._descriptions[0],
                category=self.result_category,
                create_new=True,
                new_item_name=item_name,
                new_item_fields=dict(pending_fields),
            )
        else:
            self.result_category = cat
            self.result_assignment = MappingAssignment(
                action="assign",
                description=self._descriptions[0],
                category=cat,
                create_new=False,
                new_item_name="",
            )
        self.accept()


class ValueFilterDialog(QDialog):
    """Searchable multi-select filter for Unmapped / Bad map description values.

    ``selected_keys is None`` means show-all (pre-check every description).
    A non-empty set pre-checks only those keys. Apply returns the checked
    key set; empty / all-selected is normalized by the caller to no filter.
    """

    def __init__(
        self,
        issues: Sequence[DayIssue],
        selected_keys: Optional[set] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter VALUE")
        self.setMinimumWidth(440)
        self.setMinimumHeight(420)
        self.setStyleSheet(f"QDialog{{background:{_WHITE};color:{_T1};}}")
        self.result_keys: Optional[set] = None
        self._entries: List[DayIssue] = sorted(
            issues,
            key=lambda i: (0 if i.kind == "Unmapped" else 1, i.description.lower()),
        )
        all_keys = {i.description_key for i in self._entries}
        if selected_keys is None:
            self._checked_keys: set = set(all_keys)
        else:
            self._checked_keys = {k for k in selected_keys if k in all_keys}
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)
        root.addWidget(_lbl("Filter by description (VALUE)", size=15, weight=700))
        root.addWidget(_lbl(
            f"{len(self._entries)} description(s) · Unmapped first, then Bad map.",
            size=12, color=_T2,
        ))

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search descriptions…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._refilter)
        root.addWidget(self._search)

        self._select_all = QCheckBox("Select All (visible)")
        self._select_all.setTristate(True)
        self._select_all.setStyleSheet(_checkbox_style())
        self._select_all.stateChanged.connect(self._on_select_all)
        root.addWidget(self._select_all)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};font-size:12px;font-family:'Segoe UI';"
            f"color:{_T1};}}"
            "QListWidget::item{padding:3px 6px;}"
        )
        self._list.itemChanged.connect(self._on_item_changed)
        root.addWidget(self._list, 1)

        row = QHBoxLayout()
        clear_checks = _btn("Clear checks", primary=False)
        clear_checks.clicked.connect(self._clear_visible_checks)
        row.addWidget(clear_checks)
        clear_filter = _btn("Clear filter", primary=False)
        clear_filter.setToolTip("Show all descriptions (no VALUE filter)")
        clear_filter.clicked.connect(self._accept_cleared)
        row.addWidget(clear_filter)
        row.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        apply_btn = _btn("Apply Filter", primary=True)
        apply_btn.clicked.connect(self._accept)
        row.addWidget(apply_btn)
        root.addLayout(row)

        self._refilter("")
        self._search.setFocus()

    def _sync_checks_from_list(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            key = item.data(Qt.UserRole)
            if item.checkState() == Qt.Checked:
                self._checked_keys.add(key)
            else:
                self._checked_keys.discard(key)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._checked_keys.add(key)
        else:
            self._checked_keys.discard(key)
        self._sync_select_all_state()

    def _on_select_all(self, state: int) -> None:
        if state == int(Qt.PartiallyChecked):
            return
        checked = state == int(Qt.Checked)
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            key = item.data(Qt.UserRole)
            if checked:
                self._checked_keys.add(key)
            else:
                self._checked_keys.discard(key)
        self._list.blockSignals(False)
        self._sync_select_all_state()

    def _clear_visible_checks(self) -> None:
        self._select_all.blockSignals(True)
        self._select_all.setCheckState(Qt.Unchecked)
        self._select_all.blockSignals(False)
        self._on_select_all(int(Qt.Unchecked))

    def _sync_select_all_state(self) -> None:
        n = self._list.count()
        if n == 0:
            self._select_all.blockSignals(True)
            self._select_all.setCheckState(Qt.Unchecked)
            self._select_all.blockSignals(False)
            return
        n_checked = sum(
            1 for i in range(n)
            if self._list.item(i).checkState() == Qt.Checked
        )
        self._select_all.blockSignals(True)
        if n_checked == 0:
            self._select_all.setCheckState(Qt.Unchecked)
        elif n_checked == n:
            self._select_all.setCheckState(Qt.Checked)
        else:
            self._select_all.setCheckState(Qt.PartiallyChecked)
        self._select_all.blockSignals(False)

    def _refilter(self, text: str = "") -> None:
        self._sync_checks_from_list()
        needle = (text or self._search.text() or "").strip().lower()
        self._list.blockSignals(True)
        self._list.clear()
        for issue in self._entries:
            hay = f"{issue.kind} {issue.description}".lower()
            if needle and needle not in hay:
                continue
            label = f"[{issue.kind}] {issue.description}  ({issue.count})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, issue.description_key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked
                if issue.description_key in self._checked_keys
                else Qt.Unchecked
            )
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._sync_select_all_state()

    def _accept_cleared(self) -> None:
        self.result_keys = set()
        self.accept()

    def _accept(self) -> None:
        self._sync_checks_from_list()
        self.result_keys = set(self._checked_keys)
        self.accept()


class DayIssuesWidget(QWidget):
    """Cashier / accountant Day Issues page for one register date."""

    issues_changed = Signal(int)   # badge count
    open_register = Signal(object)  # date
    mappings_applied = Signal()     # refresh register rows
    auto_fill_maps_changed = Signal(bool)  # setting: auto-fill Table from maps

    def __init__(
        self,
        user: User,
        parent: Optional[QWidget] = None,
        *,
        row_source: Optional[Callable[[date], Tuple[Optional[list], Optional[list]]]] = None,
        apply_live: Optional[Callable[..., int]] = None,
        local_days: Optional[Callable[[], List[date]]] = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._target: date = date.today()
        self._scope: str = "day"  # "day" | "all"
        self._issues_all: List[DayIssue] = []
        self._issues: List[DayIssue] = []
        self._issue_dates: dict = {}  # description_key -> list[date]
        self._days_scanned: List[date] = []
        self._categories: List[Category] = []
        self._payees: List[str] = []
        self._restrict_items: bool = False
        self._restrict_payees: bool = False
        self._not_in_list_prompting: set = set()
        self._pending_inline_edit: Optional[Tuple[int, int]] = None
        # description_key -> {"payee": str, "item": str} inline drafts (Phase 6)
        self._mapping_drafts: dict = {}
        self._checked_keys: set = set()
        self._search_text: str = ""
        self._value_filter: Optional[set] = None  # description keys; None = all
        self._loading = False
        self._populating = False
        # Perf: skip full scan on tab return unless date/scope changed or dirty.
        self._loaded = False
        self._dirty = True
        # Per-day snapshots for All days (re-scan only dirty / live day).
        self._day_snap_cache: dict = {}
        # Verify handoff: All days limited to the blocked rows' days (None = every open day).
        self._scan_days: Optional[Tuple[date, ...]] = None
        self._lookups_loaded = False
        # One Map dialog/save at a time; a second click would race the first.
        self._map_in_flight = False
        # Manual Refresh: re-download Item/Payee lists instead of the cached snapshot.
        self._refetch_lookups = False
        # Optional: (replace_transactions, extra_transactions) for live/unsaved rows.
        self._row_source = row_source
        # Optional: register days with rows only this desktop has (All days).
        self._local_days = local_days
        # Optional: stamp Payee+Item onto live Table / draft rows (Phase 3).
        self._apply_live = apply_live
        # Last successful map — offered once via the success dialog Undo button.
        self._last_mapping_undo: Optional[MappingUndoSnapshot] = None
        # Cashier UI: own rows only. Accountant / admin: everyone.
        self._cashier_scope_id: Optional[ObjectId] = (
            user._id if getattr(user, "role", "") == "cashier" else None
        )
        self._source_refresh_timer = QTimer(self)
        self._source_refresh_timer.setSingleShot(True)
        self._source_refresh_timer.setInterval(350)
        self._source_refresh_timer.timeout.connect(self._schedule_refresh)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(
                qta.icon("mdi.alert-decagram-outline", color=_BLUE).pixmap(24, 24)
            )
            icon_lbl.setFixedSize(24, 24)
            icon_lbl.setStyleSheet("background:transparent;")
            hdr.addWidget(icon_lbl)
        except Exception:
            pass
        # Title + count chip match Description Maps / Drafts (system navy + QB blue).
        hdr.addWidget(_lbl("Day Issues", size=18, weight=700, color=_NAVY))

        self._count_chip = QLabel("—")
        self._count_chip.setStyleSheet(
            f"background:{_BLUE_L};color:{_BLUE};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;font-family:'Segoe UI';"
        )
        hdr.addWidget(self._count_chip)
        hdr.addStretch()

        hdr.addWidget(_lbl("SHOW", size=12, color=_T2))
        self._scope_combo = QComboBox()
        self._scope_combo.addItem("This day", "day")
        self._scope_combo.addItem("All days", "all")
        self._scope_combo.setFixedHeight(32)
        self._scope_combo.setFixedWidth(110)
        self._scope_combo.setStyleSheet(with_combo_popup(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
        ))
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        hdr.addWidget(self._scope_combo)

        hdr.addWidget(_lbl("Date", size=12, color=_T2))
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("dd MMM yyyy")
        self._date.setDate(QDate.currentDate())
        self._date.setFixedHeight(32)
        self._date.setFixedWidth(140)
        self._date.setStyleSheet(
            f"QDateEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
        )
        style_calendar_popup(self._date)
        self._date.dateChanged.connect(self._on_date_changed)
        hdr.addWidget(self._date)

        refresh = _btn("Refresh", primary=False)
        refresh.clicked.connect(lambda: self.force_refresh())
        hdr.addWidget(refresh)

        self._open_reg_btn = _btn("Open Table", primary=False)
        self._open_reg_btn.clicked.connect(
            lambda: self.open_register.emit(self._target)
        )
        hdr.addWidget(self._open_reg_btn)
        root.addLayout(hdr)

        scope_note = (
            "Showing your incomplete rows only."
            if self._cashier_scope_id is not None
            else "Showing incomplete rows for all cashiers."
        )
        hint = _lbl(
            f"{scope_note} Double-click Payee / Item to fill inline, then "
            "Save Filled Mappings — or Map Selected for a dialog. Submit is "
            "blocked while issues remain (unless the accountant allows it).",
            size=12, color=_T2,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        opts = QHBoxLayout()
        opts.setSpacing(8)
        self._auto_fill_maps_chk = QCheckBox(
            "Auto-fill Item / Payee from Description Maps when typing or pasting"
        )
        self._auto_fill_maps_chk.setChecked(True)
        self._auto_fill_maps_chk.setStyleSheet(_checkbox_style())
        self._auto_fill_maps_chk.setToolTip(
            "When on, a known description map fills blank Item and Payee cells "
            "on the Table. Partial maps fill what they have; Day Issues still "
            "lists rows that remain incomplete. Turn off to map only here."
        )
        self._auto_fill_maps_chk.toggled.connect(self._on_auto_fill_maps_toggled)
        opts.addWidget(self._auto_fill_maps_chk)
        opts.addStretch(1)
        root.addLayout(opts)
        # Load persisted setting without racing the toggled handler.
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._load_auto_fill_maps_setting())

        # One toolbar row: search first, then actions, status at the end.
        tools = QHBoxLayout()
        tools.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search description, payee, item, or type…")
        self._search.setFixedHeight(32)
        self._search.setMinimumWidth(200)
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:#F9FAFB;color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 10px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};background:{_WHITE};}}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        tools.addWidget(self._search, 1)

        self._search_clear_btn = _btn("Search", primary=False)
        self._search_clear_btn.setFixedWidth(72)
        self._search_clear_btn.setToolTip("Clear the search filter")
        self._search_clear_btn.clicked.connect(self._on_search_clear_clicked)
        tools.addWidget(self._search_clear_btn)

        self._filter_btn = _btn("Filter VALUE…", primary=False)
        self._filter_btn.clicked.connect(self._open_value_filter)
        tools.addWidget(self._filter_btn)
        self._value_chip = QLabel("")
        self._value_chip.setStyleSheet(
            f"background:{_BLUE_L};color:{_BLUE};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;font-family:'Segoe UI';"
        )
        self._value_chip.setVisible(False)
        tools.addWidget(self._value_chip)
        self._clear_filter_btn = _btn("Clear VALUE Filter", primary=False)
        self._clear_filter_btn.clicked.connect(self._clear_value_filter)
        self._clear_filter_btn.setEnabled(False)
        self._clear_filter_btn.setVisible(False)
        tools.addWidget(self._clear_filter_btn)

        self._map_btn = _btn("Map Selected", primary=True)
        self._map_btn.clicked.connect(self._map_selected)
        tools.addWidget(self._map_btn)
        self._map_all_btn = _btn("Map All Unmapped…", primary=False)
        self._map_all_btn.clicked.connect(self._map_all_unmapped)
        tools.addWidget(self._map_all_btn)
        self._save_filled_btn = _btn("Save Filled Mappings", primary=True)
        self._save_filled_btn.clicked.connect(self._schedule_save_filled)
        self._save_filled_btn.setEnabled(False)
        tools.addWidget(self._save_filled_btn)
        check_hl = _btn("Check Highlighted", primary=False)
        check_hl.clicked.connect(self._check_highlighted)
        tools.addWidget(check_hl)
        clear_chk = _btn("Clear Checks", primary=False)
        clear_chk.clicked.connect(self._clear_checks)
        tools.addWidget(clear_chk)

        tools.addStretch(1)
        self._status = _lbl("", size=12, color=_T2)
        tools.addWidget(self._status)
        root.addLayout(tools)

        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame{background:transparent;border:none;}")
        tv = QVBoxLayout(self._table_host)
        tv.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["", "#", "Type", "Description", "Rows", "Payee", "Item", "Detail"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        # Extra right padding so Description ▾ chevron does not collide with text.
        # Table row ☐ indicators: faint-blue fill + visible checkmark.
        # Override shared _table_style selection — pale #E8F4FD is nearly invisible.
        self._table.setStyleSheet(
            _table_style()
            + "QHeaderView::section{padding:0 18px 0 6px;}"
            + _table_checkbox_indicator_qss()
            + f"QTableWidget::item:selected{{background:{_SEL_BG};color:{_SEL_FG};}}"
        )
        self._table.setItemDelegate(_IssuesRowDelegate(self._table))
        self._table.installEventFilter(self)
        self._table.setItemDelegateForColumn(
            _COL_PAYEE,
            _IssuesCompleterDelegate(
                lambda: list(self._payees),
                self._table,
                uppercase=True,
                navigate=self._advance_inline_edit,
            ),
        )
        self._table.setItemDelegateForColumn(
            _COL_ITEM,
            _IssuesCompleterDelegate(
                lambda: [c.name for c in self._categories],
                self._table,
                uppercase=False,
                navigate=self._advance_inline_edit,
            ),
        )
        self._table.itemChanged.connect(self._on_issue_cell_changed)
        self._table.clicked.connect(self._on_issue_cell_clicked)

        filter_hdr = ExcelFilterHeaderView(
            self._table,
            filterable_columns={_COL_DESC},
            sort_kinds={_COL_DESC: "text"},
        )
        filter_hdr.set_value_provider(self._desc_filter_menu_values)
        filter_hdr.set_label_provider(
            lambda c: "Description" if c == _COL_DESC else "",
        )
        filter_hdr.filter_changed.connect(self._on_desc_header_filter)
        filter_hdr.sort_requested.connect(self._on_desc_header_sort)
        self._table.setHorizontalHeader(filter_hdr)

        hdr_view = self._table.horizontalHeader()
        hdr_view.setStretchLastSection(False)
        hdr_view.setSectionsMovable(False)
        hdr_view.setMinimumSectionSize(36)
        for i, width in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
            if i in _FIXED_COLS:
                hdr_view.setSectionResizeMode(i, QHeaderView.Fixed)
            else:
                hdr_view.setSectionResizeMode(i, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._table,
            _COL_KEY,
            _COL_DEFAULTS,
            auto_fit_if_unset=True,
        )
        tv.addWidget(self._table)
        root.addWidget(self._table_host, 1)

        self._loading_overlay = LoadingOverlay(self._table_host, "Loading…")
        self._footer = _lbl("", size=11, color=_TM)
        root.addWidget(self._footer)
        self._wire_mapping_apply_outbox()

    def _wire_mapping_apply_outbox(self) -> None:
        """Background apply exhausted: the saved map stays, the row comes back."""
        from tahmeed.services.mapping_apply_outbox import set_failure_handler

        def _on_failed(job, exc: BaseException) -> None:
            try:
                self._footer.setText(
                    f"Saved map is in place, but {len(job.description_keys)} "
                    "description(s) could not be written onto register rows. "
                    "They are back on this list."
                )
                # Retries are exhausted. Show the description again.
                self.mark_dirty(days=list(job.dates) or None)
                self._schedule_refresh()
            except Exception:
                pass
            _ = exc

        set_failure_handler(_on_failed)

    def _schedule_refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self.refresh())

    async def _await_ui(self, fn: Callable[[], object]):
        """Run a modal dialog from a Qt tick while this task is suspended.

        exec() inside a running task blocks other tasks' wake-ups on Python
        3.14 / qasync, and a dropped wake-up freezes that task for good.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()

        def _run() -> None:
            if fut.done():
                return
            try:
                fut.set_result(fn())
            except Exception as exc:
                fut.set_exception(exc)

        QTimer.singleShot(0, _run)
        return await fut

    def _kick_mapping_apply_drain(self) -> None:
        from tahmeed.services.mapping_apply_outbox import drain_mapping_apply_outbox
        from tahmeed.ui.async_utils import schedule_coro

        schedule_coro(drain_mapping_apply_outbox())

    async def _load_auto_fill_maps_setting(self) -> None:
        from tahmeed.services.settings_service import get_setting

        try:
            on = bool(await get_setting("auto_fill_from_description_maps"))
        except Exception:
            on = True
        self._auto_fill_maps_chk.blockSignals(True)
        self._auto_fill_maps_chk.setChecked(on)
        self._auto_fill_maps_chk.blockSignals(False)
        self.auto_fill_maps_changed.emit(on)
        try:
            self._restrict_items = bool(await get_setting("restrict_items"))
        except Exception:
            self._restrict_items = False
        try:
            self._restrict_payees = bool(await get_setting("restrict_payees"))
        except Exception:
            self._restrict_payees = False

    def _on_auto_fill_maps_toggled(self, on: bool) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._save_auto_fill_maps_setting(bool(on)))

    async def _save_auto_fill_maps_setting(self, on: bool) -> None:
        from tahmeed.services.settings_service import set_setting

        try:
            await set_setting("auto_fill_from_description_maps", on)
        except Exception as exc:
            msg = f"Could not save auto-fill preference:\n{exc}"
            await self._await_ui(
                lambda: QMessageBox.warning(self, "Setting not saved", msg)
            )
            # Revert checkbox to last known server/default without re-saving.
            self._auto_fill_maps_chk.blockSignals(True)
            self._auto_fill_maps_chk.setChecked(not on)
            self._auto_fill_maps_chk.blockSignals(False)
            return
        self.auto_fill_maps_changed.emit(on)

    def force_refresh(self) -> None:
        """Manual Refresh — always re-scan (clears All-days cache)."""
        self._dirty = True
        self._day_snap_cache.clear()
        self._lookups_loaded = False
        self._refetch_lookups = True
        self._source_refresh_timer.stop()
        self._schedule_refresh()

    def mark_dirty(self, days: Optional[Sequence[date]] = None) -> None:
        """Register rows / mappings changed — next show or soft refresh will scan."""
        self._dirty = True
        if days is None:
            self._day_snap_cache.clear()
        else:
            for d in days:
                if isinstance(d, date):
                    self._day_snap_cache.pop(d, None)

    def on_register_source_changed(self, *, visible: bool) -> None:
        """Table Desc/Payee/Item changed. Debounced refresh only while this page is shown."""
        # Only the open register day changed — keep other All-days cache entries.
        self.mark_dirty(days=[self._target])
        if not visible:
            self._source_refresh_timer.stop()
            return
        self._source_refresh_timer.start()

    def _schedule_save_filled(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._save_filled_mappings())

    def set_date(self, target: date, *, force: bool = False) -> None:
        """Set the register date. Full scan only when needed (or *force*)."""
        date_changed = target != self._target
        self._target = target
        qd = QDate(target.year, target.month, target.day)
        self._date.blockSignals(True)
        self._date.setDate(qd)
        self._date.blockSignals(False)
        if force or date_changed or self._dirty or not self._loaded:
            self._schedule_refresh()

    def _set_show_scope(self, scope: str) -> None:
        """Switch SHOW combo without emitting (caller schedules refresh)."""
        scope = "all" if scope == "all" else "day"
        if self._scope == scope:
            return
        self._scope_combo.blockSignals(True)
        idx = self._scope_combo.findData(scope)
        if idx >= 0:
            self._scope_combo.setCurrentIndex(idx)
        self._scope_combo.blockSignals(False)
        self._scope = scope
        self._date.setEnabled(scope == "day")
        self._open_reg_btn.setEnabled(scope == "day")
        self._dirty = True

    def open_for_date(self, target: date) -> None:
        """Deep-link from Table (or first visit): This day + *target*, then load."""
        self._scan_days = None
        self._set_show_scope("day")
        self.set_date(target, force=True)

    def on_page_shown(self, fallback_date: Optional[date] = None) -> None:
        """Sidebar returned to Day Issues — keep date / scope / VALUE filter.

        Verify handoff (``open_from_verify``) and any in-page filters must survive
        leaving the tab. Only a true first visit (never loaded, no handoff) falls
        back to ``open_for_date``.
        """
        preserve = (
            self._loaded
            or self._loading
            or self._value_filter is not None
            or self._scope == "all"
            or bool(self._checked_keys)
        )
        if preserve:
            # Already loaded and clean: keep the list on screen. No scan.
            if self._dirty or not self._loaded:
                self._schedule_refresh()
            return
        day = fallback_date or self._target or date.today()
        self.open_for_date(day)

    async def _reload_catalogs(self) -> None:
        """Re-read Item + Payee suggestion lists without scanning issues.

        Does not clear the payee cache. ``add_payee`` already drops it, so a
        warm cache stays a memory read and a new payee is fetched once.
        """
        cats, payees = await asyncio.gather(
            get_payment_target_categories(),
            get_payee_names(active_only=True),
        )
        self._categories = sort_payment_targets(list(cats))
        self._payees = list(payees)
        self._lookups_loaded = True

    def open_from_verify(
        self,
        target: Optional[date] = None,
        *,
        description_keys: Optional[Sequence[str]] = None,
        all_days: bool = False,
        days: Optional[Sequence[date]] = None,
    ) -> None:
        """Deep-link from Verify approve gate: list the blocked descriptions.

        Applies a VALUE filter for *description_keys* and pre-checks them for
        Map Selected. Uses All days when the selection spans multiple register
        days so every blocked description can appear; *days* limits that scan
        to the blocked rows' register days.
        """
        keys = {
            normalize_description(k)
            for k in (description_keys or ())
            if (k or "").strip()
        }
        keys.discard("")
        if keys:
            self._value_filter = set(keys)
            self._checked_keys = set(keys)
            if self._search.text() or self._search_text:
                self._search.blockSignals(True)
                self._search.clear()
                self._search.blockSignals(False)
                self._on_search_changed("")
            self._sync_value_filter_chrome()
        day = target or date.today()
        if all_days:
            scan = tuple(sorted({d for d in (days or ()) if isinstance(d, date)}))
            self._scan_days = scan or None
            self._set_show_scope("all")
            self.set_date(day, force=True)
        else:
            self.open_for_date(day)

    def _on_date_changed(self, qd: QDate) -> None:
        self._target = date(qd.year(), qd.month(), qd.day())
        if self._scope == "day":
            self._dirty = True
            self._schedule_refresh()

    def _on_scope_changed(self, _index: int = 0) -> None:
        self._scan_days = None
        self._scope = self._scope_combo.currentData() or "day"
        self._date.setEnabled(self._scope == "day")
        self._open_reg_btn.setEnabled(self._scope == "day")
        self._dirty = True
        self._schedule_refresh()

    def _row_source_args(self) -> tuple:
        replace_txs = None
        extra_txs = None
        if self._row_source is not None:
            try:
                replace_txs, extra_txs = self._row_source(self._target)
            except Exception:
                replace_txs, extra_txs = None, None
        return replace_txs, extra_txs

    def _all_days_local_rows(self, replace_txs, extra_txs) -> dict:
        """Live / unsaved register rows per day for the All-days scan."""
        rows = {self._target: (replace_txs, extra_txs)}
        if self._local_days is None or self._row_source is None:
            return rows
        try:
            days = list(self._local_days() or [])
        except Exception:
            days = []
        if self._scan_days:
            days = [d for d in days if d in self._scan_days]
        for d in days:
            if d in rows:
                continue
            try:
                rows[d] = self._row_source(d)
            except Exception:
                continue
        return rows

    def _drop_issue_keys(self, keys: set) -> None:
        """Instant UI update after map — remove resolved descriptions from the list."""
        if not keys:
            return
        self._issues_all = [
            i for i in self._issues_all if i.description_key not in keys
        ]
        for key in keys:
            self._issue_dates.pop(key, None)
            self._checked_keys.discard(key)
            self._mapping_drafts.pop(key, None)
        self._strip_cached_issue_keys(keys)
        if self._value_filter is not None:
            kept = self._value_filter - keys
            self._value_filter = kept or None
        self._sync_value_filter_chrome()
        self._apply_filters(repaint=False)
        self._populate()
        self.issues_changed.emit(len(self._issues_all))

    def _strip_cached_issue_keys(self, keys: set) -> None:
        """Keep All-days cache from painting a just-mapped description again."""
        if not keys:
            return
        for snap in self._day_snap_cache.values():
            issues = getattr(snap, "issues", None)
            if not issues:
                continue
            kept = [issue for issue in issues if issue.description_key not in keys]
            if len(kept) == len(issues):
                continue
            snap.issues = kept
            dates = getattr(snap, "issue_dates", None)
            if isinstance(dates, dict):
                for key in keys:
                    dates.pop(key, None)

    def _hide_applying_issues(self, snap) -> None:
        """Drop descriptions whose row update has not reached the register yet."""
        from tahmeed.services.mapping_apply_outbox import applying_description_keys

        pending = applying_description_keys()
        if not pending or snap is None:
            return
        issues = getattr(snap, "issues", None)
        if not issues:
            return
        snap.issues = [issue for issue in issues if issue.description_key not in pending]
        dates = getattr(snap, "issue_dates", None)
        if isinstance(dates, dict):
            for key in pending:
                dates.pop(key, None)

    async def refresh(self, *, quiet: bool = False) -> None:
        if self._loading:
            return
        self._kick_mapping_apply_drain()
        self._loading = True
        if not quiet:
            self._loading_overlay.show_loading("Scanning day issues…")
        error_msg: Optional[str] = None
        try:
            replace_txs, extra_txs = self._row_source_args()
            need_lookups = not self._lookups_loaded or not self._categories or not self._payees
            if need_lookups:
                if self._refetch_lookups:
                    from tahmeed.services.catalog_service import fetch_catalog_snapshot

                    await fetch_catalog_snapshot(refresh=True)
                    invalidate_payee_cache()
                    self._refetch_lookups = False
                cats, payees = await asyncio.gather(
                    get_payment_target_categories(),
                    get_payee_names(active_only=True),
                )
                self._categories = sort_payment_targets(list(cats))
                self._payees = list(payees)
                self._lookups_loaded = True
            # Pass the payee list in so the scan does not download it again.
            if self._scope == "all":
                snap = await collect_day_issues_all_days(
                    cashier_id=self._cashier_scope_id,
                    anchor_date=self._target,
                    local_rows=self._all_days_local_rows(replace_txs, extra_txs),
                    day_cache=self._day_snap_cache,
                    payee_names=self._payees,
                    only_days=self._scan_days,
                    progress=None if quiet else self._show_scan_progress,
                )
            else:
                snap = await collect_day_issues(
                    self._target,
                    transactions=replace_txs,
                    extra_transactions=extra_txs,
                    cashier_id=self._cashier_scope_id,
                    payee_names=self._payees,
                )
            self._hide_applying_issues(snap)
            self._issues_all = list(snap.issues)
            self._issue_dates = dict(getattr(snap, "issue_dates", None) or {})
            self._days_scanned = list(getattr(snap, "days_scanned", None) or [])
            if self._scope == "day":
                self._days_scanned = [self._target]
                self._issue_dates = {
                    i.description_key: [self._target] for i in self._issues_all
                }
                # Keep single-day cache in sync for later All-days switches.
                self._day_snap_cache[self._target] = snap
            live_keys = {i.description_key for i in self._issues_all}
            self._checked_keys &= live_keys
            if self._value_filter is not None:
                kept = self._value_filter & live_keys
                self._value_filter = kept or None
            self._sync_value_filter_chrome()
            self._apply_filters(repaint=False)
            self._populate()
            self.issues_changed.emit(len(self._issues_all))
            self._dirty = False
            self._loaded = True
        except Exception as exc:
            error_msg = f"Could not load issues:\n{exc}"
            self.issues_changed.emit(0)
        finally:
            self._loading = False
            if not quiet:
                self._loading_overlay.hide_loading()
        if error_msg is not None:
            await self._await_ui(
                lambda: QMessageBox.critical(self, "Day Issues", error_msg)
            )

    def _show_scan_progress(self, done: int, total: int) -> None:
        if total <= 1:
            return
        self._loading_overlay.show_loading(
            f"Scanning day issues: {done} of {total} days",
            value=done,
            maximum=total,
        )

    def _apply_filters(self, *, repaint: bool = True) -> None:
        self._issues = filter_day_issues(
            self._issues_all,
            search=self._search_text,
            value_filter=self._value_filter,
        )
        if repaint:
            self._populate()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text or ""
        has_text = bool((text or "").strip())
        self._search_clear_btn.setText("Clear" if has_text else "Search")
        self._search_clear_btn.setStyleSheet(
            (
                f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
                "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
                f"QPushButton:hover{{background:#005EA3;}}"
            )
            if has_text else
            (
                f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
                "border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
                f"QPushButton:hover{{background:{_BG};}}"
            )
        )
        self._apply_filters()

    def _on_search_clear_clicked(self) -> None:
        if self._search.text():
            self._search.clear()
            self._search.setFocus()
        else:
            self._search.setFocus()

    def _sync_value_filter_chrome(self) -> None:
        active = self._value_filter is not None
        self._clear_filter_btn.setEnabled(active)
        self._clear_filter_btn.setVisible(active)
        if active:
            n = len(self._value_filter)
            self._value_chip.setText(f"VALUE · {n}")
            self._value_chip.setVisible(True)
            self._filter_btn.setText("Edit VALUE…")
        else:
            self._value_chip.clear()
            self._value_chip.setVisible(False)
            self._filter_btn.setText("Filter VALUE…")
        self._sync_desc_header_filter()

    def _sync_desc_header_filter(self) -> None:
        hdr = self._table.horizontalHeader()
        if not isinstance(hdr, ExcelFilterHeaderView):
            return
        displays = description_displays_for_value_filter(
            self._value_filter, self._issues_all,
        )
        if displays:
            hdr.sync_active({_COL_DESC: displays})
        else:
            hdr.clear_filters()

    def _desc_filter_menu_values(self, col: int) -> set:
        if col != _COL_DESC:
            return set()
        return {i.description for i in self._issues_all if i.description}

    def _on_desc_header_filter(self, col: int, accepted) -> None:
        if col != _COL_DESC:
            return
        accepted_set = set(accepted or [])
        if not accepted_set:
            self._value_filter = None
        else:
            self._value_filter = value_filter_from_descriptions(
                accepted_set, self._issues_all,
            )
        self._sync_value_filter_chrome()
        self._apply_filters()

    def _on_desc_header_sort(self, col: int, mode: str) -> None:
        if col != _COL_DESC:
            return
        reverse = mode == SORT_DESC
        self._issues_all.sort(
            key=lambda i: (i.description or "").lower(),
            reverse=reverse,
        )
        self._apply_filters()

    def _open_value_filter(self) -> None:
        if not self._issues_all:
            QMessageBox.information(self, "Filter VALUE", "No issues to filter.")
            return
        dlg = ValueFilterDialog(
            self._issues_all,
            selected_keys=self._value_filter,  # None = pre-check all
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        all_keys = {i.description_key for i in self._issues_all}
        self._value_filter = resolve_value_filter_selection(
            dlg.result_keys or set(), all_keys,
        )
        self._sync_value_filter_chrome()
        self._apply_filters()

    def _clear_value_filter(self) -> None:
        self._value_filter = None
        self._sync_value_filter_chrome()
        self._apply_filters()

    def _check_highlighted(self) -> None:
        for issue in self._selected_issues():
            self._checked_keys.add(issue.description_key)
        self._populate()

    def _clear_checks(self) -> None:
        self._checked_keys.clear()
        self._populate()

    def _draft_key(self, description: str) -> str:
        return normalize_description(description)

    def _store_draft(self, description: str, payee: str, item: str) -> None:
        key = self._draft_key(description)
        if not key:
            return
        payee_text = " ".join((payee or "").strip().upper().split())
        item_text = (item or "").strip()
        if payee_text in {"—", "-"}:
            payee_text = ""
        if item_text in {"—", "-"}:
            item_text = ""
        if not payee_text and not item_text:
            self._mapping_drafts.pop(key, None)
            return
        self._mapping_drafts[key] = {
            "description": description,
            "payee": payee_text,
            "item": item_text,
        }

    def _filled_drafts(self) -> List[tuple]:
        """Return (description, payee, item) for drafts with both fields set."""
        out: List[tuple] = []
        for issue in self._issues_all:
            key = issue.description_key
            draft = self._mapping_drafts.get(key) or {}
            payee = (draft.get("payee") or "").strip()
            item = (draft.get("item") or "").strip()
            if payee and item:
                out.append((issue.description, payee, item))
        return out

    def _update_save_filled_enabled(self) -> None:
        filled = self._filled_drafts()
        self._save_filled_btn.setEnabled(bool(filled))
        if filled:
            base = self._status.text().split("  ·  ready")[0]
            self._status.setText(f"{base}  ·  {len(filled)} ready to Save Filled")

    def _on_issue_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._populating or item is None:
            return
        if item.column() == _COL_CHECK:
            row = item.row()
            if 0 <= row < len(self._issues):
                key = self._issues[row].description_key
                if item.checkState() == Qt.Checked:
                    self._checked_keys.add(key)
                else:
                    self._checked_keys.discard(key)
            return
        if item.column() not in (_COL_PAYEE, _COL_ITEM):
            return
        row = item.row()
        if row < 0 or row >= len(self._issues):
            return
        issue = self._issues[row]
        payee_it = self._table.item(row, _COL_PAYEE)
        item_it = self._table.item(row, _COL_ITEM)
        payee = (payee_it.text() if payee_it else "").strip()
        item_name = (item_it.text() if item_it else "").strip()
        self._store_draft(issue.description, payee, item_name)
        self._update_save_filled_enabled()
        if item.column() == _COL_ITEM and item_name:
            QTimer.singleShot(
                0, lambda r=row, t=item_name: self._offer_add_unknown_item(r, t)
            )
        elif item.column() == _COL_PAYEE and payee:
            QTimer.singleShot(
                0, lambda r=row, t=payee: self._offer_add_unknown_payee(r, t)
            )

    def _set_issue_cell(self, row: int, col: int, text: str) -> None:
        it = self._table.item(row, col)
        if it is None:
            return
        self._table.blockSignals(True)
        it.setText(text)
        self._table.blockSignals(False)
        if 0 <= row < len(self._issues):
            issue = self._issues[row]
            payee_it = self._table.item(row, _COL_PAYEE)
            item_it = self._table.item(row, _COL_ITEM)
            self._store_draft(
                issue.description,
                (payee_it.text() if payee_it else "").strip(),
                (item_it.text() if item_it else "").strip(),
            )
            self._update_save_filled_enabled()

    def _offer_add_unknown_item(self, row: int, text: str) -> None:
        if not self._restrict_items or self._populating or not self._categories:
            return
        key = (row, _COL_ITEM, text)
        if key in self._not_in_list_prompting:
            return
        it = self._table.item(row, _COL_ITEM)
        if it is None or it.text().strip() != text:
            return
        if any(c.name.strip().lower() == text.strip().lower() for c in self._categories):
            return
        self._not_in_list_prompting.add(key)
        try:
            from tahmeed.ui.dialogs.not_in_list_dialog import (
                ask_not_in_list,
                run_add_item_dialog,
            )

            if not ask_not_in_list(self, entity="Item", name=text):
                self._set_issue_cell(row, _COL_ITEM, "")
                return
            data = run_add_item_dialog(self, prefill_name=text)
            if not data:
                self._set_issue_cell(row, _COL_ITEM, "")
                return
            from tahmeed.ui.async_utils import schedule_coro
            schedule_coro(self._finish_add_item(row, data))
        finally:
            self._not_in_list_prompting.discard(key)
            self._flush_pending_inline_edit()

    def _offer_add_unknown_payee(self, row: int, text: str) -> None:
        if not self._restrict_payees or self._populating or not self._payees:
            return
        key = (row, _COL_PAYEE, text)
        if key in self._not_in_list_prompting:
            return
        it = self._table.item(row, _COL_PAYEE)
        if it is None or it.text().strip() != text:
            return
        if not payee_needs_create(text, self._payees):
            return
        self._not_in_list_prompting.add(key)
        try:
            from tahmeed.ui.dialogs.not_in_list_dialog import (
                ask_not_in_list,
                run_add_payee_dialog,
            )

            if not ask_not_in_list(self, entity="Payee", name=text):
                self._set_issue_cell(row, _COL_PAYEE, "")
                return
            created = run_add_payee_dialog(self, prefill=text)
            if not created:
                self._set_issue_cell(row, _COL_PAYEE, "")
                return
            name, currency = created
            from tahmeed.ui.async_utils import schedule_coro
            schedule_coro(self._finish_add_payee(row, name, currency=currency))
        finally:
            self._not_in_list_prompting.discard(key)
            self._flush_pending_inline_edit()

    async def _finish_add_item(self, row: int, data: dict) -> None:
        from tahmeed.services.mapping_assignment_service import create_item_for_mapping

        try:
            cat = await create_item_for_mapping(data.get("name") or "", data)
        except Exception as exc:
            msg = f"Failed to create item:\n{exc}"
            self._set_issue_cell(row, _COL_ITEM, "")
            await self._await_ui(lambda: QMessageBox.critical(self, "Error", msg))
            return
        if not any(c.name.lower() == cat.name.lower() for c in self._categories):
            self._categories.append(cat)
            self._categories = sort_payment_targets(self._categories)
        self._set_issue_cell(row, _COL_ITEM, cat.name.upper())

    async def _finish_add_payee(
        self, row: int, name: str, *, currency: str = "TZS",
    ) -> None:
        try:
            await add_payee(name, currency=currency or "TZS")
        except Exception as exc:
            msg = f"Failed to create payee:\n{exc}"
            self._set_issue_cell(row, _COL_PAYEE, "")
            await self._await_ui(lambda: QMessageBox.critical(self, "Error", msg))
            return
        normalized = " ".join(name.upper().split())
        if payee_needs_create(normalized, self._payees):
            self._payees.append(normalized)
        self._set_issue_cell(row, _COL_PAYEE, normalized)

    def _on_issue_cell_clicked(self, index) -> None:
        if not index.isValid() or index.column() not in (_COL_PAYEE, _COL_ITEM):
            return
        if QApplication.keyboardModifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return
        self._table.edit(index)

    def eventFilter(self, obj, event) -> bool:
        # With no box open, Tab would walk every column (# / Type / Description…).
        # Keep it on the Payee / Item boxes instead.
        if (
            obj is self._table
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)
            and self._table.state() != QAbstractItemView.EditingState
        ):
            if self._table.rowCount():
                back = event.key() == Qt.Key_Backtab or bool(
                    event.modifiers() & Qt.ShiftModifier
                )
                cur = self._table.currentIndex()
                if cur.isValid():
                    self._advance_inline_edit(
                        cur.row(), cur.column(), "prev" if back else "next"
                    )
                else:
                    self._open_inline_edit(0, _COL_PAYEE)
            return True
        return super().eventFilter(obj, event)

    def _advance_inline_edit(self, row: int, col: int, move: str) -> None:
        """Open the next Payee / Item box after Tab, Shift+Tab or Enter."""
        target = inline_edit_target(row, col, move)
        if self._not_in_list_prompting:
            self._pending_inline_edit = target
            return
        self._open_inline_edit(*target)

    def _open_inline_edit(self, row: int, col: int) -> None:
        if self._populating or not 0 <= row < self._table.rowCount():
            return
        index = self._table.model().index(row, col)
        self._table.setCurrentIndex(index)
        self._table.scrollTo(index)
        self._table.edit(index)

    def _flush_pending_inline_edit(self) -> None:
        if self._not_in_list_prompting or self._pending_inline_edit is None:
            return
        target, self._pending_inline_edit = self._pending_inline_edit, None
        QTimer.singleShot(0, lambda: self._open_inline_edit(*target))

    def _end_inline_edit(self) -> None:
        """Close any open cell editor before the table is rebuilt."""
        idx = self._table.currentIndex()
        if idx.isValid() and self._table.isPersistentEditorOpen(idx):
            self._table.closePersistentEditor(idx)
        focus = QApplication.focusWidget()
        if focus is not None and self._table.isAncestorOf(focus):
            self._table.setFocus(Qt.OtherFocusReason)

    def _populate(self) -> None:
        issues = self._issues
        self._populating = True
        self._pending_inline_edit = None
        self._end_inline_edit()
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            n = len(issues)
            total = len(self._issues_all)
            self._count_chip.setText(
                f"{n} shown" if n != total else f"{n} issue{'s' if n != 1 else ''}"
            )
            scope_label = (
                f"All days ({len(self._days_scanned)} day(s))"
                if self._scope == "all"
                else as_of_label(self._target)
            )
            extras = []
            if self._value_filter is not None:
                extras.append(f"VALUE filter {len(self._value_filter)}")
            if self._search_text.strip():
                extras.append(f"search {self._search_text.strip()!r}")
            if self._checked_keys:
                extras.append(f"{len(self._checked_keys)} checked")
            extra_txt = ("  ·  " + "  ·  ".join(extras)) if extras else ""
            self._status.setText(
                f"{scope_label}  ·  "
                f"{sum(1 for i in self._issues_all if i.kind == 'Unmapped')} unmapped  ·  "
                f"{sum(1 for i in self._issues_all if i.kind == 'Bad map')} bad map"
                f"{extra_txt}"
                if self._issues_all else f"{scope_label}  ·  no issues"
            )
            self._map_btn.setEnabled(total > 0)
            self._map_all_btn.setEnabled(
                any(i.kind == "Unmapped" for i in self._issues_all)
            )

            # Drop drafts for descriptions no longer in the issue list.
            live_keys = {i.description_key for i in self._issues_all}
            for key in list(self._mapping_drafts.keys()):
                if key not in live_keys:
                    self._mapping_drafts.pop(key, None)

            for i, issue in enumerate(issues):
                self._table.insertRow(i)
                bg = _issues_stripe_bg(i)
                kind_bg = _AMBER_L if issue.kind == "Unmapped" else _RED_L
                draft = self._mapping_drafts.get(issue.description_key) or {}
                payee = (draft.get("payee") or issue.payee or "").strip()
                item_name = (draft.get("item") or issue.item or "").strip()

                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                check.setCheckState(
                    Qt.Checked
                    if issue.description_key in self._checked_keys
                    else Qt.Unchecked
                )
                check.setBackground(QBrush(QColor(bg)))
                self._table.setItem(i, _COL_CHECK, check)

                self._table.setItem(
                    i, _COL_NUM,
                    _cell(str(i + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=bg),
                )
                self._table.setItem(
                    i, _COL_KIND,
                    _cell(issue.kind, Qt.AlignCenter | Qt.AlignVCenter, bg=kind_bg),
                )
                self._table.setItem(i, _COL_DESC, _cell(issue.description, bg=bg))
                self._table.setItem(
                    i, _COL_ROWS,
                    _cell(str(issue.count), Qt.AlignCenter | Qt.AlignVCenter, bg=bg),
                )
                self._table.setItem(i, _COL_PAYEE, _editable_cell(payee, bg=bg))
                self._table.setItem(i, _COL_ITEM, _editable_cell(item_name, bg=bg))
                self._table.setItem(
                    i, _COL_DETAIL, _cell(issue.detail or "—", bg=bg)
                )
                _finish_table_row(self._table, i, bg)
                for col in (_COL_PAYEE, _COL_ITEM):
                    it = self._table.item(i, col)
                    if it is not None:
                        it.setFlags(
                            Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                        )
                chk = self._table.item(i, _COL_CHECK)
                if chk is not None:
                    chk.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                    )
                    chk.setCheckState(
                        Qt.Checked
                        if issue.description_key in self._checked_keys
                        else Qt.Unchecked
                    )

            self._footer.setText(
                "Check ☐ and/or highlight rows, then Map Selected — or edit "
                "Payee/Item inline and Save Filled."
                if issues else "All clear — this day can be submitted."
            )
            self._autofit_columns()
        finally:
            self._table.blockSignals(False)
            self._populating = False
        self._update_save_filled_enabled()

    def _autofit_columns(self) -> None:
        """Size content columns so Payee / Item / Detail are not ellipsized."""
        if self._table.rowCount() <= 0:
            return
        self._table._col_width_suspend = True  # type: ignore[attr-defined]
        try:
            for col in range(self._table.columnCount()):
                if col in _FIXED_COLS:
                    self._table.setColumnWidth(col, _COL_DEFAULTS[col])
                    continue
                self._table.resizeColumnToContents(col)
                width = self._table.columnWidth(col) + 16
                # Description filter chevron needs a little extra room.
                if col == _COL_DESC:
                    width += 12
                floor = _COL_DEFAULTS[col] if col < len(_COL_DEFAULTS) else 48
                self._table.setColumnWidth(col, min(max(width, floor), 520))
            saver = getattr(self._table, "_col_width_save", None)
            if callable(saver):
                saver()
        finally:
            self._table._col_width_suspend = False  # type: ignore[attr-defined]

    def _selected_issues(self) -> List[DayIssue]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [self._issues[r] for r in rows if 0 <= r < len(self._issues)]

    def _map_selected(self) -> None:
        selected_keys = {i.description_key for i in self._selected_issues()}
        targets = map_target_issues(
            self._issues,
            checked_keys=self._checked_keys,
            selected_keys=selected_keys,
        )
        if not targets:
            QMessageBox.information(
                self, "Map Selected",
                "Check ☐ rows and/or click-drag to highlight rows, then Map Selected.",
            )
            return
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._do_map([i.description for i in targets], targets))

    def _map_all_unmapped(self) -> None:
        unmapped = [i for i in self._issues_all if i.kind == "Unmapped"]
        if not unmapped:
            QMessageBox.information(self, "Map All", "No unmapped descriptions.")
            return
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._do_map([i.description for i in unmapped], unmapped))

    async def _save_filled_mappings(self) -> None:
        """Commit inline drafts that have both Payee and Item (skip incomplete)."""
        filled = self._filled_drafts()
        if not filled:
            await self._await_ui(lambda: QMessageBox.information(
                self,
                "Save Filled",
                "Fill both Payee and Item on one or more rows first.\n"
                "Incomplete drafts (only one field) are not saved.",
            ))
            return

        resolved: List[tuple] = []
        unknown_items: List[str] = []
        for desc, payee, item_name in filled:
            cat = next(
                (
                    c for c in self._categories
                    if c.name.strip().lower() == item_name.strip().lower()
                ),
                None,
            )
            if cat is None or cat._id is None:
                unknown_items.append(item_name)
                continue
            resolved.append((desc, payee, cat))

        if unknown_items:
            sample = ", ".join(sorted(set(unknown_items))[:6])
            await self._await_ui(lambda: QMessageBox.warning(
                self,
                "Unknown Item",
                f"These Item name(s) are not in the item list and were skipped:\n"
                f"{sample}\n\n"
                "Pick an existing item (or use Map Selected → New Item…).",
            ))
            if not resolved:
                return

        odd_payees = sorted({
            p for _d, p, _c in resolved
            if payee_needs_create(p, self._payees)
        })
        if odd_payees:
            tip = "\n".join(f"  · {p}" for p in odd_payees[:8])
            answer = await self._await_ui(lambda: QMessageBox.question(
                self,
                "Create payee(s)?",
                f"These payees are not in the Payee list:\n{tip}\n\n"
                "Create them and save the mappings?",
            ))
            if answer != QMessageBox.Yes:
                return

        self._loading_overlay.show_loading("Saving filled mappings…")
        for name in odd_payees:
            try:
                await add_payee(name)
            except Exception as exc:
                self._loading_overlay.hide_loading()
                msg = str(exc)
                await self._await_ui(
                    lambda: QMessageBox.warning(self, "Could Not Create Payee", msg)
                )
                return
            if payee_needs_create(name, self._payees):
                self._payees.append(name)

        descs = [desc for desc, _p, _c in resolved]
        live_n = 0
        committed = 0
        try:
            prior_by_key = await self._snapshot_prior_maps(descs)
            # Optimistic: stamp live Table/draft first, then one bulk map save.
            for desc, payee, cat in resolved:
                live_n += self._stamp_live_rows(
                    [desc], payee, cat.name, cat._id
                )
            from tahmeed.services.description_mapping_service import save_mappings_bulk

            saved = await save_mappings_bulk(
                [
                    (desc, cat._id, cat.name, payee)
                    for desc, payee, cat in resolved
                ]
            )
            self._last_mapping_undo = MappingUndoSnapshot(
                descriptions=list(descs),
                dates=self._dates_for_descriptions(descs),
                prior_by_key=prior_by_key,
                new_ids_by_key={
                    normalize_description(m.description): m._id
                    for m in saved
                    if m._id is not None and normalize_description(m.description)
                },
            )
            # Group by (payee, item, category_id) for background DB apply.
            groups: dict[tuple, list[str]] = {}
            for desc, payee, cat in resolved:
                key = (payee, cat.name, str(cat._id))
                groups.setdefault(key, []).append(desc)
                self._mapping_drafts.pop(normalize_description(desc), None)
                committed += 1
            for (payee, item_name, cat_id_str), descs_g in groups.items():
                self._enqueue_saved_row_apply(
                    descs_g, payee, item_name, ObjectId(cat_id_str)
                )
        except Exception as exc:
            self._last_mapping_undo = None
            self._loading_overlay.hide_loading()
            err = str(exc)
            await self._await_ui(
                lambda: QMessageBox.critical(self, "Save Filled Failed", err)
            )
            return
        finally:
            self._loading_overlay.hide_loading()

        msg = (
            f"Saved {committed:,} filled mapping(s) to Description Maps.\n"
            f"Updated {live_n:,} open row(s) on Table/draft"
            f"{' (DB rows updating in background).' if live_n or committed else '.'}"
        )
        await self._finish_mapping_ui(descs)
        if await self._await_ui(lambda: self._confirm_mapping_undo(msg)):
            await self._undo_last_mapping()

    async def _do_map(
        self,
        descriptions: List[str],
        source_issues: List[DayIssue],
    ) -> None:
        if self._map_in_flight:
            return
        self._map_in_flight = True
        try:
            await self._run_map(descriptions, source_issues)
        finally:
            self._map_in_flight = False

    async def _run_map(
        self,
        descriptions: List[str],
        source_issues: List[DayIssue],
    ) -> None:
        prefill_payee = ""
        prefill_item = ""
        payees = {i.payee for i in source_issues if i.payee}
        items = {i.item for i in source_issues if i.item}
        if len(payees) == 1:
            prefill_payee = next(iter(payees))
        if len(items) == 1:
            prefill_item = next(iter(items))

        # Lists are already on the page. Do not block the dialog on a catalog
        # download; a slow network made Map Selected feel dead.
        if not self._lookups_loaded or not self._categories or not self._payees:
            try:
                await self._reload_catalogs()
            except Exception:
                pass

        dlg = MapDescriptionsDialog(
            descriptions,
            self._categories,
            self._payees,
            parent=self,
            prefill_payee=prefill_payee,
            prefill_item=prefill_item,
        )
        if await self._await_ui(dlg.exec) != QDialog.Accepted:
            return
        assignment = dlg.result_assignment
        payee = dlg.result_payee
        if assignment is None or not payee:
            return
        self._loading_overlay.show_loading("Saving mappings…")
        try:
            category = await materialize_mapping_assignment(assignment, self._categories)
        except Exception as exc:
            self._loading_overlay.hide_loading()
            err = str(exc)
            await self._await_ui(
                lambda: QMessageBox.warning(self, "Could Not Save Item", err)
            )
            return
        if category is None or category._id is None:
            self._loading_overlay.hide_loading()
            await self._await_ui(lambda: QMessageBox.warning(
                self, "Select Item", "Please choose or create an item."
            ))
            return
        remember_category(self._categories, category)

        if dlg.result_create_payee or payee_needs_create(payee, self._payees):
            try:
                await add_payee(payee, currency=dlg.result_currency or "TZS")
            except Exception as exc:
                self._loading_overlay.hide_loading()
                err = str(exc)
                await self._await_ui(
                    lambda: QMessageBox.warning(self, "Could Not Create Payee", err)
                )
                return
            if payee_needs_create(payee, self._payees):
                self._payees.append(payee)

        try:
            prior_by_key = await self._snapshot_prior_maps(descriptions)
            # Optimistic: live stamp first, bulk map upsert, DB apply in outbox.
            live_n = self._stamp_live_rows(
                descriptions, payee, category.name, category._id
            )
            from tahmeed.services.description_mapping_service import save_mappings_bulk

            saved = await save_mappings_bulk(
                [
                    (desc, category._id, category.name, payee)
                    for desc in descriptions
                ]
            )
            self._last_mapping_undo = MappingUndoSnapshot(
                descriptions=list(descriptions),
                dates=self._dates_for_descriptions(descriptions),
                prior_by_key=prior_by_key,
                new_ids_by_key={
                    normalize_description(m.description): m._id
                    for m in saved
                    if m._id is not None and normalize_description(m.description)
                },
            )
            self._enqueue_saved_row_apply(
                descriptions, payee, category.name, category._id
            )
            for desc in descriptions:
                self._mapping_drafts.pop(normalize_description(desc), None)
        except Exception as exc:
            self._last_mapping_undo = None
            self._loading_overlay.hide_loading()
            err = str(exc)
            await self._await_ui(
                lambda: QMessageBox.critical(self, "Mapping Failed", err)
            )
            return
        finally:
            self._loading_overlay.hide_loading()

        apply_days = self._dates_for_descriptions(descriptions)
        scope_txt = (
            f"{len(apply_days)} day(s)"
            if self._scope == "all"
            else as_of_label(self._target)
        )
        detail = (
            f" ({live_n:,} on Table/draft including unsaved)."
            if live_n
            else "."
        )
        msg = (
            f"Mapped {len(descriptions):,} description(s) to:\n"
            f"Payee: {payee}\nItem: {category.name}\n\n"
            f"Saved to Description Maps.\n"
            f"Updated {live_n:,} open row(s) across {scope_txt}"
            f"{detail}\n"
            f"Saved register rows are updating in the background."
        )
        await self._finish_mapping_ui(descriptions)
        if await self._await_ui(lambda: self._confirm_mapping_undo(msg)):
            await self._undo_last_mapping()

    async def _finish_mapping_ui(self, descriptions: Sequence[str]) -> None:
        """Drop mapped rows and leave them off until the row update finishes.

        A quiet rescan here reads register rows that are still blank on a slow
        network and puts the description back as unmapped.
        """
        keys = {normalize_description(d) for d in descriptions if normalize_description(d)}
        self._drop_issue_keys(keys)
        self.mappings_applied.emit()

    async def _snapshot_prior_maps(self, descriptions: Sequence[str]) -> dict:
        """Capture existing Description Maps before an upsert (for Undo restore)."""
        from tahmeed.services.description_mapping_service import (
            get_mappings_for_descriptions,
        )

        existing = await get_mappings_for_descriptions(list(descriptions))
        prior: dict = {}
        for desc in descriptions:
            key = normalize_description(desc)
            if not key or key in prior:
                continue
            mapping = existing.get(key)
            if mapping is None:
                prior[key] = None
                continue
            prior[key] = (
                mapping.description or desc,
                mapping.category_id,
                mapping.category_name,
                mapping.payee or "",
            )
        return prior

    def _confirm_mapping_undo(self, message: str) -> bool:
        """Success dialog with Undo. Returns True when the user chooses Undo."""
        if self._last_mapping_undo is None:
            QMessageBox.information(self, "Mappings saved", message)
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Mappings saved")
        box.setText(message)
        box.setInformativeText(
            "Undo reverses Description Maps and clears Payee + Item on the "
            "matching Table / register rows from this action."
        )
        undo_btn = box.addButton("Undo", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        # Ok is default so Enter dismisses; Undo is an explicit choice.
        ok_btn = box.button(QMessageBox.Ok)
        if ok_btn is not None:
            box.setDefaultButton(ok_btn)
        box.exec()
        return box.clickedButton() is undo_btn

    async def _undo_last_mapping(self) -> None:
        """Reverse the last successful Map / Save Filled commit."""
        snap = self._last_mapping_undo
        if snap is None:
            return
        self._last_mapping_undo = None
        self._loading_overlay.show_loading("Undoing mappings…")
        try:
            from tahmeed.services.mapping_apply_outbox import (
                cancel_pending_jobs_for_keys,
                wait_outbox_idle,
            )

            keys = [
                normalize_description(d)
                for d in snap.descriptions
                if normalize_description(d)
            ]
            cancel_pending_jobs_for_keys(keys)
            await wait_outbox_idle()

            delete_ids, restore_entries = split_undo_map_actions(snap)
            from tahmeed.services.description_mapping_service import (
                delete_mappings_bulk,
                get_mappings_for_descriptions,
                save_mappings_bulk,
            )

            # Resolve new-map ids the bulk save did not return.
            need_lookup = [
                key
                for key, prior in snap.prior_by_key.items()
                if prior is None and key not in snap.new_ids_by_key
            ]
            if need_lookup:
                current = await get_mappings_for_descriptions(
                    [
                        d
                        for d in snap.descriptions
                        if normalize_description(d) in need_lookup
                    ]
                )
                for key in need_lookup:
                    mapping = current.get(key)
                    if mapping is not None and mapping._id is not None:
                        delete_ids.append(mapping._id)

            if delete_ids:
                await delete_mappings_bulk(delete_ids)
            if restore_entries:
                await save_mappings_bulk(restore_entries)

            self._clear_live_rows(snap.descriptions, days=snap.dates)
            await self._clear_saved_rows(snap.descriptions, days=snap.dates)
        except Exception as exc:
            self._loading_overlay.hide_loading()
            err = str(exc)
            await self._await_ui(lambda: QMessageBox.critical(self, "Undo Failed", err))
            return
        finally:
            self._loading_overlay.hide_loading()

        self.mark_dirty(days=snap.dates)
        self.mappings_applied.emit()
        await self.refresh(quiet=False)
        await self._await_ui(lambda: QMessageBox.information(
            self,
            "Mapping undone",
            f"Reverted {len(snap.descriptions):,} description(s).\n"
            "Description Maps and Table / register stamps from that action "
            "were reversed.",
        ))

    def _clear_live_rows(
        self,
        descriptions: Sequence[str],
        *,
        days: Optional[Sequence[date]] = None,
    ) -> int:
        """Clear Payee + Item on open Table/draft rows for Undo."""
        apply_days = list(days) if days else self._dates_for_descriptions(descriptions)
        live_n = 0
        if self._apply_live is None:
            return 0
        for day in apply_days:
            try:
                live_n += int(
                    self._apply_live(
                        day,
                        descriptions,
                        payee="",
                        item_name="",
                        category_id=None,
                        cashier_id=self._cashier_scope_id,
                        emit_source_changed=False,
                        clear_category=True,
                    )
                    or 0
                )
            except TypeError:
                try:
                    live_n += int(
                        self._apply_live(
                            day,
                            descriptions,
                            payee="",
                            item_name="",
                            category_id=None,
                            cashier_id=self._cashier_scope_id,
                            emit_source_changed=False,
                        )
                        or 0
                    )
                except Exception:
                    continue
            except Exception:
                continue
        return live_n

    async def _clear_saved_rows(
        self,
        descriptions: Sequence[str],
        *,
        days: Optional[Sequence[date]] = None,
    ) -> int:
        """Clear Payee + Item on matching open DB rows (Undo)."""
        from tahmeed.services.cashier_service import apply_description_mapping_bulk

        keys = [
            normalize_description(d)
            for d in descriptions
            if normalize_description(d)
        ]
        if not keys:
            return 0
        target_days = list(days) if days else [self._target]
        try:
            return await apply_description_mapping_bulk(
                keys,
                dates=target_days,
                cashier_id=self._cashier_scope_id,
                clear=True,
            )
        except Exception:
            return 0

    def _stamp_live_rows(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
    ) -> int:
        """Sync stamp onto open Table/draft rows (optimistic, no network)."""
        apply_days = self._dates_for_descriptions(descriptions)
        live_n = 0
        if self._apply_live is None:
            return 0
        for day in apply_days:
            try:
                live_n += int(
                    self._apply_live(
                        day,
                        descriptions,
                        payee=payee,
                        item_name=item_name,
                        category_id=category_id,
                        cashier_id=self._cashier_scope_id,
                        emit_source_changed=False,
                    )
                    or 0
                )
            except TypeError:
                try:
                    live_n += int(
                        self._apply_live(
                            day,
                            descriptions,
                            payee=payee,
                            item_name=item_name,
                            category_id=category_id,
                            cashier_id=self._cashier_scope_id,
                        )
                        or 0
                    )
                except Exception:
                    continue
            except Exception:
                continue
        return live_n

    def _enqueue_saved_row_apply(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
    ) -> None:
        """Enqueue one bulk DB stamp; drain runs in the background."""
        from tahmeed.services.mapping_apply_outbox import enqueue_mapping_apply

        keys = [
            normalize_description(d)
            for d in descriptions
            if normalize_description(d)
        ]
        if not keys:
            return
        enqueue_mapping_apply(
            description_keys=keys,
            payee=payee,
            item=item_name,
            category_id=category_id,
            dates=self._dates_for_descriptions(descriptions),
            cashier_id=self._cashier_scope_id,
        )

    async def _apply_mapping_everywhere(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
    ) -> tuple:
        """Legacy helper: live stamp + await one bulk DB apply (tests / callers)."""
        live_n = self._stamp_live_rows(
            descriptions, payee, item_name, category_id
        )
        saved_n = await self._apply_to_saved_rows(
            descriptions,
            payee,
            item_name,
            category_id,
            days=self._dates_for_descriptions(descriptions),
        )
        return saved_n, live_n

    def _dates_for_descriptions(self, descriptions: Sequence[str]) -> List[date]:
        keys = {normalize_description(d) for d in descriptions}
        days: set[date] = set()
        for key in keys:
            for d in self._issue_dates.get(key) or []:
                if isinstance(d, date):
                    days.add(d)
        if not days:
            days.add(self._target)
        return sorted(days)

    async def _apply_to_saved_rows(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
        *,
        days: Optional[Sequence[date]] = None,
    ) -> int:
        """One bulk ``update_many`` for matching open (unverified) rows."""
        from tahmeed.services.cashier_service import apply_description_mapping_bulk

        keys = [
            normalize_description(d)
            for d in descriptions
            if normalize_description(d)
        ]
        if not keys:
            return 0
        target_days = list(days) if days else [self._target]
        try:
            return await apply_description_mapping_bulk(
                keys,
                payee=payee,
                category_name=item_name,
                category_id=category_id,
                dates=target_days,
                cashier_id=self._cashier_scope_id,
            )
        except Exception:
            return 0
