"""Table model + view used by every tab of the bulk Import Daily page."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QRect,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QStyle,
    QStyleOptionTab,
    QStylePainter,
    QTabBar,
    QTableView,
    QTabWidget,
    QWidget,
)

from tahmeed.ui.widgets.combo_styles import with_combo_popup

SORT_ROLE = Qt.UserRole + 1

# Same palette as the cashier Day Issues / Verify tables. The OS may be in dark
# mode, so every colour (text included) is set explicitly.
WHITE = "#FFFFFF"
BORDER = "#E5E7EB"
BLUE = "#0077C5"
BLUE_L = "#E8F4FD"
T1 = "#111827"
T2 = "#6B7280"
ROW_STRIPE = "#E6EFFC"
HEADER_BG = "#F1F5F9"
SELECT_BG = "#93C5FD"
SELECT_FG = "#0F172A"

READY_BG = "#DCFCE7"
BLOCKED_BG = "#FEE2E2"
UNMAPPED_BG = "#FEF3C7"
PUSHED_BG = "#BBF7D0"
IGNORED_BG = "#F3F4F6"
LOCKED_FG = "#9CA3AF"


@dataclass
class GridRow:
    key: Hashable
    cells: Sequence[Any]
    background: Optional[str] = None
    tooltip: str = ""
    editable: Set[int] = field(default_factory=set)
    # Per-cell backgrounds (cell index -> colour), over ``background`` / stripes.
    cell_colors: Dict[int, str] = field(default_factory=dict)
    # False: no tick box and greyed text (e.g. a day already pushed).
    checkable: bool = True


def _checkmark_url() -> str:
    from tahmeed.ui.cashier.day_issues import _ensure_checkmark_path

    return _ensure_checkmark_path()


def checkbox_qss() -> str:
    """Blue tick boxes for QCheckBox, table rows and list rows."""
    url = _checkmark_url()
    box = f"width:14px;height:14px;border-radius:3px;border:1.5px solid #93C5FD;background:{WHITE};"
    checked = f'background:{BLUE_L};border:1.5px solid {BLUE};image:url("{url}");'
    return (
        f"QCheckBox{{color:{T1};font-family:'Segoe UI';font-size:12px;spacing:6px;"
        "background:transparent;}"
        f"QCheckBox::indicator,QTableView::indicator,QListView::indicator{{{box}}}"
        f"QCheckBox::indicator:hover{{border-color:{BLUE};}}"
        "QCheckBox::indicator:checked,QTableView::indicator:checked,"
        f"QListView::indicator:checked{{{checked}}}"
        f"QCheckBox:disabled{{color:#9CA3AF;}}"
    )


def light_theme_qss() -> str:
    """Light fields, tabs, lists and tick boxes for the page and its dialogs."""
    return with_combo_popup(
        f"QLabel{{color:{T1};}}"
        f"QLineEdit,QComboBox{{border:1px solid {BORDER};border-radius:5px;background:{WHITE};"
        f"color:{T1};font-family:'Segoe UI';font-size:12px;padding:3px 8px;min-height:20px;"
        f"selection-background-color:#DBEAFE;selection-color:{T1};}}"
        f"QLineEdit:focus,QComboBox:focus{{border-color:{BLUE};}}"
        "QComboBox::drop-down{border:none;width:20px;}"
        f"QListWidget,QListView{{background:{WHITE};color:{T1};border:1px solid {BORDER};"
        "font-family:'Segoe UI';font-size:12px;}"
        f"QListWidget::item:selected,QListView::item:selected{{background:{BLUE_L};color:{T1};}}"
        f"QRadioButton{{color:{T1};font-family:'Segoe UI';font-size:12px;background:transparent;}}"
        "QRadioButton::indicator{width:14px;height:14px;border-radius:8px;"
        f"border:1.5px solid #93C5FD;background:{WHITE};}}"
        "QRadioButton::indicator:checked{border:1.5px solid "
        f"{BLUE};background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,"
        f"stop:0 {BLUE},stop:0.45 {BLUE},stop:0.55 {WHITE},stop:1 {WHITE});}}"
        f"QTabWidget::pane{{border:1px solid {BORDER};border-radius:6px;background:{WHITE};top:-1px;}}"
        "QTabBar{font-family:'Segoe UI';font-size:12px;font-weight:600;}"
        "QTabBar::tab{background:transparent;"
        f"color:{T2};padding:6px 12px;font-family:'Segoe UI';font-size:12px;font-weight:600;"
        "border:none;margin-right:4px;}"
        f"QTabBar::tab:hover{{color:{T1};}}"
        f"QTabBar::tab:selected{{color:{BLUE};border-bottom:2px solid {BLUE};}}"
        + checkbox_qss()
    )


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


class GridModel(QAbstractTableModel):
    """Read-mostly rows with an optional tick-box first column.

    Numbers are shown with thousands separators and sorted numerically.
    ``edited`` fires ``(key, column, text)`` for cells listed in
    ``GridRow.editable``; ``checks_changed`` fires when tick boxes change.
    """

    edited = Signal(object, int, str)
    checks_changed = Signal()

    def __init__(
        self,
        headers: Sequence[str],
        *,
        checkable: bool = False,
        right_align: Sequence[int] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._checkable = checkable
        self._offset = 1 if checkable else 0
        self._headers = ([""] if checkable else []) + list(headers)
        self._right = {c + self._offset for c in right_align}
        self._rows: List[GridRow] = []
        self._checked: Set[Hashable] = set()

    # -- data --------------------------------------------------------------

    def set_headers(self, headers: Sequence[str], right_align: Sequence[int] = ()) -> None:
        self.beginResetModel()
        self._headers = ([""] if self._checkable else []) + list(headers)
        self._right = {c + self._offset for c in right_align}
        self.endResetModel()

    def set_rows(self, rows: List[GridRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        keys = {r.key for r in rows if r.checkable}
        self._checked &= keys
        self.endResetModel()

    def is_checkable(self, key: Hashable) -> bool:
        return any(r.key == key and r.checkable for r in self._rows)

    def update_row(
        self,
        key: Hashable,
        *,
        cells: Optional[Sequence[Any]] = None,
        cell_colors: Optional[Dict[int, str]] = None,
    ) -> None:
        """Change one row in place (keeps an open cell editor and the scroll)."""
        for r, row in enumerate(self._rows):
            if row.key != key:
                continue
            if cells is not None:
                row.cells = list(cells)
            if cell_colors is not None:
                row.cell_colors = dict(cell_colors)
            self.dataChanged.emit(self.index(r, 0), self.index(r, self.columnCount() - 1))
            return

    def row_at(self, row: int) -> Optional[GridRow]:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def keys(self) -> List[Hashable]:
        return [r.key for r in self._rows]

    def checked_keys(self) -> List[Hashable]:
        return [r.key for r in self._rows if r.key in self._checked]

    def set_checked(self, keys, checked: bool = True) -> None:
        keys = set(keys)
        if checked:
            keys &= {r.key for r in self._rows if r.checkable}
            self._checked |= keys
        else:
            self._checked -= keys
        if self._rows and self._checkable:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._rows) - 1, 0))
        self.checks_changed.emit()

    def clear_checks(self) -> None:
        self.set_checked(list(self._checked), False)

    # -- Qt model API ------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if self._checkable and section == 0:
                return "☐"
            return self._headers[section]
        return None

    def _value(self, row: GridRow, column: int) -> Any:
        index = column - self._offset
        return row.cells[index] if 0 <= index < len(row.cells) else ""

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.ForegroundRole:
            return QBrush(QColor(T1 if row.checkable else LOCKED_FG))
        if self._checkable and col == 0:
            if role == Qt.CheckStateRole:
                if not row.checkable:
                    return None
                return Qt.Checked if row.key in self._checked else Qt.Unchecked
            if role == SORT_ROLE:
                return 0 if row.key in self._checked else 1
            if role == Qt.BackgroundRole and row.background:
                return QBrush(QColor(row.background))
            return None
        value = self._value(row, col)
        if role in (Qt.DisplayRole, Qt.EditRole):
            return _display(value)
        if role == SORT_ROLE:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            if isinstance(value, date):
                return value.isoformat()
            return _display(value).upper()
        if role == Qt.BackgroundRole:
            color = row.cell_colors.get(col - self._offset) or row.background
            return QBrush(QColor(color)) if color else None
        if role == Qt.TextAlignmentRole and col in self._right:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return row.tooltip or _display(value) or None
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        row = self._rows[index.row()]
        if self._checkable and index.column() == 0:
            return base | Qt.ItemIsUserCheckable if row.checkable else base
        if index.column() - self._offset in row.editable:
            return base | Qt.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:  # noqa: N802
        if not index.isValid():
            return False
        row = self._rows[index.row()]
        if self._checkable and index.column() == 0 and role == Qt.CheckStateRole:
            if not row.checkable:
                return False
            state = Qt.CheckState(value) if not isinstance(value, Qt.CheckState) else value
            if state == Qt.Checked:
                self._checked.add(row.key)
            else:
                self._checked.discard(row.key)
            self.dataChanged.emit(index, index)
            self.checks_changed.emit()
            return True
        if role == Qt.EditRole and index.column() - self._offset in row.editable:
            self.edited.emit(row.key, index.column() - self._offset, str(value or ""))
            return True
        return False


class GridView(QTableView):
    """Sortable, filterable view over a :class:`GridModel`."""

    def __init__(self, model: GridModel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.grid_model = model
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(model)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        # Start in source order (days ascending, rows as in the Excel file).
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.proxy.sort(-1)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        header.sectionClicked.connect(self._on_header_clicked)
        self.setStyleSheet(
            f"QTableView{{background:{WHITE};alternate-background-color:{ROW_STRIPE};"
            f"color:{T1};border:1px solid {BORDER};gridline-color:{BORDER};"
            "font-family:'Segoe UI';font-size:12px;"
            f"selection-background-color:{SELECT_BG};selection-color:{SELECT_FG};}}"
            f"QTableView::item:selected{{background:{SELECT_BG};color:{SELECT_FG};}}"
            f"QTableView QLineEdit{{background:{WHITE};color:{T1};border:1px solid #A3A3A3;"
            f"border-radius:0px;padding:0 4px;selection-background-color:#DBEAFE;"
            f"selection-color:{T1};}}"
            f"QHeaderView{{background:{HEADER_BG};}}"
            f"QHeaderView::section{{background:{HEADER_BG};color:{T2};border:none;"
            f"border-right:1px solid {BORDER};border-bottom:1px solid {BORDER};"
            "padding:4px 6px;font-family:'Segoe UI';font-weight:600;font-size:11px;}"
            f"QTableCornerButton::section{{background:{HEADER_BG};border:none;}}"
            "QScrollBar:vertical{width:10px;background:transparent;}"
            "QScrollBar::handle:vertical{background:#CBD5E1;border-radius:4px;min-height:24px;}"
            "QScrollBar:horizontal{height:10px;background:transparent;}"
            "QScrollBar::handle:horizontal{background:#CBD5E1;border-radius:4px;min-width:24px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;}"
            + checkbox_qss()
        )

    def _on_header_clicked(self, section: int) -> None:
        if not self.grid_model._checkable or section != 0:
            return
        visible = [
            key
            for key in (self.source_key(r) for r in range(self.proxy.rowCount()))
            if self.grid_model.is_checkable(key)
        ]
        checked = set(self.grid_model.checked_keys())
        all_on = bool(visible) and all(k in checked for k in visible)
        self.grid_model.set_checked(visible, not all_on)

    def set_filter_text(self, text: str) -> None:
        self.proxy.setFilterFixedString(text or "")

    def source_key(self, proxy_row: int) -> Hashable:
        src = self.proxy.mapToSource(self.proxy.index(proxy_row, 0))
        row = self.grid_model.row_at(src.row())
        return row.key if row else None

    def selected_keys(self) -> List[Hashable]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        return [self.source_key(r) for r in rows]

    def current_key(self) -> Optional[Hashable]:
        index = self.currentIndex()
        return self.source_key(index.row()) if index.isValid() else None

    def acted_keys(self) -> List[Hashable]:
        """Ticked rows plus highlighted rows (ticked first, no duplicates)."""
        out = list(self.grid_model.checked_keys())
        seen = set(out)
        for key in self.selected_keys():
            if key not in seen:
                out.append(key)
                seen.add(key)
        return out

    def select_key(self, key: Hashable) -> None:
        for r in range(self.proxy.rowCount()):
            if self.source_key(r) == key:
                self.selectRow(r)
                self.scrollTo(self.proxy.index(r, 0))
                return

    def fit_columns(self, max_width: int = 320, min_widths: Optional[Dict[int, int]] = None) -> None:
        """Fit to contents, capped at ``max_width``; ``min_widths`` uses cell indexes."""
        self.resizeColumnsToContents()
        header = self.horizontalHeader()
        offset = 1 if self.grid_model._checkable else 0
        floors = {cell + offset: width for cell, width in (min_widths or {}).items()}
        for col in range(header.count()):
            size = header.sectionSize(col)
            if size > max_width:
                header.resizeSection(col, max_width)
            elif col in floors and size < floors[col]:
                header.resizeSection(col, floors[col])


class _BadgeTabBar(QTabBar):
    """Tab bar that draws a sidebar-style count bubble after a tab's title."""

    _GAP = 6
    _HEIGHT = 18

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._badges: Dict[int, Tuple[str, str]] = {}

    def badge(self, index: int) -> str:
        return self._badges.get(index, ("", ""))[0]

    def set_badge(self, index: int, text: str, color: str) -> None:
        if not text:
            changed = self._badges.pop(index, None) is not None
        else:
            changed = self._badges.get(index) != (text, color)
            self._badges[index] = (text, color)
        if changed:
            # Re-setting the title makes QTabBar ask for the new tab width.
            self.setTabText(index, self.tabText(index))
            self.update()

    def _badge_font(self) -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(10)
        font.setBold(True)
        return font

    def _title_width(self, index: int) -> int:
        return self.fontMetrics().horizontalAdvance(self.tabText(index))

    def _badge_width(self, text: str) -> int:
        return max(self._HEIGHT, QFontMetrics(self._badge_font()).horizontalAdvance(text) + 10)

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802
        size = super().tabSizeHint(index)
        text = self.badge(index)
        if text:
            size.setWidth(size.width() + self._badge_width(text) + self._GAP)
        return size

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QStylePainter(self)
        for index in range(self.count()):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, index)
            painter.drawControl(QStyle.CE_TabBarTabShape, opt)
            text, color = self._badges.get(index, ("", ""))
            if not text:
                painter.drawControl(QStyle.CE_TabBarTabLabel, opt)
                continue
            # The stylesheet style always centres the label in the whole tab, so the
            # title is drawn here to leave room for the bubble.
            width = self._badge_width(text)
            title_width = self._title_width(index)
            full = QRect(opt.rect)
            left = full.center().x() - (title_width + self._GAP + width) // 2
            if opt.state & QStyle.State_Selected:
                title_color = BLUE
            elif opt.state & QStyle.State_MouseOver:
                title_color = T1
            else:
                title_color = T2
            painter.save()
            painter.setPen(QColor(title_color))
            painter.setFont(self.font())
            painter.drawText(
                QRect(left, full.top(), title_width + 1, full.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                self.tabText(index),
            )
            bubble = QRect(
                left + title_width + self._GAP,
                full.center().y() - self._HEIGHT // 2,
                width,
                self._HEIGHT,
            )
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(bubble, self._HEIGHT / 2, self._HEIGHT / 2)
            painter.setPen(QColor(WHITE))
            painter.setFont(self._badge_font())
            painter.drawText(bubble, Qt.AlignCenter, text)
            painter.restore()


class BadgeTabWidget(QTabWidget):
    """QTabWidget whose tabs can show a red count bubble, like the sidebar."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setTabBar(_BadgeTabBar(self))

    def set_badge(self, index: int, count: int, color: str = "#DC2626") -> None:
        text = "" if count <= 0 else (f"{count:,}" if count < 1000 else "999+")
        self.tabBar().set_badge(index, text, color)

    def badge(self, index: int) -> str:
        return self.tabBar().badge(index)


def next_entry_cell(row: int, col: int, move: str, columns: Sequence[int]) -> Tuple[int, int]:
    """Cell to open after Tab ("next"), Shift+Tab ("prev") or Enter ("down")."""
    if move == "down":
        return row + 1, col
    if move == "prev":
        before = [c for c in columns if c < col]
        return (row, before[-1]) if before else (row - 1, columns[-1])
    after = [c for c in columns if c > col]
    return (row, after[0]) if after else (row + 1, columns[0])


class InlineEntry(QObject):
    """Type straight into a grid's entry cells, like the cashier Day Issues table.

    One click (or typing) on an entry cell opens a box with a contains-search
    list; Tab / Shift+Tab move across the entry cells and on to the next row,
    Enter moves down. ``pause()`` holds the next box while a prompt is open.
    """

    def __init__(
        self,
        view: GridView,
        suggestions: Dict[int, Callable[[], List[str]]],
        *,
        uppercase: Sequence[int] = (),
        matchers: Optional[Dict[int, Callable[[str, List[str]], List[str]]]] = None,
    ) -> None:
        super().__init__(view)
        matchers = matchers or {}
        from tahmeed.ui.cashier.day_issues import _IssuesCompleterDelegate, _IssuesRowDelegate

        self._view = view
        offset = 1 if view.grid_model._checkable else 0
        self.columns = sorted(c + offset for c in suggestions)
        self._paused = False
        self._pending: Optional[Tuple[int, int]] = None
        view.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        view.setItemDelegate(_IssuesRowDelegate(view))
        for cell, getter in suggestions.items():
            view.setItemDelegateForColumn(
                cell + offset,
                _IssuesCompleterDelegate(
                    getter,
                    view,
                    uppercase=cell in uppercase,
                    navigate=self.advance,
                    matcher=matchers.get(cell),
                ),
            )
        view.clicked.connect(self._on_clicked)
        view.installEventFilter(self)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        target, self._pending = self._pending, None
        if target is not None:
            QTimer.singleShot(0, lambda: self.open(*target))

    def advance(self, row: int, col: int, move: str) -> None:
        target = next_entry_cell(row, col, move, self.columns)
        if self._paused:
            self._pending = target
            return
        self.open(*target)

    def open(self, row: int, col: int) -> None:
        model = self._view.model()
        if not 0 <= row < model.rowCount():
            return
        index = model.index(row, col)
        self._view.setCurrentIndex(index)
        self._view.scrollTo(index)
        self._view.edit(index)

    def _on_clicked(self, index) -> None:
        if not index.isValid() or index.column() not in self.columns:
            return
        if QApplication.keyboardModifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return
        self._view.edit(index)

    def eventFilter(self, obj, event) -> bool:
        # With no box open, Tab would walk every column; keep it on the entry cells.
        if (
            obj is self._view
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)
            and self._view.state() != QAbstractItemView.EditingState
        ):
            if self._view.model().rowCount():
                back = event.key() == Qt.Key_Backtab or bool(event.modifiers() & Qt.ShiftModifier)
                cur = self._view.currentIndex()
                if cur.isValid():
                    self.advance(cur.row(), cur.column(), "prev" if back else "next")
                else:
                    self.open(0, self.columns[0])
            return True
        return super().eventFilter(obj, event)


def connect_rows(view: GridView, on_current: Callable[[Any], None]) -> None:
    view.selectionModel().currentRowChanged.connect(
        lambda current, _prev: on_current(view.source_key(current.row()) if current.isValid() else None)
    )
