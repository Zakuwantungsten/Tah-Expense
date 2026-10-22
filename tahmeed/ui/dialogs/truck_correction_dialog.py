"""Dialog to correct fleet numbers after paste or failed validation.

All flagged trucks appear in one combined list. As each row is fixed,
accepted as a place label (YARD/GARAGE), or cleared, it disappears from
the list. When the list is empty the dialog closes automatically.

Applying a correction can also fix every other open row with the same
pasted value (e.g. many ``T760 DN`` → ``T760 HDN``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Dict, List, Literal, Optional, Set

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.truck_format import (
    is_allowed_place_label,
    is_place_label_candidate,
    normalize_place_label,
    normalize_truck_number,
    try_match_fleet,
)
from tahmeed.ui.dialog_theme import (
    show_info,
    show_question,
    show_warning,
    style_message_box,
)
from tahmeed.ui.dialogs.confirm_dialog import ask_confirm
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit

IssueKind = Literal["invalid_format", "not_in_registry"]
FleetKind = Literal["truck", "trailer", "motor_vehicle"]

# Compact Fluent-style controls (Carbon/Fluent: dense, one primary action)
_CTRL_H = 28
_TRUCK_EDIT_W = 148  # plate-sized; do not stretch across the row
_BADGE_W = 96
_BORDER = "#E5E7EB"
_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BLUE_L = "#E8F4FD"
_ORANGE = "#E85D04"
_GREEN = "#16A34A"
_RED = "#B91C1C"
# Master Expenses–style zebra stripes (manual, not Qt alternate palette)
_ROW_EVEN = "#FFFFFF"
_ROW_ODD = "#F1F5F9"
_ROW_H = 24
_HDR_H = 22

_INPUT_SS = (
    f"QLineEdit {{ background: {_WHITE}; border: 1px solid #d1d5db; border-radius: 4px;"
    f" padding: 0 8px; font-size: 12px; color: {_T1};"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QLineEdit:focus {{ border-color: {_BLUE}; }}"
)
_BTN_SECONDARY = (
    f"QPushButton {{ background: {_WHITE}; color: #374151; border: 1px solid #d1d5db;"
    f" border-radius: 4px; padding: 0 10px; font-size: 11px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QPushButton:hover {{ background: {_BG}; }}"
)
_BTN_ORANGE = (
    f"QPushButton {{ background: {_WHITE}; color: {_ORANGE}; border: 1px solid {_ORANGE};"
    f" border-radius: 4px; padding: 0 10px; font-size: 11px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    "QPushButton:hover { background: #FFF7ED; }"
)
_BTN_BLUE = (
    f"QPushButton {{ background: {_WHITE}; color: {_BLUE}; border: 1px solid {_BLUE};"
    f" border-radius: 4px; padding: 0 10px; font-size: 11px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QPushButton:hover {{ background: #E8F4FD; }}"
)
_BTN_PRIMARY = (
    f"QPushButton {{ background: {_BLUE}; color: #fff; border: none;"
    f" border-radius: 4px; padding: 0 12px; font-size: 11px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    "QPushButton:hover { background: #005EA3; }"
)
_BTN_GHOST = (
    f"QPushButton {{ background: transparent; color: {_T2}; border: none;"
    f" border-radius: 4px; padding: 0 8px; font-size: 11px; font-weight: 600;"
    f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px; }}"
    f"QPushButton:hover {{ background: {_BG}; color: {_T1}; }}"
)

_KIND_LABELS = {
    "trucks": "Truck",
    "trailers": "Trailer",
    "motor_vehicles": "Motorcycle/Car",
}

# Prefer these keys first when peeping import / feed row payloads.
_DETAIL_PRIORITY = (
    "date", "txn_date", "transaction_date", "entry_date",
    "description", "details", "narration", "memo",
    "item", "category", "payee",
    "truck_number", "truck_no", "truck", "vehicle_reg", "vehicle_no",
    "plate_num", "reg_no", "truck_reg", "truck_and_trailer",
    "amount", "tzs", "usd", "tender_amount",
    "transaction_type", "type", "direction", "gate_in",
    "ref", "reference", "cheque", "ownership",
)

_SKIP_DETAIL_KEYS = frozenset({
    "_raw", "_id", "id", "fleet_override", "upload_id", "target_upload_id",
    "source_filename", "sheet_label", "is_deposit", "record",
})

_DETAIL_KEY_LABELS = {
    "truck_number": "Truck",
    "truck_no": "Truck",
    "truck": "Truck",
    "vehicle_reg": "Vehicle",
    "vehicle_no": "Vehicle",
    "plate_num": "Plate",
    "reg_no": "Reg. no",
    "truck_reg": "Truck reg",
    "truck_and_trailer": "Truck & trailer",
    "txn_date": "Date",
    "transaction_date": "Date",
    "entry_date": "Date",
    "tender_amount": "Amount",
    "transaction_type": "Type",
    "gate_in": "Gate in",
}


def _norm_key(value: str) -> str:
    """Compare pasted originals case/space-insensitively."""
    return " ".join((value or "").upper().split())


def _pretty_detail_key(key: str) -> str:
    if key in _DETAIL_KEY_LABELS:
        return _DETAIL_KEY_LABELS[key]
    text = str(key or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else key


def _format_detail_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return f"{value:g}"
    if isinstance(value, (list, dict, set, tuple)):
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return ""
    return text


def snapshot_row_details(
    row: Optional[dict],
    *,
    max_fields: int = 28,
) -> Dict[str, str]:
    """Sanitize a grid/import row into compact display key→value pairs."""
    if not row:
        return {}
    out: Dict[str, str] = {}
    for key in _DETAIL_PRIORITY:
        if key not in row:
            continue
        text = _format_detail_value(row.get(key))
        if not text:
            continue
        label = _pretty_detail_key(key)
        if label not in out:
            out[label] = text
        if len(out) >= max_fields:
            return out
    for key, raw in row.items():
        if key in _SKIP_DETAIL_KEYS or str(key).startswith("_"):
            continue
        text = _format_detail_value(raw)
        if not text:
            continue
        label = _pretty_detail_key(str(key))
        if label in out:
            continue
        out[label] = text
        if len(out) >= max_fields:
            break
    return out


@dataclass
class TruckIssue:
    row: int                 # 0-based grid / import row
    original: str
    kind: IssueKind
    corrected: str = ""      # filled when resolved
    skip: bool = False       # clear the cell (cashier) or empty
    is_place_label: bool = False
    omit_row: bool = False   # import mode: park row for later (Skipped feed tab)
    parked: bool = False     # register: park row on the Parked table tab
    allow_anyway: bool = False  # import mode: save even if not in fleet
    accept_empty: bool = False  # parked fix: blank truck is the resolution
    row_label: str = ""      # optional UI title (e.g. "File row 2442")
    combo_parts: List[str] = field(default_factory=list)  # unused when combo_suffix is set
    combo_suffix: str = ""  # ``/T691ELK`` kept while the user edits the truck only
    # Display-only snapshot for the Row details tab (Phase 3 / 4).
    row_details: Dict[str, str] = field(default_factory=dict)


@dataclass
class _RowWidgets:
    issue: TruckIssue
    card: QFrame
    edit: QLineEdit
    kind_label: QLabel
    status_badge: QLabel
    add_btn: Optional[QPushButton] = None
    accept_label_btn: Optional[QPushButton] = None
    allow_btn: Optional[QPushButton] = None
    no_truck_btn: Optional[QPushButton] = None
    skip_row_btn: Optional[QPushButton] = None
    details_btn: Optional[QPushButton] = None
    part_edits: List[QLineEdit] = field(default_factory=list)
    part_badges: List[QLabel] = field(default_factory=list)


class TruckCorrectionDialog(QDialog):
    """One combined list of truck issues; resolved rows disappear immediately."""

    def __init__(
        self,
        issues: List[TruckIssue],
        fleet: set[str],
        *,
        can_add: bool = False,
        allowed_labels: Optional[Set[str]] = None,
        on_resolved=None,
        import_mode: bool = False,
        fleet_kinds: Optional[Dict[str, str]] = None,
        heading: str = "",
        intro: str = "",
        allow_park: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(heading or "Correct truck numbers")
        self.setMinimumWidth(760)
        self.setMinimumHeight(420)
        self.resize(820, 520)
        self.setModal(True)
        self._fleet = set(fleet)
        self._fleet_kinds: Dict[str, str] = dict(fleet_kinds or {})
        # ``can_add`` kept for call-site compatibility; Add is always offered.
        _ = can_add
        self._can_add = True
        self._import_mode = import_mode
        # False when fixing rows already on the Parked tab (no re-park actions).
        self._allow_park = bool(allow_park)
        self._heading = heading or "Correct truck numbers"
        self._intro = intro
        self._on_resolved = on_resolved  # optional callback(TruckIssue) per resolved row
        self._allowed_labels: Set[str] = {
            normalize_place_label(x) for x in (allowed_labels or set()) if x
        }
        self.new_labels: List[str] = []
        # (kind, number) queued for the caller to persist after the dialog closes —
        # avoids nested asyncio during import modals.
        self.pending_registry_adds: List[tuple[str, str]] = []
        self.issues: List[TruckIssue] = []  # resolved results (updated as rows clear)
        self._rows: List[_RowWidgets] = []
        self._held_issues: List[TruckIssue] = []
        self._defer_page = False
        self._issue_page = 100
        self._body_lay: Optional[QVBoxLayout] = None
        self._issue_table: Optional[QTableWidget] = None
        self._table_sync = False
        self._edit_notify = False
        self._apply_btn: Optional[QPushButton] = None
        self._allow_btn: Optional[QPushButton] = None
        self._no_truck_btn: Optional[QPushButton] = None
        self._count_label: Optional[QLabel] = None
        self._tabs: Optional[QTabWidget] = None
        self._details_stack: Optional[QStackedWidget] = None
        self._details_focus_label: Optional[QLabel] = None
        self._details_form_host: Optional[QWidget] = None
        self._details_form: Optional[QFormLayout] = None
        self._details_empty: Optional[QLabel] = None
        self._details_table: Optional[QTableWidget] = None
        self._details_selected_btn: Optional[QPushButton] = None
        self._details_all_btn: Optional[QPushButton] = None
        self._details_focus_row: Optional[int] = None  # issue.row currently peeped
        self._details_mode: str = "all"  # "selected" | "all"
        self._busy_overlay = None  # LoadingOverlay — horse during Park/Allow all
        self._bulk_busy = False
        self._build_ui()
        self.add_issues(issues)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            f"QLabel {{ border: none; background: transparent; color: {_T1}; }}"
            "QFrame { border: none; }"
            "QToolTip {"
            f" background-color: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 4px;"
            " padding: 4px 8px; font-size: 11px; }"
            "QScrollBar:vertical {"
            f" background: transparent; width: 8px; margin: 0; }}"
            "QScrollBar::handle:vertical {"
            f" background: {_BORDER}; border-radius: 4px; min-height: 24px; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            " height: 0; }"
            "QTabWidget::pane {"
            f" border: 1px solid {_BORDER}; border-radius: 6px;"
            f" background: {_BG}; top: -1px; padding: 8px; }}"
            "QTabBar::tab {"
            f" background: transparent; color: {_T2};"
            " padding: 6px 12px; font-size: 11px; font-weight: 600;"
            " border: none; margin-right: 4px; }"
            f"QTabBar::tab:selected {{ color: {_BLUE};"
            f" border-bottom: 2px solid {_BLUE}; }}"
            f"QTabBar::tab:hover {{ color: {_T1}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel(self._heading)
        title.setStyleSheet(
            f"color: {_T1}; font-size: 14px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        header.addWidget(title, 0, Qt.AlignVCenter)
        header.addStretch(1)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {_T2}; font-size: 11px; font-weight: 600;"
            " border: none; background: transparent;"
        )
        header.addWidget(self._count_label, 0, Qt.AlignVCenter)
        root.addLayout(header)

        if self._intro:
            intro_text = self._intro
        elif self._import_mode:
            intro_text = (
                "Fix, add to registry, allow, or park for follow-up. Use Details "
                "to peep the full row. Similar pasted values can be fixed together."
            )
        else:
            intro_text = (
                "Fix, add to registry, allow a partner truck, or park for the "
                "Parked tab. Use Details to peep the full row."
            )
        intro = QLabel(intro_text)
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color: {_T2}; font-size: 11px; border: none; background: transparent;"
        )
        root.addWidget(intro)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._build_incorrect_tab(), "Incorrect trucks")
        self._tabs.addTab(self._build_details_tab(), "Row details")
        root.addWidget(self._tabs, 1)

        # Fixed footer (Carbon): bulk actions left, primary Done right
        footer_bar = QFrame()
        footer_bar.setObjectName("truckFooter")
        footer_bar.setStyleSheet(
            f"QFrame#truckFooter {{ background: {_WHITE}; border: 1px solid {_BORDER};"
            " border-radius: 6px; }"
        )
        footer = QHBoxLayout(footer_bar)
        footer.setContentsMargins(10, 8, 10, 8)
        footer.setSpacing(6)
        if self._allow_park:
            park_all = QPushButton("Park all")
            park_all.setCursor(Qt.PointingHandCursor)
            park_all.setFixedHeight(_CTRL_H)
            park_all.setStyleSheet(_BTN_GHOST)
            park_all.setToolTip(
                "Park every remaining row for follow-up "
                + (
                    "(Skipped feed tab)"
                    if self._import_mode
                    else "(Parked table tab — nothing is saved; correct rows stay on My entries)"
                )
            )
            park_all.clicked.connect(self._park_all_remaining)
            footer.addWidget(park_all)
        allow_all = QPushButton("Allow all")
        allow_all.setCursor(Qt.PointingHandCursor)
        allow_all.setFixedHeight(_CTRL_H)
        allow_all.setStyleSheet(_BTN_ORANGE)
        allow_all.setToolTip("Allow every remaining row even if not in the fleet")
        allow_all.clicked.connect(self._allow_all_remaining)
        footer.addWidget(allow_all)
        footer.addStretch()
        cancel_btn = QPushButton("Park remaining" if self._allow_park else "Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFixedHeight(_CTRL_H)
        cancel_btn.setStyleSheet(_BTN_SECONDARY)
        if self._allow_park:
            cancel_btn.setToolTip(
                "Park every still-open row for follow-up and close"
            )
            cancel_btn.clicked.connect(self._on_cancel)
        else:
            cancel_btn.setToolTip("Close without changing remaining trucks")
            cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)
        done_btn = QPushButton("Done")
        done_btn.setDefault(True)
        done_btn.setCursor(Qt.PointingHandCursor)
        done_btn.setFixedHeight(_CTRL_H)
        done_btn.setStyleSheet(_BTN_PRIMARY)
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)
        root.addWidget(footer_bar)

    def _build_incorrect_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {_BG}; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {_BG}; }}"
        )
        host = QWidget()
        host.setObjectName("truckIssueHost")
        host.setStyleSheet(f"QWidget#truckIssueHost {{ background: {_BG}; }}")
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addStretch(1)
        self._body_lay = body
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        return wrap

    def exec(self) -> int:  # type: ignore[override]
        """Pause the connection light while this dialog is open.

        Park all and Allow all pump Qt events. A probe started in that window
        expires as soon as the screen is busy and looks like a lost API.
        """
        from tahmeed.ui.async_utils import pause_background_polls

        with pause_background_polls(self):
            return super().exec()

    def _build_details_tab(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._details_selected_btn = QPushButton("Selected")
        self._details_selected_btn.setCursor(Qt.PointingHandCursor)
        self._details_selected_btn.setFixedHeight(_CTRL_H)
        self._details_selected_btn.setToolTip(
            "Show details for one peeped row (use Details on a truck card)"
        )
        self._details_selected_btn.clicked.connect(
            lambda: self._set_details_mode("selected")
        )
        toolbar.addWidget(self._details_selected_btn)

        self._details_all_btn = QPushButton("See all")
        self._details_all_btn.setCursor(Qt.PointingHandCursor)
        self._details_all_btn.setFixedHeight(_CTRL_H)
        self._details_all_btn.setToolTip("Show every incorrect truck row in a table")
        self._details_all_btn.clicked.connect(lambda: self._set_details_mode("all"))
        toolbar.addWidget(self._details_all_btn)

        toolbar.addStretch(1)
        self._details_focus_label = QLabel("")
        self._details_focus_label.setStyleSheet(
            f"color: {_T2}; font-size: 11px; border: none; background: transparent;"
        )
        toolbar.addWidget(self._details_focus_label)
        lay.addLayout(toolbar)

        self._details_stack = QStackedWidget()

        # Selected peep view
        selected = QWidget()
        selected_lay = QVBoxLayout(selected)
        selected_lay.setContentsMargins(0, 0, 0, 0)
        selected_lay.setSpacing(6)
        self._details_empty = QLabel(
            "Click Details on an incorrect truck to peep that row here."
        )
        self._details_empty.setWordWrap(True)
        self._details_empty.setStyleSheet(
            f"color: {_T2}; font-size: 11px; border: none; background: transparent;"
        )
        selected_lay.addWidget(self._details_empty)

        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QScrollArea.NoFrame)
        form_scroll.setStyleSheet(
            "QScrollArea{border:none; background:transparent;}"
        )
        self._details_form_host = QWidget()
        self._details_form_host.setStyleSheet(
            f"background: {_WHITE}; border: 1px solid {_BORDER}; border-radius: 6px;"
        )
        self._details_form = QFormLayout(self._details_form_host)
        self._details_form.setContentsMargins(12, 10, 12, 10)
        self._details_form.setHorizontalSpacing(16)
        self._details_form.setVerticalSpacing(6)
        self._details_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form_scroll.setWidget(self._details_form_host)
        selected_lay.addWidget(form_scroll, 1)
        self._details_stack.addWidget(selected)

        # See-all table — Master Expenses zebra stripes (white / slate-100)
        self._details_table = QTableWidget(0, 4)
        self._details_table.setHorizontalHeaderLabels(
            ("Row", "Truck", "Issue", "Summary")
        )
        self._details_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._details_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._details_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._details_table.setAlternatingRowColors(False)
        self._details_table.verticalHeader().setVisible(False)
        self._details_table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._details_table.setShowGrid(False)
        self._details_table.setStyleSheet(
            f"QTableWidget {{ background: {_WHITE}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; gridline-color: {_BORDER};"
            f" font-size: 10pt; font-family: 'Segoe UI'; }}"
            f"QHeaderView::section {{ background: {_ROW_ODD}; color: {_T2};"
            f" border: none; border-bottom: 1px solid {_BORDER};"
            f" padding: 0 6px; height: {_HDR_H}px;"
            " font-size: 9px; font-weight: 600; font-family: 'Segoe UI'; }}"
            f"QTableWidget::item {{ padding: 1px 6px; color: {_T1}; }}"
            f"QTableWidget::item:selected {{ background: {_BLUE_L}; color: {_T1}; }}"
        )
        hh = self._details_table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self._details_table.cellDoubleClicked.connect(self._on_details_table_activate)
        self._details_stack.addWidget(self._details_table)

        lay.addWidget(self._details_stack, 1)
        self._set_details_mode("all")
        return wrap

    def _update_count(self) -> None:
        n = len(self._rows)
        extra = len(self._held_issues)
        if self._count_label is not None:
            if extra:
                self._count_label.setText(f"{n} open · {extra:,} remain")
            else:
                self._count_label.setText(
                    f"{n} remaining" if n else "All resolved"
                )
        self._refresh_details_tab()

    def _set_details_mode(self, mode: str) -> None:
        self._details_mode = "selected" if mode == "selected" else "all"
        if self._details_stack is not None:
            self._details_stack.setCurrentIndex(
                0 if self._details_mode == "selected" else 1
            )
        for btn, active in (
            (self._details_selected_btn, self._details_mode == "selected"),
            (self._details_all_btn, self._details_mode == "all"),
        ):
            if btn is None:
                continue
            btn.setStyleSheet(_BTN_BLUE if active else _BTN_GHOST)
        self._refresh_details_tab()

    def _peep_row(self, rw: _RowWidgets) -> None:
        """Switch to Row details focused on this incorrect truck."""
        self._details_focus_row = rw.issue.row
        if self._tabs is not None:
            self._tabs.setCurrentIndex(1)
        self._set_details_mode("selected")

    def _on_details_table_activate(self, table_row: int, _col: int = 0) -> None:
        if self._details_table is None:
            return
        item = self._details_table.item(table_row, 0)
        if item is None:
            return
        issue_row = item.data(Qt.UserRole)
        if issue_row is None:
            return
        self._details_focus_row = int(issue_row)
        self._set_details_mode("selected")

    def _issue_title(self, issue: TruckIssue) -> str:
        return issue.row_label or f"Row {issue.row + 1}"

    def _issue_kind_text(self, issue: TruckIssue) -> str:
        if issue.kind == "invalid_format":
            return "Invalid format"
        return "Not in registry"

    def _details_pairs_for(self, issue: TruckIssue) -> List[tuple[str, str]]:
        pairs: List[tuple[str, str]] = [
            ("Row", self._issue_title(issue)),
            ("Pasted truck", issue.original or "—"),
            ("Issue", self._issue_kind_text(issue)),
        ]
        for key, val in (issue.row_details or {}).items():
            text = _format_detail_value(val)
            if not text:
                continue
            if key in {"Row", "Pasted truck", "Issue", "Truck"}:
                continue
            pairs.append((key, text))
        return pairs

    def _summary_for(self, issue: TruckIssue) -> str:
        details = issue.row_details or {}
        for key in ("Description", "Details", "Item", "Payee", "Amount", "TZS", "USD", "Date"):
            val = details.get(key)
            if val:
                return str(val)
        # Fall back to first non-meta detail
        for key, val in details.items():
            if key not in {"Truck", "Truck no.", "Truck No."}:
                return str(val)
        return "—"

    def _clear_details_form(self) -> None:
        if self._details_form is None:
            return
        while self._details_form.rowCount():
            self._details_form.removeRow(0)

    def _refresh_details_tab(self) -> None:
        if self._details_stack is None:
            return
        open_by_row = {rw.issue.row: rw.issue for rw in self._rows}

        # Drop focus if that row was resolved
        if (
            self._details_focus_row is not None
            and self._details_focus_row not in open_by_row
        ):
            self._details_focus_row = None

        focus = (
            open_by_row.get(self._details_focus_row)
            if self._details_focus_row is not None
            else None
        )
        if self._details_focus_label is not None:
            if focus is not None:
                self._details_focus_label.setText(
                    f'{self._issue_title(focus)} · "{focus.original}"'
                )
            elif self._details_mode == "all":
                self._details_focus_label.setText(
                    f"{len(self._rows)} incorrect truck(s)"
                )
            else:
                self._details_focus_label.setText("No row selected")

        # Selected form
        self._clear_details_form()
        show_form = focus is not None and self._details_mode == "selected"
        if self._details_empty is not None:
            self._details_empty.setVisible(not show_form)
        if self._details_form_host is not None:
            self._details_form_host.setVisible(show_form)
        if show_form and self._details_form is not None and focus is not None:
            for label, value in self._details_pairs_for(focus):
                key_lbl = QLabel(label)
                key_lbl.setStyleSheet(
                    f"color: {_T2}; font-size: 11px; font-weight: 600;"
                )
                val_lbl = QLabel(value)
                val_lbl.setWordWrap(True)
                val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val_lbl.setStyleSheet(f"color: {_T1}; font-size: 12px;")
                self._details_form.addRow(key_lbl, val_lbl)

        # See-all table
        if self._details_table is None:
            return
        rows = [rw.issue for rw in self._rows]
        self._details_table.setRowCount(len(rows))
        for i, issue in enumerate(rows):
            row_it = QTableWidgetItem(self._issue_title(issue))
            row_it.setData(Qt.UserRole, issue.row)
            truck_it = QTableWidgetItem(issue.original or "—")
            kind_it = QTableWidgetItem(self._issue_kind_text(issue))
            summary_it = QTableWidgetItem(self._summary_for(issue))
            stripe = QColor(_ROW_ODD if i % 2 else _ROW_EVEN)
            for it in (row_it, truck_it, kind_it, summary_it):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                it.setBackground(stripe)
                it.setForeground(QColor(_T1))
            self._details_table.setItem(i, 0, row_it)
            self._details_table.setItem(i, 1, truck_it)
            self._details_table.setItem(i, 2, kind_it)
            self._details_table.setItem(i, 3, summary_it)
            self._details_table.setRowHeight(i, _ROW_H)
            if focus is not None and issue.row == focus.row:
                self._details_table.selectRow(i)

    def _fleet_suggestions(self) -> List[str]:
        """Sorted registered numbers for inline autocomplete (same UX as table)."""
        return sorted(self._fleet)

    def _combo_part_labels(self, count: int) -> List[str]:
        if count <= 1:
            return ["Truck"]
        if count == 2:
            return ["Truck", "Trailer"]
        return ["Truck"] + [f"Trailer {i}" for i in range(1, count)]

    def _row_value(self, rw: _RowWidgets) -> str:
        """Merged truck/trailer value, or the single edit box."""
        if rw.part_edits:
            parts: List[str] = []
            for edit in rw.part_edits:
                text = edit.text().strip()
                if not text:
                    continue
                norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
                if norm.status in ("ok", "normalized", "place_label"):
                    parts.append(norm.value)
                else:
                    parts.append(" ".join(text.upper().split()))
            return "/".join(parts)
        return rw.edit.text().strip()

    def _truck_edit_value(self, rw: _RowWidgets) -> str:
        """Current truck field, falling back to the leading plate of the pasted cell."""
        return self._row_value(rw) or self._edit_source_for_issue(rw.issue)

    def _edit_source_for_issue(self, issue: TruckIssue) -> str:
        """Single-field text: leading truck when the trailer suffix is kept aside."""
        raw = issue.original or ""
        suffix = issue.combo_suffix or ""
        if suffix and raw.endswith(suffix):
            return raw[: -len(suffix)].strip()
        if suffix:
            cut = raw.find(suffix)
            if cut > 0:
                return raw[:cut].strip()
        return raw

    def _fill_truck_edit(self, edit: QLineEdit, source: str) -> None:
        norm = normalize_truck_number(source, allowed_labels=self._allowed_labels)
        if norm.status in ("ok", "normalized", "place_label"):
            edit.setText(norm.value)
        else:
            edit.setText(norm.value or str(source).upper())

    def _with_combo_suffix(
        self,
        issue: TruckIssue,
        truck: str,
        *,
        is_place_label: bool = False,
    ) -> str:
        truck = (truck or "").strip()
        if is_place_label or not issue.combo_suffix:
            return truck
        return f"{truck}{issue.combo_suffix}"

    def _is_two_trailer_issue(self, issue: TruckIssue) -> bool:
        """True when the pasted cell has truck + two (or more) trailers."""
        if len(issue.combo_parts or []) >= 3:
            return True
        if (issue.combo_suffix or "").count("/") >= 2:
            return True
        try:
            from tahmeed.services.import_truck_check import split_truck_combo_cell
            parts = split_truck_combo_cell(issue.original or "")
        except Exception:
            return False
        return bool(parts) and len(parts) >= 3

    def _refresh_fleet_completers(self) -> None:
        numbers = self._fleet_suggestions
        for rw in self._rows:
            edits = rw.part_edits or ([rw.edit] if isinstance(rw.edit, TruckLineEdit) else [])
            for edit in edits:
                if isinstance(edit, TruckLineEdit):
                    edit.set_local_numbers(numbers)

    def _lookup_kind(self, number: str) -> Optional[str]:
        if not number:
            return None
        key = number.strip().upper()
        kind = self._fleet_kinds.get(key)
        if kind:
            return kind
        matched = try_match_fleet(key, self._fleet)
        if matched:
            return self._fleet_kinds.get(matched)
        # Fall back to sync cache if kinds were not passed in
        try:
            from tahmeed.services.truck_service import lookup_fleet_kind_sync
            return lookup_fleet_kind_sync(matched or key)
        except Exception:
            return None

    def _status_for_text(self, text: str) -> tuple[str, str]:
        """Return (label, color) for the registry status badge."""
        raw = (text or "").strip()
        if not raw:
            return ("—", _T2)
        if is_allowed_place_label(raw, self._allowed_labels):
            return ("Place label", _BLUE)
        if is_place_label_candidate(raw):
            return ("Place label?", _BLUE)
        norm = normalize_truck_number(raw, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            return ("Place label", _BLUE)
        if norm.status == "empty":
            return ("—", _T2)
        if norm.status == "invalid":
            # Free-form motorcycle/car plates can still be in the fleet.
            matched_free = try_match_fleet(raw, self._fleet)
            if matched_free is not None:
                kind = self._lookup_kind(matched_free)
                if kind == "motor_vehicle":
                    return ("Bike/Car ✓", _GREEN)
                if kind == "trailer":
                    return ("Trailer ✓", _GREEN)
                if kind == "truck":
                    return ("Truck ✓", _GREEN)
                return ("In registry ✓", _GREEN)
            return ("Invalid format", "#B45309")
        matched = try_match_fleet(norm.value, self._fleet)
        if matched is None:
            return ("Not in registry", _RED)
        kind = self._lookup_kind(matched)
        if kind == "trailer":
            return ("Trailer ✓", _GREEN)
        if kind == "truck":
            return ("Truck ✓", _GREEN)
        if kind == "motor_vehicle":
            return ("Bike/Car ✓", _GREEN)
        return ("In registry ✓", _GREEN)

    # ── Similar-row helpers ───────────────────────────────────────────────

    def _similar_open_rows(self, rw: _RowWidgets) -> List[_RowWidgets]:
        key = _norm_key(self._edit_source_for_issue(rw.issue))
        if not key:
            return []
        return [
            other for other in self._rows
            if other is not rw and _norm_key(self._edit_source_for_issue(other.issue)) == key
        ]

    def _confirm_apply_similar(
        self,
        rw: _RowWidgets,
        corrected: str,
        *,
        action: str = "apply",
    ) -> List[_RowWidgets]:
        """Ask whether to fix other open rows with the same pasted value."""
        similar = self._similar_open_rows(rw)
        if not similar:
            return []
        original = rw.issue.original
        verb = {
            "apply": "Apply",
            "allow": "Allow",
            "add": "Add / apply",
        }.get(action, "Apply")
        reply = show_question(
            self,
            "Similar trucks",
            f'{verb} "{corrected}" to this row and {len(similar)} other '
            f'row(s) with the same pasted value "{original}"?\n\n'
            "Choose No to fix only this row.",
            default_no=False,
        )
        return similar if reply == QMessageBox.Yes else []

    def _resolve_issue(
        self,
        issue: TruckIssue,
        *,
        corrected: str,
        skip: bool = False,
        omit_row: bool = False,
        allow_anyway: bool = False,
        is_place_label: bool = False,
    ) -> TruckIssue:
        issue.corrected = corrected
        issue.skip = skip
        issue.omit_row = omit_row
        issue.allow_anyway = allow_anyway
        issue.is_place_label = is_place_label
        issue.accept_empty = False
        # Apply / Allow / Clear never park — only ``_park_row`` sets this.
        issue.parked = False
        return issue

    def _fixing_parked_trucks(self) -> bool:
        """Parked-tab Fix trucks: blank truck can be accepted and moved to My."""
        return not self._allow_park and not self._import_mode

    def _sync_no_truck_button(self, rw: _RowWidgets) -> None:
        btn = rw.no_truck_btn
        if btn is None:
            return
        if rw.part_edits:
            blank = not any(edit.text().strip() for edit in rw.part_edits)
        else:
            blank = not (rw.edit.text() or "").strip()
        btn.setVisible(blank)

    # ── Add / remove rows ─────────────────────────────────────────────────

    def add_issues(self, issues: List[TruckIssue]) -> None:
        """Append issues (dedupe by grid row — latest wins).

        Only one page of cards is built. The rest wait in ``_held_issues``
        so a few hundred rows do not freeze the window.
        """
        if not issues or self._body_lay is None:
            return
        existing = {rw.issue.row: rw for rw in self._rows}
        fresh: List[TruckIssue] = []
        for issue in issues:
            if issue.row in existing:
                rw = existing[issue.row]
                rw.issue.original = issue.original
                rw.issue.kind = issue.kind
                if issue.combo_suffix:
                    rw.issue.combo_suffix = issue.combo_suffix
                if issue.combo_parts:
                    rw.issue.combo_parts = list(issue.combo_parts)
                if issue.row_details:
                    rw.issue.row_details = dict(issue.row_details)
                if issue.row_label:
                    rw.issue.row_label = issue.row_label
                if rw.part_edits and rw.issue.combo_parts:
                    for edit, part in zip(rw.part_edits, rw.issue.combo_parts):
                        self._fill_truck_edit(edit, part)
                else:
                    self._fill_truck_edit(rw.edit, self._edit_source_for_issue(issue))
                pasted = rw.card.findChild(QLabel, "pastedLbl")
                if pasted is not None:
                    pasted.setText(f'· "{issue.original}"')
                self._refresh_kind_label(rw)
                self._refresh_status_badge(rw)
                self._sync_no_truck_button(rw)
                continue
            fresh.append(issue)
        room = max(0, self._issue_page - len(self._rows))
        for issue in fresh[:room]:
            card = self._make_card(issue)
            self._body_lay.insertWidget(self._body_lay.count() - 1, card)
        if len(fresh) > room:
            self._held_issues.extend(fresh[room:])
        self._update_count()

    def _make_card(self, issue: TruckIssue) -> QFrame:
        """One card: pasted value, registry status, and the row actions."""
        card = QFrame()
        card.setObjectName("truckIssueCard")
        card.setFrameShape(QFrame.NoFrame)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        stripe = _ROW_EVEN if len(self._rows) % 2 == 0 else _ROW_ODD
        card.setStyleSheet(
            f"QFrame#truckIssueCard {{"
            f" background-color: {stripe}; border: none;"
            f" border-bottom: 1px solid {_BORDER}; }}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel(issue.row_label or f"Row {issue.row + 1}")
        title.setStyleSheet(
            f"font-weight: 700; color: {_T1}; font-size: 11px;"
            " border: none; background: transparent;"
        )
        header.addWidget(title)

        pasted = QLabel(f'· "{issue.original}"')
        pasted.setObjectName("pastedLbl")
        pasted.setStyleSheet(
            f"color: {_T2}; font-size: 11px; border: none; background: transparent;"
        )
        header.addWidget(pasted)

        kind_label = QLabel("")
        kind_label.setWordWrap(False)
        header.addWidget(kind_label, 1)

        details_btn = QPushButton("Details")
        details_btn.setToolTip("Peep full row details in the Row details tab")
        details_btn.setCursor(Qt.PointingHandCursor)
        details_btn.setFixedHeight(_CTRL_H)
        details_btn.setStyleSheet(_BTN_GHOST)
        header.addWidget(details_btn)
        lay.addLayout(header)

        combo = [] if issue.combo_suffix else list(issue.combo_parts or [])
        part_edits: List[QLineEdit] = []
        part_badges: List[QLabel] = []
        add_btn = None
        accept_btn = None
        rw_holder: Optional[_RowWidgets] = None

        apply_btn = QPushButton("Apply")
        apply_btn.setToolTip(
            "Apply this value (and optionally all similar pasted values) if valid"
        )
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setFixedHeight(_CTRL_H)
        apply_btn.setStyleSheet(_BTN_PRIMARY)

        allow_btn = QPushButton("Allow")
        allow_btn.setToolTip(
            "Keep this truck on the row even though it is not in your fleet "
            "(e.g. a partner vehicle)"
        )
        allow_btn.setCursor(Qt.PointingHandCursor)
        allow_btn.setFixedHeight(_CTRL_H)
        allow_btn.setStyleSheet(_BTN_ORANGE)

        no_truck_btn = None
        if self._fixing_parked_trucks():
            no_truck_btn = QPushButton("No truck")
            no_truck_btn.setToolTip(
                "This row has no truck. Mark it fixed and move it to My entries."
            )
            no_truck_btn.setCursor(Qt.PointingHandCursor)
            no_truck_btn.setFixedHeight(_CTRL_H)
            no_truck_btn.setStyleSheet(_BTN_SECONDARY)
            no_truck_btn.setVisible(False)

        skip_row_btn = None
        if self._allow_park:
            skip_row_btn = QPushButton("Park")
            skip_row_btn.setToolTip(
                "Park this row for follow-up "
                + (
                    "— other rows still import (Skipped feed tab)"
                    if self._import_mode
                    else "— moves to the Parked table tab"
                )
            )
            skip_row_btn.setCursor(Qt.PointingHandCursor)
            skip_row_btn.setFixedHeight(_CTRL_H)
            skip_row_btn.setStyleSheet(_BTN_GHOST)

        def _add_row_actions(row: QHBoxLayout) -> None:
            if no_truck_btn is not None:
                row.addWidget(no_truck_btn)
            row.addWidget(apply_btn)
            row.addWidget(allow_btn)
            if skip_row_btn is not None:
                row.addWidget(skip_row_btn)

        if len(combo) >= 2:
            labels = self._combo_part_labels(len(combo))
            for idx, part in enumerate(combo):
                prow = QHBoxLayout()
                prow.setSpacing(6)
                name = QLabel(labels[idx])
                name.setFixedWidth(56)
                name.setStyleSheet(
                    f"color: {_T1}; font-size: 11px; font-weight: 600;"
                    " border: none; background: transparent;"
                )
                prow.addWidget(name)
                edit = TruckLineEdit(local_numbers=self._fleet_suggestions)
                edit.setFixedHeight(_CTRL_H)
                edit.setMinimumWidth(100)
                edit.setMaximumWidth(_TRUCK_EDIT_W)
                edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self._fill_truck_edit(edit, part)
                edit.setPlaceholderText("T688 EAF")
                edit.setStyleSheet(_INPUT_SS)
                edit.editingFinished.connect(lambda e=edit: self._autonorm_edit(e))
                prow.addWidget(edit)
                badge = QLabel("—")
                badge.setFixedHeight(_CTRL_H)
                badge.setMinimumWidth(_BADGE_W)
                prow.addWidget(badge)
                part_add = QPushButton("Add")
                part_add.setToolTip(
                    f"Add this {labels[idx].lower()} to the fleet registry"
                )
                part_add.setCursor(Qt.PointingHandCursor)
                part_add.setFixedHeight(_CTRL_H)
                part_add.setStyleSheet(_BTN_BLUE)
                prow.addWidget(part_add)
                part_add.clicked.connect(
                    lambda _=False, i=idx: self._add_combo_part(rw_holder, i)
                )
                if idx == 0:
                    _add_row_actions(prow)
                    prow.addStretch()
                lay.addLayout(prow)
                part_edits.append(edit)
                part_badges.append(badge)
            primary_edit = part_edits[0]
            status_badge = part_badges[0]
        else:
            edit_row = QHBoxLayout()
            edit_row.setSpacing(6)
            edit = TruckLineEdit(local_numbers=self._fleet_suggestions)
            edit.setFixedHeight(_CTRL_H)
            edit.setMinimumWidth(100)
            edit.setMaximumWidth(_TRUCK_EDIT_W)
            edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self._fill_truck_edit(edit, self._edit_source_for_issue(issue))
            edit.setPlaceholderText("T688 EAF or YARD")
            edit.setStyleSheet(_INPUT_SS)
            edit.editingFinished.connect(lambda e=edit: self._autonorm_edit(e))
            edit_row.addWidget(edit)
            status_badge = QLabel("—")
            status_badge.setFixedHeight(_CTRL_H)
            status_badge.setMinimumWidth(_BADGE_W)
            status_badge.setToolTip(
                "Shows whether the entered number is a Truck, Trailer, or "
                "Motorcycle/Car in your registry"
            )
            edit_row.addWidget(status_badge)
            add_btn = QPushButton("Add to registry")
            add_btn.setToolTip("Add this number to the fleet registry")
            add_btn.setCursor(Qt.PointingHandCursor)
            add_btn.setFixedHeight(_CTRL_H)
            add_btn.setStyleSheet(_BTN_BLUE)
            edit_row.addWidget(add_btn)
            accept_btn = QPushButton("Label")
            accept_btn.setToolTip(
                "Accept as place label (YARD / GARAGE) and remove from list"
            )
            accept_btn.setCursor(Qt.PointingHandCursor)
            accept_btn.setFixedHeight(_CTRL_H)
            accept_btn.setStyleSheet(_BTN_BLUE)
            edit_row.addWidget(accept_btn)
            _add_row_actions(edit_row)
            edit_row.addStretch()
            lay.addLayout(edit_row)
            primary_edit = edit

        rw = _RowWidgets(
            issue=issue,
            card=card,
            edit=primary_edit,
            kind_label=kind_label,
            status_badge=status_badge,
            add_btn=add_btn,
            accept_label_btn=accept_btn,
            allow_btn=allow_btn,
            no_truck_btn=no_truck_btn,
            skip_row_btn=skip_row_btn,
            details_btn=details_btn,
            part_edits=part_edits,
            part_badges=part_badges,
        )
        rw_holder = rw
        self._rows.append(rw)
        self._refresh_kind_label(rw)
        self._refresh_status_badge(rw)
        if accept_btn is not None:
            accept_btn.setVisible(
                is_place_label_candidate(primary_edit.text())
                or is_allowed_place_label(primary_edit.text(), self._allowed_labels)
            )
        self._sync_no_truck_button(rw)
        for edit in part_edits or [primary_edit]:
            edit.textChanged.connect(lambda _t, r=rw: self._on_edit_changed(r))
        apply_btn.clicked.connect(lambda _=False, r=rw: self._apply_row(r))
        if accept_btn is not None:
            accept_btn.clicked.connect(lambda _=False, r=rw: self._accept_label_row(r))
        allow_btn.clicked.connect(lambda _=False, r=rw: self._allow_anyway_row(r))
        if no_truck_btn is not None:
            no_truck_btn.clicked.connect(lambda _=False, r=rw: self._accept_empty_row(r))
        if skip_row_btn is not None:
            skip_row_btn.clicked.connect(lambda _=False, r=rw: self._park_row(r))
        if add_btn is not None:
            add_btn.clicked.connect(lambda _=False, r=rw: self._add_to_registry_row(r))
        details_btn.clicked.connect(lambda _=False, r=rw: self._peep_row(r))
        return card

    def _refresh_kind_label(self, rw: _RowWidgets) -> None:
        if rw.part_edits:
            labels = self._combo_part_labels(len(rw.part_edits))
            bad: List[str] = []
            for i, edit in enumerate(rw.part_edits):
                status, _ = self._status_for_text(edit.text())
                if "✓" not in status and status not in ("Place label", "Place label?"):
                    bad.append(labels[i])
            if not bad:
                text, color = "Ready to apply", _GREEN
            elif len(bad) == 1:
                text, color = f"{bad[0]} needs attention", "#b91c1c"
            else:
                text, color = "Truck and trailer need attention", "#b91c1c"
        elif self._is_two_trailer_issue(rw.issue):
            status, _ = self._status_for_text(rw.edit.text())
            if "✓" in status or status in ("Place label", "Place label?"):
                text, color = "Two trailers — Allow or Park", "#b45309"
            else:
                text, color = "Two trailers — truck needs attention", "#b91c1c"
        elif self._fixing_parked_trucks() and not (rw.edit.text() or "").strip() and not rw.part_edits:
            text = "No truck on this row"
            color = "#b45309"
        elif rw.issue.kind == "invalid_format":
            text = "Invalid format — use T + digits + space + suffix (e.g. T688 EAF)"
            color = "#b45309"
        else:
            text = "Not in fleet registry"
            color = "#b91c1c"
        rw.kind_label.setText(text)
        rw.kind_label.setStyleSheet(
            f"color: {color}; font-size: 10px; border: none; background: transparent;"
        )

    def _badge_ss(self, color: str) -> str:
        return (
            f"QLabel {{ color: {color}; background: {_BG}; border: 1px solid {_BORDER};"
            f" border-radius: 4px; padding: 0 6px; font-size: 10px; font-weight: 700;"
            f" min-height: {_CTRL_H}px; max-height: {_CTRL_H}px;"
            f" min-width: {_BADGE_W}px; }}"
        )

    def _refresh_status_badge(self, rw: _RowWidgets) -> None:
        if rw.part_edits and rw.part_badges:
            for edit, badge in zip(rw.part_edits, rw.part_badges):
                label, color = self._status_for_text(edit.text())
                badge.setText(label)
                badge.setStyleSheet(self._badge_ss(color))
                badge.setAlignment(Qt.AlignCenter)
            return
        label, color = self._status_for_text(rw.edit.text())
        rw.status_badge.setText(label)
        rw.status_badge.setStyleSheet(self._badge_ss(color))
        rw.status_badge.setAlignment(Qt.AlignCenter)

    def _on_edit_changed(self, rw: _RowWidgets) -> None:
        if rw.accept_label_btn is not None and not rw.part_edits:
            text = rw.edit.text().strip()
            rw.accept_label_btn.setVisible(
                is_place_label_candidate(text)
                or is_allowed_place_label(text, self._allowed_labels)
            )
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        self._sync_no_truck_button(rw)

    def _autonorm_edit(self, edit: QLineEdit) -> None:
        text = edit.text().strip()
        if not text:
            return
        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        if norm.status in ("ok", "normalized", "place_label") and norm.value != text:
            edit.blockSignals(True)
            edit.setText(norm.value)
            edit.blockSignals(False)

    def _part_match_value(self, text: str) -> tuple[Optional[str], str]:
        """Canonical plate for one combo field, plus ``ok`` / ``place`` / error kind."""
        raw = (text or "").strip()
        if not raw:
            return None, "empty"
        if is_allowed_place_label(raw, self._allowed_labels):
            return normalize_place_label(raw), "place"
        norm = normalize_truck_number(raw, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            return norm.value, "place"
        if norm.status in ("ok", "normalized"):
            matched = try_match_fleet(norm.value, self._fleet)
            if matched is not None:
                return matched, "ok"
            return None, "missing"
        matched = try_match_fleet(raw, self._fleet)
        if matched is not None:
            return matched, "ok"
        if norm.status == "invalid":
            return None, "invalid"
        return None, "missing"

    def _combo_resolved_parts(
        self, rw: _RowWidgets
    ) -> tuple[Optional[List[str]], Optional[str]]:
        """Return canonical truck/trailer parts, or ``(None, warning)``."""
        labels = self._combo_part_labels(len(rw.part_edits))
        values: List[str] = []
        for i, edit in enumerate(rw.part_edits):
            value, kind = self._part_match_value(edit.text())
            name = labels[i]
            raw = edit.text().strip()
            if kind == "empty":
                return None, f"Enter a {name.lower()} number."
            if kind == "invalid":
                return None, (
                    f'"{raw}" is not a recognized {name.lower()} format.\n\n'
                    "Use T + digits + suffix (e.g. T688 EAF), or Add / Allow anyway."
                )
            if kind == "missing":
                return None, (
                    f'"{raw}" is not in the fleet registry.\n\n'
                    "Use “Add” next to that field, “Allow anyway”, or “Skip row”."
                )
            assert value is not None
            values.append(value)
            edit.setText(value)
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        return values, None

    def _add_combo_part(self, rw: Optional[_RowWidgets], idx: int) -> None:
        """Add one truck or trailer from a split combo card to the registry."""
        if rw is None or idx < 0 or idx >= len(rw.part_edits):
            return
        edit = rw.part_edits[idx]
        number = self._commit_registry_add(edit.text().strip())
        if not number:
            return
        edit.setText(number)
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        parts, err = self._combo_resolved_parts(rw)
        if err is not None:
            return
        merged = "/".join(parts or [])
        also = self._confirm_apply_similar(rw, merged, action="add")
        self._finish_resolved(rw, corrected=merged, also=also)

    # ── Resolve one row → remove from list ────────────────────────────────

    def _remove_row(self, rw: _RowWidgets, resolved: TruckIssue) -> None:
        self.issues.append(resolved)
        index = self._rows.index(rw) if rw in self._rows else -1
        if index >= 0:
            self._rows.pop(index)
        rw.card.setParent(None)
        rw.card.deleteLater()
        self._update_count()
        if callable(self._on_resolved):
            try:
                self._on_resolved(resolved)
            except Exception:
                pass
        if self._rows or self._defer_page:
            return
        if self._show_next_issue_page():
            return
        self.accept()

    def _show_next_issue_page(self) -> bool:
        """Build the next page of cards. At most ``_issue_page`` cards exist."""
        if self._rows or not self._held_issues or self._body_lay is None:
            return False
        page = self._held_issues[: self._issue_page]
        del self._held_issues[: self._issue_page]
        for issue in page:
            card = self._make_card(issue)
            self._body_lay.insertWidget(self._body_lay.count() - 1, card)
        self._update_count()
        return True

    def _park_held_issues(self) -> None:
        """Resolve issues that were never given a card, using the same park flags."""
        for issue in list(self._held_issues):
            issue.corrected = (issue.corrected or issue.original or "").strip()
            issue.skip = False
            issue.allow_anyway = False
            issue.is_place_label = False
            issue.accept_empty = False
            if self._import_mode:
                issue.omit_row = True
                issue.parked = False
            else:
                issue.omit_row = False
                issue.parked = True
            self.issues.append(issue)
            self._notify_resolved(issue)
        self._held_issues.clear()

    def _finish_resolved(
        self,
        rw: _RowWidgets,
        *,
        corrected: str,
        skip: bool = False,
        omit_row: bool = False,
        allow_anyway: bool = False,
        is_place_label: bool = False,
        also: Optional[List[_RowWidgets]] = None,
    ) -> None:
        """Resolve ``rw`` and optionally the similar rows in ``also``."""
        self._remove_row(
            rw,
            self._resolve_issue(
                rw.issue,
                corrected=self._with_combo_suffix(
                    rw.issue, corrected, is_place_label=is_place_label
                ),
                skip=skip,
                omit_row=omit_row,
                allow_anyway=allow_anyway,
                is_place_label=is_place_label,
            ),
        )
        for other in list(also or []):
            if other not in self._rows:
                continue
            self._remove_row(
                other,
                self._resolve_issue(
                    other.issue,
                    corrected=self._with_combo_suffix(
                        other.issue, corrected, is_place_label=is_place_label
                    ),
                    skip=skip,
                    omit_row=omit_row,
                    allow_anyway=allow_anyway,
                    is_place_label=is_place_label,
                ),
            )

    def _clear_row(self, rw: _RowWidgets) -> None:
        issue = rw.issue
        issue.corrected = ""
        issue.skip = True
        issue.omit_row = False
        issue.parked = False
        issue.allow_anyway = False
        issue.is_place_label = False
        issue.accept_empty = False
        self._remove_row(rw, issue)

    def _accept_empty_row(self, rw: _RowWidgets) -> None:
        """Parked fix: a blank truck is the resolution and moves the row to My."""
        issue = rw.issue
        issue.corrected = ""
        issue.skip = False
        issue.omit_row = False
        issue.parked = False
        issue.allow_anyway = False
        issue.is_place_label = False
        issue.accept_empty = True
        self._remove_row(rw, issue)

    def _omit_row(self, rw: _RowWidgets) -> None:
        """Import mode: park this row for the Skipped feed tab; do not import it now."""
        issue = rw.issue
        text = self._truck_edit_value(rw)
        issue.corrected = self._with_combo_suffix(issue, text or issue.original)
        issue.skip = False
        issue.omit_row = True
        issue.parked = False
        issue.allow_anyway = False
        issue.is_place_label = False
        issue.accept_empty = False
        self._remove_row(rw, issue)

    def _park_row(self, rw: _RowWidgets) -> None:
        """Park for follow-up — import Skipped tab, or register Parked tab."""
        if self._import_mode:
            self._omit_row(rw)
            return
        issue = rw.issue
        text = self._truck_edit_value(rw)
        issue.corrected = self._with_combo_suffix(issue, text or issue.original)
        issue.skip = False
        issue.omit_row = False
        issue.parked = True
        issue.allow_anyway = False
        issue.is_place_label = False
        issue.accept_empty = False
        self._remove_row(rw, issue)

    def _allow_anyway_row(self, rw: _RowWidgets, *, ask_similar: bool = True) -> None:
        """Accept truck even when not in fleet / odd format.

        Re-checks trucks + trailers first. Registry matches resolve as a normal
        apply (no override). Non-fleet / two-trailer overrides always confirm
        when ``ask_similar`` is True (single-row Allow). Bulk Allow all confirms
        once up front, then calls with ``ask_similar=False``.
        """
        text = self._truck_edit_value(rw)
        if not text:
            if not ask_similar and self._fixing_parked_trucks():
                self._accept_empty_row(rw)
                return
            if not ask_similar:
                return
            show_warning(self, "Allow", "Enter a truck value first.")
            return

        if rw.part_edits:
            unmatched = [
                edit.text().strip()
                for edit in rw.part_edits
                if edit.text().strip()
                and try_match_fleet(edit.text().strip(), self._fleet) is None
            ]
            if unmatched and ask_similar:
                if not self._confirm_allow(
                    title="Allow truck",
                    heading="Allow this truck/trailer row anyway?",
                    body=(
                        f"{len(unmatched)} part(s) are not in the fleet registry."
                    ),
                    note="The value will be kept on this row as a partner / non-fleet truck.",
                ):
                    return
            also = (
                self._confirm_apply_similar(rw, text, action="allow")
                if ask_similar
                else []
            )
            self._finish_resolved(
                rw,
                corrected=text,
                allow_anyway=bool(unmatched),
                also=also,
            )
            return

        text = rw.edit.text().strip() or rw.issue.original

        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        is_place = False
        if norm.status in ("ok", "normalized"):
            value = norm.value
        elif norm.status == "place_label":
            value = norm.value
            is_place = True
        else:
            value = " ".join(text.upper().split())

        rw.edit.setText(value)
        self._refresh_status_badge(rw)

        # Place labels are not fleet vehicles — allow without registry check
        if is_place or is_allowed_place_label(value, self._allowed_labels):
            also = (
                self._confirm_apply_similar(rw, value, action="allow")
                if ask_similar
                else []
            )
            self._finish_resolved(
                rw,
                corrected=normalize_place_label(value),
                is_place_label=True,
                also=also,
            )
            return

        matched = try_match_fleet(value, self._fleet)
        if matched is not None and not self._is_two_trailer_issue(rw.issue):
            rw.edit.setText(matched)
            self._refresh_status_badge(rw)
            also = (
                self._confirm_apply_similar(rw, matched, action="apply")
                if ask_similar
                else []
            )
            # In registry — treat as a normal apply, not an override
            self._finish_resolved(rw, corrected=matched, also=also)
            return

        if matched is not None:
            rw.edit.setText(matched)
            value = matched
            self._refresh_status_badge(rw)

        # Not in fleet registry, or two-trailer irregularity — confirm first
        if ask_similar:
            if self._is_two_trailer_issue(rw.issue):
                ok = self._confirm_allow(
                    title="Two trailers",
                    heading="Allow this two-trailer row anyway?",
                    body=f'Pasted value:\n"{rw.issue.original}"',
                    note=(
                        "It will be kept for this import."
                        if self._import_mode
                        else "It will be kept on this row."
                    ),
                )
            else:
                ok = self._confirm_allow(
                    title="Allow truck",
                    heading=f'Allow "{value}" anyway?',
                    body=(
                        "This number is not in your fleet registry "
                        "(trucks, trailers, or motorcycles & cars)."
                    ),
                    note=(
                        "It will be kept as a partner / non-fleet truck "
                        + ("for this import." if self._import_mode else "on this row.")
                    ),
                )
            if not ok:
                rw.edit.setFocus()
                return

        also = (
            self._confirm_apply_similar(rw, value, action="allow")
            if ask_similar
            else []
        )
        self._finish_resolved(
            rw,
            corrected=value,
            allow_anyway=True,
            also=also,
        )

    def _skip_all_remaining(self) -> None:
        self._park_all_remaining()

    def _ensure_busy_overlay(self):
        from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

        if self._busy_overlay is None:
            self._busy_overlay = LoadingOverlay(self, "Working…")
        return self._busy_overlay

    def _pump_busy(self) -> None:
        """Let the horse overlay paint during bulk Park/Allow all."""
        overlay = self._busy_overlay
        if overlay is not None and overlay.isVisible():
            try:
                overlay.raise_()
                panel = getattr(overlay, "_panel", None)
                if panel is not None:
                    panel.repaint()
                overlay.repaint()
            except RuntimeError:
                pass
        QApplication.processEvents()

    def _run_bulk_busy(self, message: str, work: Callable[[], None]) -> None:
        """Show branded horse loader while bulk Park/Allow all applies rows."""
        if self._bulk_busy:
            work()
            return
        overlay = self._ensure_busy_overlay()
        self._bulk_busy = True
        try:
            overlay.show_loading(message)
            overlay.raise_()
            self._pump_busy()
            work()
        finally:
            self._bulk_busy = False
            try:
                if self._busy_overlay is not None:
                    self._busy_overlay.hide_loading()
            except RuntimeError:
                pass

    def _park_all_remaining(self) -> None:
        rows = list(self._rows)
        held = len(self._held_issues)
        if not rows and not held:
            return
        n = len(rows) + held
        plural = "s" if n != 1 else ""

        def _work() -> None:
            self._defer_page = True
            try:
                for i, rw in enumerate(rows):
                    if rw in self._rows:
                        self._park_row(rw)
                    if i == 0 or (i + 1) % 8 == 0 or i + 1 == n:
                        self._pump_busy()
            finally:
                self._defer_page = False
            self._park_held_issues()
            self._update_count()
            if not self._rows and self.isVisible():
                self.accept()

        self._run_bulk_busy(f"Parking {n} truck{plural}…", _work)

    def _allow_held_issues(self) -> None:
        """Allow rows that were never given a card, using the pasted value."""
        for issue in list(self._held_issues):
            text = (issue.original or "").strip()
            if not text:
                if self._fixing_parked_trucks():
                    issue.corrected = ""
                    issue.skip = False
                    issue.omit_row = False
                    issue.parked = False
                    issue.allow_anyway = False
                    issue.is_place_label = False
                    issue.accept_empty = True
                    self.issues.append(issue)
                    self._notify_resolved(issue)
                elif self._import_mode:
                    issue.corrected = ""
                    issue.omit_row = True
                    issue.parked = False
                    issue.allow_anyway = False
                    issue.accept_empty = False
                    self.issues.append(issue)
                    self._notify_resolved(issue)
                continue
            norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
            if norm.status in ("ok", "normalized", "place_label"):
                value = norm.value
            else:
                value = " ".join(text.upper().split())
            is_place = norm.status == "place_label" or is_allowed_place_label(
                value, self._allowed_labels
            )
            matched = None if is_place else try_match_fleet(value, self._fleet)
            if is_place:
                self._resolve_issue(
                    issue,
                    corrected=normalize_place_label(value),
                    is_place_label=True,
                )
            elif matched is not None and not self._is_two_trailer_issue(issue):
                self._resolve_issue(issue, corrected=matched)
            else:
                self._resolve_issue(
                    issue,
                    corrected=matched or value,
                    allow_anyway=True,
                )
            self.issues.append(issue)
            self._notify_resolved(issue)
        self._held_issues.clear()

    def _notify_resolved(self, issue: TruckIssue) -> None:
        if not callable(self._on_resolved):
            return
        try:
            self._on_resolved(issue)
        except Exception:
            pass

    def _allow_all_remaining(self) -> None:
        """Allow every open row after one Fluent confirmation."""
        open_rows = list(self._rows)
        held = len(self._held_issues)
        if not open_rows and not held:
            return

        n = len(open_rows) + held
        samples = [
            (self._truck_edit_value(rw) or "").strip() or "—"
            for rw in open_rows[:5]
        ]
        if held and len(samples) < 5:
            samples.append(f"{held:,} more not on this page")
        extra = f"\n…and {n - len(samples)} more" if n > len(samples) else ""
        body = "\n".join(f"  • {s}" for s in samples) + extra
        if not self._confirm_allow(
            title="Allow all",
            heading=f"Allow all {n} remaining truck(s)?",
            body=body,
            note=(
                "Trucks already in the fleet are applied normally. "
                "Others are kept as partner / non-fleet values."
            ),
        ):
            return

        plural = "s" if n != 1 else ""

        def _work() -> None:
            self._defer_page = True
            try:
                for i, rw in enumerate(list(open_rows)):
                    if rw in self._rows:
                        self._allow_anyway_row(rw, ask_similar=False)
                    if i == 0 or (i + 1) % 8 == 0 or i + 1 == n:
                        self._pump_busy()
            finally:
                self._defer_page = False
            self._allow_held_issues()
            self._update_count()
            if not self._rows and not self._held_issues and self.isVisible():
                self.accept()

        self._run_bulk_busy(f"Allowing {n} truck{plural}…", _work)

    def _accept_label_row(self, rw: _RowWidgets) -> None:
        text = normalize_place_label(rw.edit.text())
        if not text:
            show_warning(self, "Label", "Enter a place label first (e.g. YARD).")
            return
        if not is_place_label_candidate(text) and not is_allowed_place_label(
            text, self._allowed_labels
        ):
            show_warning(
                self,
                "Label",
                "Place labels should be words like YARD or GARAGE "
                "(not a truck number).",
            )
            return
        if text not in self._allowed_labels:
            self._allowed_labels.add(text)
            self.new_labels.append(text)
        also = self._confirm_apply_similar(rw, text, action="apply")
        self._finish_resolved(
            rw,
            corrected=text,
            is_place_label=True,
            also=also,
        )

    def _apply_combo_row(self, rw: _RowWidgets) -> None:
        if self._fixing_parked_trucks() and not any(
            edit.text().strip() for edit in rw.part_edits
        ):
            self._accept_empty_row(rw)
            return
        parts, err = self._combo_resolved_parts(rw)
        if err is not None:
            if self._import_mode and not any(e.text().strip() for e in rw.part_edits):
                self._omit_row(rw)
                return
            show_warning(self, "Truck / trailer", err)
            return
        merged = "/".join(parts or [])
        also = self._confirm_apply_similar(rw, merged, action="apply")
        self._finish_resolved(rw, corrected=merged, also=also)

    def _apply_row(self, rw: _RowWidgets) -> None:
        if rw.part_edits:
            self._apply_combo_row(rw)
            return
        text = rw.edit.text().strip()
        if not text:
            if self._import_mode:
                self._omit_row(rw)
            elif self._fixing_parked_trucks():
                self._accept_empty_row(rw)
            else:
                self._clear_row(rw)
            return

        if self._is_two_trailer_issue(rw.issue):
            show_warning(
                self,
                "Two trailers",
                "This cell has two trailers and cannot be applied as a "
                "normal truck match.\n\n"
                "Use “Allow anyway” to import as-is, or “Skip row”.",
            )
            rw.edit.setFocus()
            return

        if is_allowed_place_label(text, self._allowed_labels):
            value = normalize_place_label(text)
            also = self._confirm_apply_similar(rw, value, action="apply")
            self._finish_resolved(
                rw, corrected=value, is_place_label=True, also=also,
            )
            return

        norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
        if norm.status == "place_label":
            also = self._confirm_apply_similar(rw, norm.value, action="apply")
            self._finish_resolved(
                rw, corrected=norm.value, is_place_label=True, also=also,
            )
            return

        if norm.status == "invalid":
            if self._import_mode:
                show_warning(
                    self,
                    "Invalid format",
                    f'"{text}" is not a recognized truck format yet.\n\n'
                    "Use “Allow anyway” to import as-is, “Skip row” to park it, "
                    "or enter a T + digits + suffix number (e.g. T688 EAF).",
                )
            else:
                show_warning(
                    self,
                    "Invalid format",
                    f'"{text}" is not a valid truck number.\n\n'
                    "Use T + digits + space + suffix, e.g. T688 EAF,\n"
                    "or click “Accept label” for YARD / GARAGE.",
                )
            rw.edit.setFocus()
            return

        number = norm.value
        rw.edit.setText(number)
        self._refresh_status_badge(rw)
        matched = try_match_fleet(number, self._fleet)
        if matched is None:
            tip = (
                "Use “Add to registry”, “Allow”, or “Skip”."
                if self._import_mode
                else "Use “Add to registry”, “Allow”, or enter a registered number."
            )
            show_warning(
                self,
                "Not in registry",
                f'"{number}" is not in the fleet registry.\n\n{tip}',
            )
            rw.edit.setFocus()
            return

        also = self._confirm_apply_similar(rw, matched, action="apply")
        self._finish_resolved(rw, corrected=matched, also=also)

    def _ask_registry_kind(self, number: str) -> Optional[str]:
        """Ask which fleet collection to use. Returns collection path or None."""
        box = QMessageBox(self)
        box.setWindowTitle("Add to registry")
        box.setIcon(QMessageBox.Question)
        box.setText(f'Add "{number}" to which registry?')
        box.setInformativeText(
            "Truck / Trailer require plate format T688 EAF.\n"
            "Motorcycle/Car accepts other registration styles."
        )
        truck_btn = box.addButton("Truck", QMessageBox.AcceptRole)
        trailer_btn = box.addButton("Trailer", QMessageBox.AcceptRole)
        motor_btn = box.addButton("Motorcycle/Car", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        style_message_box(box)
        box.exec()
        clicked = box.clickedButton()
        if clicked is truck_btn:
            return "trucks"
        if clicked is trailer_btn:
            return "trailers"
        if clicked is motor_btn:
            return "motor_vehicles"
        return None

    def _confirm_registry_add(self, number: str, kind: str) -> bool:
        """Fluent confirm before queuing a fleet registry add."""
        kind_label = _KIND_LABELS.get(kind, kind)
        return ask_confirm(
            self,
            title="Add to registry",
            heading=f'Add "{number}" to the fleet registry?',
            body=f"It will be saved as a {kind_label}.",
            note="This update is shared with everyone who uses the fleet list.",
            accept_label="Add",
            cancel_label="Cancel",
            destructive=False,
            default_cancel=True,
        )

    def _confirm_allow(
        self,
        *,
        title: str,
        heading: str,
        body: str = "",
        note: str = "",
    ) -> bool:
        """Fluent confirm before Allow / Allow all (non-fleet override)."""
        return ask_confirm(
            self,
            title=title,
            heading=heading,
            body=body,
            note=note,
            accept_label="Allow",
            cancel_label="Cancel",
            destructive=False,
            default_cancel=True,
        )

    def _commit_registry_add(self, text: str) -> Optional[str]:
        """Ask kind → confirm → queue the plate. Returns canonical number."""
        if not text:
            show_warning(
                self, "Registry", "Enter a registration number first."
            )
            return None
        if is_allowed_place_label(text, self._allowed_labels) or (
            is_place_label_candidate(text) and not any(ch.isdigit() for ch in text)
        ):
            show_warning(
                self,
                "Registry",
                f'"{text}" looks like a place label, not a vehicle.\n'
                "Use “Label” for YARD / GARAGE instead.",
            )
            return None

        kind = self._ask_registry_kind(text)
        if kind is None:
            return None

        if kind in ("trucks", "trailers"):
            norm = normalize_truck_number(text, allowed_labels=())
            if norm.status not in ("ok", "normalized"):
                show_warning(
                    self,
                    "Format",
                    f'Truck/Trailer numbers must look like T688 EAF.\n\n'
                    f'"{text}" is not a valid plate format.\n'
                    "Choose Motorcycle/Car for other registration styles.",
                )
                return None
            number = norm.value
        else:
            number = " ".join(text.upper().split())
            if len(number) < 2:
                show_warning(
                    self, "Registry", "Enter a registration number first."
                )
                return None

        if not self._confirm_registry_add(number, kind):
            return None

        from tahmeed.services.truck_service import collection_to_kind

        self.pending_registry_adds.append((kind, number))
        self._fleet.add(number)
        self._fleet_kinds[number] = collection_to_kind(kind)
        self._refresh_fleet_completers()
        return number

    def _add_to_registry_row(self, rw: _RowWidgets) -> None:
        text = rw.edit.text().strip()
        number = self._commit_registry_add(text)
        if not number:
            return

        rw.edit.setText(number)
        self._refresh_status_badge(rw)
        self._refresh_kind_label(rw)
        if self._is_two_trailer_issue(rw.issue):
            return
        also = self._confirm_apply_similar(rw, number, action="add")
        self._finish_resolved(rw, corrected=number, also=also)

    # ── Dialog close ──────────────────────────────────────────────────────

    def _on_done(self) -> None:
        """Auto-apply every remaining row that can be resolved; warn if any remain."""
        for rw in list(self._rows):
            if rw.part_edits:
                if self._fixing_parked_trucks() and not any(
                    edit.text().strip() for edit in rw.part_edits
                ):
                    self._accept_empty_row(rw)
                    continue
                parts, err = self._combo_resolved_parts(rw)
                if err is None and parts:
                    issue = rw.issue
                    issue.corrected = "/".join(parts)
                    issue.skip = False
                    issue.is_place_label = False
                    self._remove_row(rw, issue)
                continue
            text = rw.edit.text().strip()
            if not text:
                if self._fixing_parked_trucks():
                    self._accept_empty_row(rw)
                else:
                    self._clear_row(rw)
                continue
            if is_allowed_place_label(text, self._allowed_labels) or is_place_label_candidate(text):
                # Only auto-accept if already remembered; candidates need explicit click
                if is_allowed_place_label(text, self._allowed_labels):
                    issue = rw.issue
                    issue.corrected = normalize_place_label(text)
                    issue.skip = False
                    issue.is_place_label = True
                    self._remove_row(rw, issue)
                continue
            norm = normalize_truck_number(text, allowed_labels=self._allowed_labels)
            if norm.status == "place_label":
                issue = rw.issue
                issue.corrected = norm.value
                issue.skip = False
                issue.is_place_label = True
                self._remove_row(rw, issue)
                continue
            if norm.status in ("ok", "normalized"):
                if self._is_two_trailer_issue(rw.issue):
                    continue
                matched = try_match_fleet(norm.value, self._fleet)
                if matched is not None:
                    rw.edit.setText(matched)
                    issue = rw.issue
                    issue.corrected = self._with_combo_suffix(issue, matched)
                    issue.skip = False
                    issue.is_place_label = False
                    self._remove_row(rw, issue)
                    continue
        if self._rows:
            tip = (
                "Skip row / Allow anyway, or fix with Apply."
                if self._import_mode
                else "Resolve them in the list, or Cancel remaining."
            )
            show_info(
                self,
                "Still need attention",
                f"{len(self._rows)} truck(s) still need attention.\n{tip}",
            )
            focus = self._rows[0]
            (focus.part_edits[0] if focus.part_edits else focus.edit).setFocus()
            return
        # Empty list already called accept() via _remove_row; if we got here with
        # zero rows for another reason, close cleanly.
        if not self._rows:
            self.accept()

    def _on_cancel(self) -> None:
        """Park every still-open issue and close."""
        self._park_all_remaining()
        self._update_count()
        if self.isVisible():
            self.accept()
