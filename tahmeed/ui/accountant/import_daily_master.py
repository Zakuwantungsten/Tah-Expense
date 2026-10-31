"""Accountant page: bulk Import Daily → Master Expenses.

Load many daily / MASTER Excel files into a local import session, group rows
by reconciled day, map descriptions with the built-in expense automation
mappings and/or Description Maps (plus mappings typed here), fix trucks row by
row, then push ticked Ready days. Finishing the session discards it, optionally
saving session-only typed mappings to Description Maps first. The single-file
flow stays available as "Single file".
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from tahmeed.models.user import User
from tahmeed.services.daily_import_service import DailyImportCancelled
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.import_session_parser import (
    SKIP_REASON_LABELS,
    common_sheet_names,
)
from tahmeed.services.import_session_pack import apply_mapping_pack, builtin_mappings
from tahmeed.services.import_session_push import (
    DayNotReady,
    PriorUpload,
    PushGroup,
    build_push_groups,
    date_outliers,
    find_prior_uploads,
    push_groups,
    row_date_range,
    supplier_rows,
)
from tahmeed.services.import_session_service import (
    MODE_BOTH_BUILTIN_FIRST,
    MODE_BOTH_MAPS_FIRST,
    MODE_BUILTIN_ONLY,
    MODE_MAPS_ONLY,
    ImportSession,
    add_files_to_session,
    delete_session,
    list_sessions,
    load_session,
    save_session,
    truck_key,
)
from tahmeed.services.import_session_validation import (
    DAY_BLOCKED,
    DAY_PUSHED,
    DAY_READY,
    ISSUE_BAD_ITEM,
    ISSUE_BAD_PAYEE,
    ISSUE_UNMAPPED,
    KIND_BAD_MAP,
    KIND_IGNORED,
    KIND_UNMAPPED,
    SOURCE_LABELS,
    DescriptionIssue,
    ImportReferences,
    SessionValidation,
    TruckRowIssue,
    is_fleet_truck,
    issue_text,
    load_import_references,
    rank_truck_matches,
    validate_session,
)
from tahmeed.ui.accountant.import_session_grid import (
    BLOCKED_BG,
    IGNORED_BG,
    PUSHED_BG,
    READY_BG,
    UNMAPPED_BG,
    GridModel,
    GridRow,
    BadgeTabWidget,
    GridView,
    InlineEntry,
    connect_rows,
    light_theme_qss,
)
from tahmeed.ui.async_utils import create_task, pause_background_polls
from tahmeed.ui.dialog_theme import (
    get_text,
    show_critical,
    show_info,
    show_question,
    show_warning,
    style_input_dialog,
    style_message_box,
)
from tahmeed.ui.dialogs.import_session_dialogs import (
    FinishSessionDialog,
    LoadPackDialog,
    MapDescriptionsDialog,
    PromoteCandidate,
    TruckFixDialog,
    WrongTruck,
    ask_new_item,
    ask_new_payee,
    dialog_button,
)
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.upload_busy import UploadBusy, UploadCancelled

_BG = "#F4F6F8"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"
DRAFT_BG = "#FEF9C3"
_BADGE_RED = "#DC2626"  # same as the sidebar badges
_BADGE_GREY = "#9CA3AF"
_MAPPING_MODE_LABELS = {
    MODE_BOTH_MAPS_FIRST: "Both — Description Maps first",
    MODE_BOTH_BUILTIN_FIRST: "Both — Built-in first",
    MODE_BUILTIN_ONLY: "Built-in only",
    MODE_MAPS_ONLY: "Description Maps only",
}

SCOPE_ALL = "All days"
SCOPE_DAY = "Selected day"

TAB_DAYS, TAB_ISSUES, TAB_TRUCKS, TAB_EXCEL, TAB_SKIPPED, TAB_PREVIEW, TAB_FILES = range(7)
_BADGE_TABS = (TAB_DAYS, TAB_ISSUES, TAB_TRUCKS, TAB_EXCEL, TAB_SKIPPED)

# Day Issues grid columns (cell index, without the tick column).
_ISSUE_PAYEE_COL = 4
_ISSUE_ITEM_COL = 5
# Truck Issues grid: the CORRECT TRUCK cell.
_TRUCK_FIX_COL = 7


def _fmt_day(value: Optional[date]) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def _files_label(names: List[str]) -> str:
    if len(names) <= 2:
        return " + ".join(names)
    return f"{names[0]} + {len(names) - 1} more"


def _scope_combo(default_day: bool = False) -> QComboBox:
    combo = QComboBox()
    combo.addItems([SCOPE_ALL, SCOPE_DAY])
    combo.setCurrentIndex(1 if default_day else 0)
    return combo


def _push_report_box(parent: QWidget, text: str, *, warning: bool, can_open: bool) -> bool:
    """Push result message; True when the user picks Open in Master Expenses."""
    icon = QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information
    box = QMessageBox(icon, "Push to Master", text, QMessageBox.StandardButton.NoButton, parent)
    open_btn = None
    if can_open:
        open_btn = box.addButton("Open in Master Expenses", QMessageBox.ButtonRole.ActionRole)
    ok = box.addButton(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(ok)
    style_message_box(box)
    box.exec()
    return open_btn is not None and box.clickedButton() is open_btn


def _label(text: str = "", *, muted: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{_T2 if muted else _T1};font-family:'Segoe UI';font-size:12px;"
        "background:transparent;"
    )
    return lbl


class ImportDailyMasterWidget(QWidget):
    """Bulk daily Excel import workspace (Day Batches, Issues, Trucks, …)."""

    open_master_dates = Signal(object, object)  # first, last row date

    def __init__(self, user: User, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._user = user
        self._session: Optional[ImportSession] = None
        self._refs: Optional[ImportReferences] = None
        self._validation: Optional[SessionValidation] = None
        self._current_day: Optional[date] = None
        self._issue_drafts: Dict[str, Dict[str, str]] = {}
        self._truck_drafts: Dict[str, str] = {}  # row_id -> plate typed, not saved yet
        self._truck_draft_allowed: set[str] = set()  # drafts kept although not in the fleet
        self._truck_rows_by_id: Dict[str, TruckRowIssue] = {}
        self._issues_by_key: Dict[str, DescriptionIssue] = {}
        self._dirty: set[int] = set()
        self._busy = False
        self._loaded = False
        self._build()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        self.setObjectName("importDailyMaster")
        self.setStyleSheet(
            f"QWidget#importDailyMaster {{ background: {_BG}; }}"
            "QWidget#importTabPage { background: #FFFFFF; }"
            + light_theme_qss()
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        try:
            icon = QLabel()
            icon.setPixmap(qta.icon("mdi.file-upload-outline", color=_BLUE).pixmap(26, 26))
            icon.setStyleSheet("background: transparent;")
            header.addWidget(icon)
        except Exception:
            pass
        title = QLabel("Import Daily → Master Expenses")
        title.setStyleSheet(
            f"color:{_T1};font-size:18px;font-weight:700;font-family:'Segoe UI';"
            "background:transparent;"
        )
        header.addWidget(title)
        header.addStretch()
        header.addWidget(_label("Session:", muted=True))
        self._session_combo = QComboBox()
        self._session_combo.setMinimumWidth(260)
        self._session_combo.activated.connect(self._on_session_picked)
        header.addWidget(self._session_combo)
        for text, slot in (
            ("New", self._on_new_session),
            ("Rename", self._on_rename_session),
            ("Delete", self._on_delete_session),
            ("Finish Session…", self._on_finish_session),
        ):
            btn = dialog_button(text)
            btn.clicked.connect(slot)
            header.addWidget(btn)
        root.addLayout(header)

        toolbar = QHBoxLayout()
        self._add_btn = dialog_button("  Add Excel Files…", primary=True)
        try:
            self._add_btn.setIcon(qta.icon("mdi.microsoft-excel", color="#FFFFFF"))
            self._add_btn.setIconSize(QSize(16, 16))
        except Exception:
            pass
        self._add_btn.clicked.connect(self._on_add_files)
        toolbar.addWidget(self._add_btn)
        refresh_btn = dialog_button("Refresh Lists")
        refresh_btn.setToolTip(
            "Reload Items, Payees, Trucks, Trailers, Motorcycles & Cars and Description Maps"
        )
        refresh_btn.clicked.connect(lambda: self._start(self._load_references(refresh=True)))
        toolbar.addWidget(refresh_btn)
        pack_btn = dialog_button("Load Mapping Pack…")
        pack_btn.setToolTip(
            "Use a newer expense automation pack instead of the built-in mappings for "
            "this session, or export a pack (trucks never come from a pack)"
        )
        pack_btn.clicked.connect(self._on_load_pack)
        toolbar.addWidget(pack_btn)
        toolbar.addSpacing(16)
        toolbar.addWidget(_label("MAPPINGS", muted=True))
        self._mapping_mode = QComboBox()
        for mode, text in _MAPPING_MODE_LABELS.items():
            self._mapping_mode.addItem(text, mode)
        self._mapping_mode.setToolTip(
            "Which mappings fill in Payee + Item: the built-in QuickBooks automation "
            "mappings, the Description Maps tab, or both (the first one listed wins). "
            "Mappings you type in this session always apply."
        )
        self._mapping_mode.currentIndexChanged.connect(self._on_mapping_mode_changed)
        toolbar.addWidget(self._mapping_mode)
        toolbar.addStretch()
        classic = dialog_button("Single file (classic)…")
        classic.setToolTip("Old one-file import straight to Master Expenses")
        classic.clicked.connect(self._on_classic_import)
        toolbar.addWidget(classic)
        root.addLayout(toolbar)

        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            "background:#FFFFFF;border:1px solid #E5E7EB;border-radius:6px;padding:8px 10px;"
            f"color:{_T1};font-family:'Segoe UI';font-size:12px;"
        )
        root.addWidget(self._banner)

        self._tabs = BadgeTabWidget()
        self._tabs.addTab(self._build_days_tab(), "Day Batches")
        self._tabs.addTab(self._build_issues_tab(), "Day Issues")
        self._tabs.addTab(self._build_trucks_tab(), "Truck Issues")
        self._tabs.addTab(self._build_excel_tab(), "Excel Issues")
        self._tabs.addTab(self._build_skipped_tab(), "Skipped Rows")
        self._tabs.addTab(self._build_preview_tab(), "Preview")
        self._tabs.addTab(self._build_files_tab(), "Files")
        self._tabs.currentChanged.connect(lambda _i: self._fill_current_tab())
        root.addWidget(self._tabs, 1)
        self._update_banner()

    def _tab_page(self) -> tuple[QWidget, QVBoxLayout, QHBoxLayout]:
        page = QWidget()
        page.setObjectName("importTabPage")
        page.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        return page, layout, bar

    def _search_box(self, view: GridView, placeholder: str = "Search…") -> QLineEdit:
        box = QLineEdit()
        box.setPlaceholderText(placeholder)
        box.setClearButtonEnabled(True)
        box.setMaximumWidth(240)
        box.textChanged.connect(view.set_filter_text)
        return box

    def _build_days_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._days_model = GridModel(
            ["DATE", "SOURCE FILE(S)", "ROWS", "TO PUSH", "TZS", "USD", "STATUS"],
            checkable=True,
            right_align=[2, 3, 4, 5],
        )
        self._days_view = GridView(self._days_model)
        connect_rows(self._days_view, self._on_day_selected)
        self._days_view.doubleClicked.connect(self._on_day_double_clicked)
        self._days_model.checks_changed.connect(self._update_ticked_label)

        tick_ready = dialog_button("Tick All Ready")
        tick_ready.clicked.connect(self._on_tick_ready)
        clear = dialog_button("Clear Ticks")
        clear.clicked.connect(self._days_model.clear_checks)
        self._ticked_label = _label(muted=True)
        push = dialog_button("Push Ticked Days to Master", primary=True)
        push.setToolTip(
            "Save the ticked Ready days to Master Expenses as verified rows. "
            "Each day shows in Browse → Uploads under its MATUMIZI file name."
        )
        push.clicked.connect(self._on_push)
        unmark = dialog_button("Unmark Pushed")
        unmark.setToolTip(
            "Click Pushed days to highlight them, then Unmark to push them again "
            "(rows already in Master are skipped)"
        )
        unmark.clicked.connect(self._on_unmark_pushed)
        bar.addWidget(tick_ready)
        bar.addWidget(clear)
        bar.addWidget(self._ticked_label)
        bar.addSpacing(12)
        bar.addWidget(push)
        bar.addWidget(unmark)
        bar.addStretch()
        bar.addWidget(_label("Double-click a day to see its issues or preview.", muted=True))
        layout.addWidget(self._days_view, 1)
        return page

    def _build_issues_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._issues_model = GridModel(
            ["TYPE", "DESCRIPTION", "ROWS", "DAYS", "PAYEE", "ITEM", "FROM", "DETAIL"],
            checkable=True,
            right_align=[2, 3],
        )
        self._issues_model.edited.connect(self._on_issue_cell_edited)
        self._issues_view = GridView(self._issues_model)
        self._issues_entry = InlineEntry(
            self._issues_view,
            {
                _ISSUE_PAYEE_COL: lambda: self._refs.payees.names() if self._refs else [],
                _ISSUE_ITEM_COL: lambda: self._refs.items.names() if self._refs else [],
            },
            uppercase=[_ISSUE_PAYEE_COL],
        )
        self._issues_scope = _scope_combo()
        self._issues_scope.currentIndexChanged.connect(lambda _i: self._fill_issues())
        bar.addWidget(_label("SHOW", muted=True))
        bar.addWidget(self._issues_scope)
        bar.addWidget(self._search_box(self._issues_view, "Search description…"))
        for text, slot, primary in (
            ("Map Selected…", self._on_map_selected, False),
            ("Map All Unmapped…", self._on_map_all, False),
            ("Save Filled Mappings", self._on_save_filled, True),
            ("Remove Temp Mapping", self._on_remove_temp_mapping, False),
        ):
            btn = dialog_button(text, primary=primary)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        self._save_global_box = QCheckBox("Save to Description Maps")
        self._save_global_box.setChecked(True)
        self._save_global_box.setToolTip(
            "Ticked: mappings you enter are added to the Description Maps tab right away "
            "(permanent, every PC). Untick for a one-off mapping kept in this session only."
        )
        bar.addWidget(self._save_global_box)
        bar.addStretch()
        self._issues_counter = _label(muted=True)
        layout.addWidget(self._issues_counter)
        layout.addWidget(self._issues_view, 1)
        hint = _label(
            "Click a PAYEE or ITEM cell (or just start typing) and pick from the list; Tab "
            "moves PAYEE → ITEM → next row, Shift+Tab goes back, Enter goes down. A name "
            "not in the list can be added as a new Payee / Item on the spot. Then Save "
            "Filled Mappings — or tick rows and use Map. Every row must be mapped.",
            muted=True,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _build_trucks_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._trucks_model = GridModel(
            [
                "STATUS",
                "DAY",
                "FILE",
                "XL ROW",
                "DESCRIPTION",
                "AMOUNT",
                "TRUCK IN EXCEL",
                "CORRECT TRUCK",
            ],
            checkable=True,
            right_align=[3, 5],
        )
        self._trucks_model.edited.connect(self._on_truck_cell_edited)
        self._trucks_view = GridView(self._trucks_model)
        self._trucks_view.doubleClicked.connect(self._on_truck_double_clicked)
        self._trucks_entry = InlineEntry(
            self._trucks_view,
            {_TRUCK_FIX_COL: self._truck_choices},
            uppercase=[_TRUCK_FIX_COL],
            matchers={_TRUCK_FIX_COL: rank_truck_matches},
        )
        self._trucks_scope = _scope_combo()
        self._trucks_scope.currentIndexChanged.connect(lambda _i: self._fill_trucks())
        bar.addWidget(_label("SHOW", muted=True))
        bar.addWidget(self._trucks_scope)
        bar.addWidget(self._search_box(self._trucks_view, "Search…"))
        for text, slot, primary in (
            ("Fix Trucks…", lambda: self._on_fix_trucks(), True),
            ("Save Truck Fixes", self._on_save_truck_fixes, False),
            ("Set No Truck", self._on_set_no_truck, False),
            ("Remove Fix", self._on_remove_truck_fix, False),
        ):
            btn = dialog_button(text, primary=primary)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        bar.addStretch()
        self._trucks_counter = _label(muted=True)
        layout.addWidget(self._trucks_counter)
        layout.addWidget(self._trucks_view, 1)
        hint = _label(
            "Fix Trucks… (or double-click a row) goes through each wrong truck: type and Tab to "
            "pick one of our trucks, trailers or motorcycles & cars, then Apply & Next; Accept as "
            "Empty for no truck, or Allow to keep a value that is not in our fleet. The fix goes "
            "on every row with that same Excel value, in this session only — nothing is guessed "
            "or saved for later imports. To fix a single row, type in CORRECT TRUCK (Tab picks "
            "the suggestion and moves on) and Save Truck Fixes.",
            muted=True,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _build_excel_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._excel_model = GridModel([], right_align=[])
        self._excel_view = GridView(self._excel_model)
        self._excel_scope = _scope_combo()
        self._excel_scope.currentIndexChanged.connect(lambda _i: self._fill_excel())
        self._excel_filter = QComboBox()
        self._excel_filter.addItems(["Problem rows", "All rows"])
        self._excel_filter.currentIndexChanged.connect(lambda _i: self._fill_excel())
        bar.addWidget(_label("SHOW", muted=True))
        bar.addWidget(self._excel_scope)
        bar.addWidget(self._excel_filter)
        bar.addWidget(self._search_box(self._excel_view))
        bar.addStretch()
        self._excel_count = _label(muted=True)
        bar.addWidget(self._excel_count)
        layout.addWidget(self._excel_view, 1)
        return page

    def _build_skipped_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._skipped_model = GridModel([], right_align=[])
        self._skipped_view = GridView(self._skipped_model)
        self._skipped_scope = _scope_combo()
        self._skipped_scope.currentIndexChanged.connect(lambda _i: self._fill_skipped())
        self._skipped_reason = QComboBox()
        self._skipped_reason.currentIndexChanged.connect(lambda _i: self._fill_skipped(keep_reason=True))
        bar.addWidget(_label("SHOW", muted=True))
        bar.addWidget(self._skipped_scope)
        bar.addWidget(_label("REASON", muted=True))
        bar.addWidget(self._skipped_reason)
        bar.addWidget(self._search_box(self._skipped_view))
        export = dialog_button("Export Skipped…")
        export.clicked.connect(self._on_export_skipped)
        bar.addWidget(export)
        bar.addStretch()
        self._skipped_count = _label(muted=True)
        bar.addWidget(self._skipped_count)
        layout.addWidget(self._skipped_view, 1)
        return page

    def _build_preview_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._preview_model = GridModel(
            ["RECONCILED", "DATE", "PAYEE", "ITEM", "DESCRIPTION", "TRUCK", "TZS", "USD",
             "MEMO", "SOURCE FILE", "MAPPED BY", "STATUS"],
            right_align=[6, 7],
        )
        self._preview_view = GridView(self._preview_model)
        self._preview_scope = _scope_combo(default_day=True)
        self._preview_scope.currentIndexChanged.connect(lambda _i: self._fill_preview())
        bar.addWidget(_label("SHOW", muted=True))
        bar.addWidget(self._preview_scope)
        bar.addWidget(self._search_box(self._preview_view))
        bar.addStretch()
        self._preview_count = _label(muted=True)
        bar.addWidget(self._preview_count)
        layout.addWidget(self._preview_view, 1)
        return page

    def _build_files_tab(self) -> QWidget:
        page, layout, bar = self._tab_page()
        self._files_model = GridModel(
            ["FILE", "SHEET", "ROWS", "SKIPPED", "WARNINGS", "PATH"],
            checkable=True,
            right_align=[2, 3],
        )
        self._files_view = GridView(self._files_model)
        remove = dialog_button("Remove Selected Files")
        remove.clicked.connect(self._on_remove_files)
        reload_btn = dialog_button("Re-read Selected Files")
        reload_btn.clicked.connect(self._on_reload_files)
        bar.addWidget(remove)
        bar.addWidget(reload_btn)
        bar.addStretch()
        layout.addWidget(self._files_view, 1)
        return page

    # --------------------------------------------------------------- lifecycle

    def refresh(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._start(self._initial_load())

    def _start(self, coro) -> None:
        if self._busy:
            coro.close()
            return
        create_task(self._guarded(coro))

    async def _guarded(self, coro) -> None:
        self._busy = True
        try:
            await coro
        except (UploadCancelled, DailyImportCancelled):
            pass
        except Exception as exc:
            show_critical(self, "Import Daily", f"Something went wrong:\n\n{exc}")
        finally:
            self._busy = False

    async def _initial_load(self) -> None:
        sessions = list_sessions()
        session = None
        if sessions:
            try:
                session = load_session(sessions[0].session_id)
            except (OSError, ValueError, KeyError, TypeError):
                session = None
        self._set_session(session or self._new_session())
        await self._load_references(refresh=False)

    async def _load_references(self, *, refresh: bool) -> None:
        if refresh:
            from tahmeed.services.description_mapping_service import (
                invalidate_description_mapping_cache,
            )

            invalidate_description_mapping_cache()
        show_widget_loading(self, "Loading items, payees, trucks and trailers…")
        try:
            refs = await load_import_references(refresh=refresh)
            refs.builtin_mappings = builtin_mappings()
            self._refs = refs
        finally:
            hide_widget_loading(self)
        self._revalidate()

    # ----------------------------------------------------------------- session

    def _new_session(self, name: str = "") -> ImportSession:
        session = ImportSession.new(name)
        save_session(session)
        return session

    def _set_session(self, session: ImportSession) -> None:
        if session.ignored:
            # Every expense is tracked; drop ignores left by older sessions.
            session.ignored.clear()
            session.touch()
            save_session(session)
        self._session = session
        self._issue_drafts.clear()
        self._truck_drafts.clear()
        self._truck_draft_allowed.clear()
        self._current_day = None
        self._days_model.clear_checks()
        self._mapping_mode.blockSignals(True)
        self._mapping_mode.setCurrentIndex(
            max(0, self._mapping_mode.findData(session.options.mapping_mode))
        )
        self._mapping_mode.blockSignals(False)
        self._fill_session_combo()
        self._revalidate()

    def _fill_session_combo(self) -> None:
        self._session_combo.blockSignals(True)
        self._session_combo.clear()
        current = self._session.session_id if self._session else None
        for summary in list_sessions():
            label = (
                f"{summary.name}  ·  {summary.file_count} file(s), "
                f"{summary.row_count:,} rows"
            )
            self._session_combo.addItem(label, summary.session_id)
        if self._session is not None and self._session_combo.findData(current) < 0:
            self._session_combo.insertItem(0, self._session.name, current)
        index = self._session_combo.findData(current)
        if index >= 0:
            self._session_combo.setCurrentIndex(index)
        self._session_combo.blockSignals(False)

    def _on_session_picked(self, index: int) -> None:
        session_id = self._session_combo.itemData(index)
        if not session_id or (self._session and session_id == self._session.session_id):
            return
        try:
            self._set_session(load_session(session_id))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            show_critical(self, "Import session", f"Could not open that session:\n\n{exc}")
            self._fill_session_combo()

    def _on_new_session(self) -> None:
        name, ok = get_text(self, "New import session", "Session name:", "")
        if ok:
            self._set_session(self._new_session(name))

    def _on_rename_session(self) -> None:
        if self._session is None:
            return
        name, ok = get_text(self, "Rename session", "Session name:", self._session.name)
        if ok and name.strip():
            self._session.rename(name)
            self._save()
            self._fill_session_combo()

    def _on_delete_session(self) -> None:
        if self._session is None:
            return
        answer = show_question(
            self,
            "Delete session",
            f"Delete '{self._session.name}'?\n\n"
            "Its staged rows and temporary mappings are discarded. "
            "Days already pushed to Master Expenses are not affected.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_session(self._session.session_id)
        remaining = list_sessions()
        session = load_session(remaining[0].session_id) if remaining else self._new_session()
        self._set_session(session)

    def _save(self) -> None:
        if self._session is not None:
            save_session(self._session)

    def _on_mapping_mode_changed(self, _index: int) -> None:
        if self._session is None:
            return
        self._session.options.mapping_mode = self._mapping_mode.currentData()
        self._session.touch()
        self._save()
        self._revalidate()

    # ------------------------------------------------------------- validation

    def _revalidate(self) -> None:
        if self._session is not None and self._refs is not None:
            self._validation = validate_session(self._session, self._refs)
        else:
            self._validation = None
        self._fill_days()
        self._update_banner()
        self._update_tab_badges()
        self._dirty = {TAB_ISSUES, TAB_TRUCKS, TAB_EXCEL, TAB_SKIPPED, TAB_PREVIEW, TAB_FILES}
        self._fill_current_tab()

    def _changed(self) -> None:
        """Persist the session and re-check every row."""
        self._save()
        self._revalidate()

    def _fill_current_tab(self) -> None:
        index = self._tabs.currentIndex()
        if index not in self._dirty:
            return
        self._dirty.discard(index)
        {
            TAB_ISSUES: self._fill_issues,
            TAB_TRUCKS: self._fill_trucks,
            TAB_EXCEL: self._fill_excel,
            TAB_SKIPPED: self._fill_skipped,
            TAB_PREVIEW: self._fill_preview,
            TAB_FILES: self._fill_files,
        }.get(index, lambda: None)()

    def _scope(self, combo: QComboBox) -> Optional[date]:
        """Selected day when the combo says so (falls back to all days)."""
        return self._current_day if combo.currentText() == SCOPE_DAY else None

    def tab_badge_counts(self) -> Dict[int, int]:
        """What each tab's bubble shows: work left to do (skipped rows for information)."""
        result, session = self._validation, self._session
        if result is None or session is None:
            return dict.fromkeys(_BADGE_TABS, 0)
        return {
            TAB_DAYS: result.blocked_count,
            TAB_ISSUES: len(result.description_issues()),
            TAB_TRUCKS: sum(d.invalid_truck_count for d in result.days),
            TAB_EXCEL: sum(1 for r in result.rows.values() if not r.ok and not r.ignored),
            TAB_SKIPPED: len(session.skipped),
        }

    def _update_tab_badges(self) -> None:
        bar = self._tabs.tabBar()
        tips = {
            TAB_DAYS: "day(s) blocked",
            TAB_ISSUES: "description(s) to map",
            TAB_TRUCKS: "row(s) with a wrong truck",
            TAB_EXCEL: "row(s) with a problem",
            TAB_SKIPPED: "Excel row(s) skipped when reading the files",
        }
        for index, count in self.tab_badge_counts().items():
            self._tabs.set_badge(index, count, _BADGE_GREY if index == TAB_SKIPPED else _BADGE_RED)
            bar.setTabToolTip(index, f"{count:,} {tips[index]}" if count else "")

    def _update_banner(self) -> None:
        session, result = self._session, self._validation
        if session is None:
            self._banner.setText("Loading…")
            self._banner.show()
            return
        if not session.rows and not session.skipped:
            self._banner.hide()
            return
        self._banner.show()
        tzs = sum(r.amount for r in session.rows if r.currency == "TZS")
        usd = sum(r.amount_usd or 0.0 for r in session.rows)
        parts = [
            f"<b>{session.name}</b>",
            f"{len(session.files)} file(s)",
            f"{len(session.days())} day(s)",
        ]
        if result is not None:
            parts += [
                f"<span style='color:#15803D'><b>{result.ready_count}</b> Ready</span>",
                f"<span style='color:#B91C1C'><b>{result.blocked_count}</b> Blocked</span>",
                f"<b>{result.pushed_count}</b> Pushed",
            ]
        else:
            parts.append("checking…")
        parts += [
            f"{len(session.rows):,} rows",
            f"TZS {tzs:,.0f}",
        ]
        if usd:
            parts.append(f"USD {usd:,.2f}")
        parts.append(f"{len(session.skipped):,} skipped")
        mappings = [f"Mappings: <b>{_MAPPING_MODE_LABELS[session.options.mapping_mode]}</b>"]
        if session.options.mapping_mode != MODE_MAPS_ONLY:
            if session.pack_mappings:
                mappings.append(
                    f"pack '{session.pack_name}' ({len(session.pack_mappings):,}) "
                    "instead of built-in"
                )
            elif self._refs is not None:
                mappings.append(f"built-in {len(self._refs.builtin_mappings):,}")
        if session.options.mapping_mode != MODE_BUILTIN_ONLY and self._refs is not None:
            mappings.append(f"Description Maps {len(self._refs.global_mappings):,}")
        mappings.append(f"typed here {len(session.temp_mappings):,}")
        line = ", ".join(mappings) + f" · {len(session.truck_fixes):,} row truck fix(es)"
        if result is not None:
            by_source = {source: 0 for source in SOURCE_LABELS}
            for res in result.rows.values():
                mapped = not ({ISSUE_UNMAPPED, ISSUE_BAD_ITEM, ISSUE_BAD_PAYEE} & set(res.issues))
                if mapped and res.mapping_source in by_source:
                    by_source[res.mapping_source] += 1
            line += " · Rows mapped: " + ", ".join(
                f"{by_source[source]:,} {label}" for source, label in SOURCE_LABELS.items()
            )
        self._banner.setText(" · ".join(parts) + f"<br><span style='color:{_T2}'>{line}</span>")

    # -------------------------------------------------------------- day batches

    def _fill_days(self) -> None:
        rows: List[GridRow] = []
        if self._validation is not None:
            for day in self._validation.days:
                pushed = day.status == DAY_PUSHED
                tooltip = "\n".join(day.reasons) or "All rows mapped; trucks valid."
                if day.status == DAY_READY:
                    bg, status = READY_BG, DAY_READY
                elif pushed:
                    bg, status = PUSHED_BG, DAY_PUSHED
                    info = self._session.pushed_days.get(day.batch_date) if self._session else None
                    when = f" on {info.pushed_at:%d/%m/%Y %H:%M}" if info else ""
                    tooltip = (
                        f"Pushed to Master Expenses{when}. To push it again, click the "
                        "row and use Unmark Pushed."
                    )
                else:
                    bg, status = BLOCKED_BG, f"{DAY_BLOCKED} ({len(day.reasons)})"
                rows.append(
                    GridRow(
                        key=day.batch_date,
                        cells=[
                            day.batch_date,
                            _files_label(day.source_files),
                            day.row_count,
                            day.push_count,
                            day.total_tzs,
                            day.total_usd,
                            status,
                        ],
                        cell_colors={6: bg},
                        tooltip=tooltip,
                        checkable=not pushed,
                    )
                )
        current = self._current_day
        self._days_model.set_rows(rows)
        self._days_view.fit_columns()
        if current is not None and current in self._days_model.keys():
            self._days_view.select_key(current)
        elif rows:
            first_ready = next((r.key for r in rows if r.cells[6] == DAY_READY), rows[0].key)
            self._days_view.select_key(first_ready)
        self._update_ticked_label()

    def _on_day_selected(self, key) -> None:
        if key == self._current_day:
            return
        self._current_day = key
        self._dirty |= {TAB_ISSUES, TAB_TRUCKS, TAB_EXCEL, TAB_SKIPPED, TAB_PREVIEW}

    def _on_day_double_clicked(self, _index) -> None:
        day = self._validation.day(self._current_day) if self._validation and self._current_day else None
        if day is None:
            return
        if day.status == DAY_BLOCKED:
            self._issues_scope.setCurrentText(SCOPE_DAY)
            self._tabs.setCurrentIndex(TAB_ISSUES)
        else:
            self._preview_scope.setCurrentText(SCOPE_DAY)
            self._tabs.setCurrentIndex(TAB_PREVIEW)

    def _on_tick_ready(self) -> None:
        if self._validation is not None:
            self._days_model.set_checked(self._validation.ready_dates(), True)

    def _update_ticked_label(self) -> None:
        ticked = self._days_model.checked_keys()
        if not ticked:
            self._ticked_label.setText("No days ticked")
            return
        rows = 0
        if self._validation is not None:
            rows = sum(
                d.push_count for d in self._validation.days if d.batch_date in set(ticked)
            )
        self._ticked_label.setText(f"{len(ticked)} day(s) ticked · {rows:,} row(s)")

    def ticked_days(self) -> List[date]:
        return list(self._days_model.checked_keys())

    # -------------------------------------------------------------------- push

    def _on_push(self) -> None:
        if self._session is None or self._validation is None:
            return
        ticked = self.ticked_days()
        if not ticked:
            show_info(
                self,
                "Push to Master",
                "Tick the days to push first (Tick All Ready ticks every Ready day).",
            )
            return
        ready = [d for d in ticked if self._validation.day(d) and self._validation.day(d).is_ready]
        left_out = [d for d in ticked if d not in ready]
        if not ready:
            show_info(
                self,
                "Push to Master",
                "None of the ticked days is Ready. Fix their issues first, or "
                "Unmark Pushed to push a day again.",
            )
            return
        if left_out:
            listing = ", ".join(_fmt_day(d) for d in left_out[:10])
            more = f" and {len(left_out) - 10} more" if len(left_out) > 10 else ""
            answer = show_question(
                self,
                "Push to Master",
                f"{len(left_out)} ticked day(s) are not Ready and will be left out:\n"
                f"{listing}{more}\n\nPush the {len(ready)} Ready day(s)?",
                default_no=False,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._start(self._push(ready))

    async def _push(self, days: List[date]) -> None:
        session, validation = self._session, self._validation
        if session is None or validation is None:
            return
        try:
            groups = build_push_groups(session, validation, days)
        except DayNotReady as exc:
            show_warning(
                self,
                "Push to Master",
                "These days are no longer Ready: "
                + ", ".join(_fmt_day(d) for d in exc.days),
            )
            return
        if not groups:
            show_info(self, "Push to Master", "Every row of those days is ignored; nothing to push.")
            return

        with pause_background_polls(self):
            show_widget_loading(self, "Checking Browse → Uploads…")
            check_error = ""
            try:
                prior = await find_prior_uploads(groups)
            except Exception as exc:
                prior, check_error = {}, str(exc) or exc.__class__.__name__
            finally:
                hide_widget_loading(self)

            groups = self._confirm_push(groups, prior, check_error)
            if not groups:
                return

            summary = None
            try:
                with UploadBusy(
                    self, "Pushing to Master Expenses…", title="Import Daily", cancellable=True
                ) as busy:
                    await busy.tick()

                    async def on_progress(index: int, total: int, group: PushGroup) -> None:
                        busy.update(
                            f"{_fmt_day(group.batch_date)} · {group.upload_name}\n"
                            f"({index} of {total}, {len(group.payloads):,} rows)",
                            value=index - 1,
                            maximum=total,
                        )
                        await asyncio.sleep(0)

                    summary = await push_groups(
                        session,
                        groups,
                        on_progress=on_progress,
                        should_cancel=busy.should_cancel,
                    )
            finally:
                self._days_model.clear_checks()
                self._changed()
            self._report_push(summary, groups)

    def _confirm_push(
        self,
        groups: List[PushGroup],
        prior: Dict[date, List[PriorUpload]],
        check_error: str,
    ) -> List[PushGroup]:
        """Ask before pushing; drop days the user chooses to skip."""
        days = sorted({g.batch_date for g in groups})
        clashes = {d: [p for p in prior.get(d, []) if not p.same_batch] for d in days}
        clashes = {d: ps for d, ps in clashes.items() if ps}
        repeats = [d for d in days if any(p.same_batch for p in prior.get(d, []))]

        rows = sum(len(g.payloads) for g in groups)
        tzs = sum(g.total_tzs for g in groups)
        usd = sum(g.total_usd for g in groups)
        names = [f"  · {_fmt_day(g.batch_date)}  {g.upload_name}  ({len(g.payloads):,} rows)" for g in groups]
        listing = "\n".join(names[:12]) + (f"\n  · …and {len(names) - 12} more" if len(names) > 12 else "")
        money = f"TZS {tzs:,.0f}" + (f" · USD {usd:,.2f}" if usd else "")
        text = (
            f"Push {len(days)} day(s), {rows:,} row(s) ({money}) to Master Expenses as "
            f"verified?\n\nBrowse → Uploads will list:\n{listing}"
        )
        if repeats:
            text += (
                f"\n\n{len(repeats)} day(s) were pushed before from an import session; "
                "rows already there are skipped."
            )
        if check_error:
            text += f"\n\nCould not check Browse → Uploads for earlier imports:\n{check_error}"
        text += self._visibility_warnings(groups)

        if not clashes:
            answer = show_question(self, "Push to Master", text, default_no=False)
            return groups if answer == QMessageBox.StandardButton.Yes else []

        lines = []
        for d, ps in sorted(clashes.items()):
            for p in ps:
                lines.append(f"  · {_fmt_day(d)}: {p.source_filename or '(no name)'} ({p.row_count:,} rows)")
        clash_listing = "\n".join(lines[:12]) + (
            f"\n  · …and {len(lines) - 12} more" if len(lines) > 12 else ""
        )
        text += (
            f"\n\n{len(clashes)} day(s) already have a different upload in Browse → Uploads "
            f"(for example from the single-file import):\n{clash_listing}\n\n"
            "Pushing those days again may duplicate their rows."
        )
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            "Push to Master",
            text,
            QMessageBox.StandardButton.NoButton,
            self,
        )
        skip = box.addButton("Skip Those Days", QMessageBox.ButtonRole.AcceptRole)
        push_all = box.addButton("Push All", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(skip)
        style_message_box(box)
        box.exec()
        clicked = box.clickedButton()
        if clicked is push_all:
            return groups
        if clicked is skip:
            kept = [g for g in groups if g.batch_date not in clashes]
            if not kept:
                show_info(self, "Push to Master", "Nothing left to push.")
            return kept
        return []

    def _visibility_warnings(self, groups: List[PushGroup]) -> str:
        """Rows that will be pushed but not show where the user expects in Master."""
        text = ""
        outliers = date_outliers(groups)
        if outliers:
            rows = sum(o.rows for o in outliers)
            listing = "\n".join(
                f"  · {_fmt_day(o.batch_date)} file: {o.rows:,} row(s) dated {_fmt_day(o.row_date)}"
                for o in outliers[:8]
            ) + (f"\n  · …and {len(outliers) - 8} more" if len(outliers) > 8 else "")
            text += (
                f"\n\n{rows:,} row(s) have an Excel date in another month than their day. "
                "Master Expenses lists rows by that date, so they will show under that "
                f"month / year (check the Excel dates if that is a typo):\n{listing}"
            )
        if self._refs is not None:
            suppliers = supplier_rows(groups, self._refs.is_supplier)
            if suppliers:
                rows = sum(suppliers.values())
                names = ", ".join(
                    f"{name} ({n:,})"
                    for name, n in sorted(suppliers.items(), key=lambda kv: -kv[1])[:6]
                )
                text += (
                    f"\n\n{rows:,} row(s) use supplier items ({names}). Supplier payments are "
                    "saved but never listed in Master Expenses — only in Browse → Uploads."
                )
        return text

    def _report_push(self, summary, groups: List[PushGroup]) -> None:
        if summary is None:
            return
        day_count = len({g.batch_date for g in groups})
        pushed_days = {d.batch_date for d in summary.days}
        pushed_groups = [g for g in groups if g.batch_date in pushed_days]
        pushed = len(summary.days)
        lines = [f"Pushed {pushed} of {day_count} day(s) to Master Expenses."]
        span = row_date_range(pushed_groups)
        if pushed:
            lines.append(f"{summary.inserted:,} row(s) added.")
            if summary.duplicates_skipped:
                lines.append(f"{summary.duplicates_skipped:,} row(s) were already there (skipped).")
            if summary.skipped_no_item:
                lines.append(f"{summary.skipped_no_item:,} row(s) skipped (no item).")
            if span is not None:
                lines.append(
                    f"In Master Expenses they are dated {_fmt_day(span[0])} – {_fmt_day(span[1])}."
                )
            lines.append("They are listed in Browse → Uploads under their MATUMIZI file names.")
            warnings = self._visibility_warnings(pushed_groups)
            if warnings:
                lines.append(warnings.lstrip("\n"))
        if summary.failed_day is not None:
            lines.append(
                f"\nStopped at {_fmt_day(summary.failed_day)}:\n{summary.error}\n\n"
                "Days before it are saved. Push the rest again — rows already in "
                "Master are skipped."
            )
        elif summary.cancelled:
            lines.append(f"\nCancelled — {day_count - pushed} day(s) not pushed.")
        can_open = bool(pushed) and span is not None
        if _push_report_box(
            self, "\n".join(lines), warning=summary.failed_day is not None, can_open=can_open
        ) and can_open:
            self.open_master_dates.emit(span[0], span[1])

    def _on_unmark_pushed(self) -> None:
        if self._session is None:
            return
        keys = self._days_view.acted_keys()
        days = [d for d in keys if self._session.is_day_pushed(d)]
        if not days:
            show_info(self, "Unmark Pushed", "Click Pushed days to highlight them first.")
            return
        answer = show_question(
            self,
            "Unmark Pushed",
            f"Mark {len(days)} day(s) as not pushed?\n\n"
            "Their rows stay in Master Expenses. Pushing them again adds only rows "
            "that are not there yet.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for d in days:
            self._session.unmark_day_pushed(d)
        self._days_model.clear_checks()
        self._changed()

    # ------------------------------------------------------------ mapping pack

    def _on_load_pack(self) -> None:
        if self._session is None or self._refs is None:
            show_info(self, "Load mapping pack", "Lists are still loading — try again in a moment.")
            return
        dlg = LoadPackDialog(self)
        if dlg.exec() != LoadPackDialog.Accepted or dlg.choice is None:
            return
        choice = dlg.choice
        off = (
            "\n\nMappings is set to 'Description Maps only', so built-in / pack mappings "
            "are not used until you change it."
            if self._session.options.mapping_mode == MODE_MAPS_ONLY
            else ""
        )
        if choice.builtin:
            self._session.use_builtin_mappings()
            self._changed()
            show_info(
                self,
                "Load mapping pack",
                f"'{self._session.name}' uses the {len(self._refs.builtin_mappings):,} "
                f"built-in mappings.{off}",
            )
            return
        result = apply_mapping_pack(self._session, choice.pack, self._refs)
        self._changed()

        lines = [
            f"'{self._session.name}' now uses pack '{self._session.pack_name}' instead of "
            "the built-in mappings:",
            f"  · {result.mappings_added:,} description mapping(s), "
            f"{result.mappings_in_session:,} used by this session's rows",
            "  · Mappings you typed here are kept and still win",
        ]

        def top(counter, label: str) -> None:
            if not counter:
                return
            names = ", ".join(f"{n} ({c})" for n, c in counter.most_common(8))
            more = f" and {len(counter) - 8} more" if len(counter) > 8 else ""
            lines.append(f"\n{label} not in our lists ({len(counter):,}): {names}{more}")

        top(result.unresolved_items, "Accounts")
        top(result.unresolved_payees, "Payees")
        if result.unresolved_items or result.unresolved_payees:
            lines.append(
                "\nDescriptions using those show as 'Bad map' in Day Issues — map them there."
            )
        show_info(self, "Load mapping pack", "\n".join(lines) + off)

    # ------------------------------------------------------------- finish session

    def _promote_candidates(self) -> List[PromoteCandidate]:
        """Session-only typed mappings whose Item and Payee exist, to save for good."""
        session, refs = self._session, self._refs
        if session is None or refs is None:
            return []
        rows_by_key: Dict[str, int] = {}
        for row in session.rows:
            key = normalize_description(row.description)
            if key not in session.ignored:
                rows_by_key[key] = rows_by_key.get(key, 0) + 1
        out: List[PromoteCandidate] = []
        for key, mapping in session.temp_mappings.items():
            item = refs.items.resolve(mapping.item)
            payee = refs.payees.resolve(mapping.payee)
            if item is None or item.id is None or payee is None:
                continue
            current = refs.global_mappings.get(key)
            current_text = ""
            same = False
            if current is not None:
                current_item = refs.items.by_id(current.category_id)
                current_name = current_item.name if current_item else (current.category_name or "")
                current_text = f"{(current.payee or '').upper()} · {current_name.upper()}"
                same = current_name.upper() == item.name and (current.payee or "").upper() == payee
            out.append(
                PromoteCandidate(
                    key=key,
                    description=mapping.description,
                    payee=payee,
                    item=item.name,
                    source="Typed here",
                    rows=rows_by_key.get(key, 0),
                    current=current_text,
                    ticked=not same,
                )
            )
        out.sort(key=lambda c: (-c.rows, c.description))
        return out

    def _on_finish_session(self) -> None:
        if self._session is None:
            return
        if self._refs is None:
            show_info(self, "Finish session", "Lists are still loading — try again in a moment.")
            return
        pending = 0
        if self._validation is not None:
            pending = self._validation.ready_count + self._validation.blocked_count
        note = (
            f"{pending} day(s) in this session are not pushed yet — their rows will be discarded."
            if pending
            else ""
        )
        pushed = self._session.pushed_days
        pushed_note = ""
        if pushed:
            rows = sum(p.row_count for p in pushed.values())
            pushed_note = (
                f"{len(pushed)} day(s) already pushed ({rows:,} row(s)) are saved in Master "
                "Expenses. Finishing does not remove them, whichever button you press."
            )
        dlg = FinishSessionDialog(
            self._promote_candidates(), note=note, pushed_note=pushed_note, parent=self
        )
        if dlg.exec() != FinishSessionDialog.Accepted:
            return
        self._start(self._finish(dlg.promote_keys))

    async def _finish(self, promote_keys: List[str]) -> None:
        session, refs = self._session, self._refs
        if session is None or refs is None:
            return
        saved = 0
        if promote_keys:
            from tahmeed.services.description_mapping_service import save_mappings_bulk

            entries = []
            for key in promote_keys:
                mapping = session.temp_mappings.get(key)
                item = refs.items.resolve(mapping.item) if mapping else None
                payee = refs.payees.resolve(mapping.payee) if mapping else None
                if mapping is None or item is None or item.id is None or payee is None:
                    continue
                entries.append((mapping.description, item.id, item.name, payee))
            with pause_background_polls(self):
                show_widget_loading(self, f"Saving {len(entries):,} mapping(s) to Description Maps…")
                try:
                    saved_maps = await save_mappings_bulk(entries)
                except Exception as exc:
                    hide_widget_loading(self)
                    show_critical(
                        self,
                        "Finish session",
                        f"Could not save to Description Maps — the session was kept.\n\n{exc}",
                    )
                    return
                hide_widget_loading(self)
            for mapping in saved_maps:
                key = (mapping.description_key or "").strip()
                if key:
                    refs.global_mappings[key] = mapping
            saved = len(saved_maps)
        name = session.name
        pushed = len(session.pushed_days)
        delete_session(session.session_id)
        self._set_session(self._new_session())
        message = f"Session '{name}' finished."
        if pushed:
            message += f"\n\nIts {pushed} pushed day(s) stay in Master Expenses."
        if saved:
            message += f"\n\n{saved:,} mapping(s) saved to Description Maps."
        show_info(self, "Finish session", message)

    # --------------------------------------------------------------- day issues

    def _issue_values(self, issue: DescriptionIssue) -> tuple[str, str]:
        draft = self._issue_drafts.get(issue.key)
        if not draft:
            return issue.payee, issue.item
        return draft.get("payee", issue.payee), draft.get("item", issue.item)

    def _issue_row(self, issue: DescriptionIssue) -> GridRow:
        payee, item = self._issue_values(issue)
        colors = {0: UNMAPPED_BG if issue.kind == KIND_UNMAPPED else BLOCKED_BG}
        if issue.kind == KIND_IGNORED:
            colors = {0: IGNORED_BG}
        elif issue.key in self._issue_drafts:
            colors.update({_ISSUE_PAYEE_COL: DRAFT_BG, _ISSUE_ITEM_COL: DRAFT_BG})
        source = SOURCE_LABELS.get(issue.source, "")
        return GridRow(
            key=issue.key,
            cells=[
                issue.kind,
                issue.description,
                issue.row_count,
                issue.day_count,
                payee,
                item,
                source,
                issue.detail,
            ],
            cell_colors=colors,
            editable=set() if issue.kind == KIND_IGNORED else {_ISSUE_PAYEE_COL, _ISSUE_ITEM_COL},
        )

    def _fill_issues(self) -> None:
        self._issues_by_key = {}
        if self._validation is not None:
            for issue in self._validation.description_issues(self._scope(self._issues_scope)):
                self._issues_by_key[issue.key] = issue
        self._issues_model.set_rows([self._issue_row(i) for i in self._issues_by_key.values()])
        self._issues_view.fit_columns(min_widths={_ISSUE_PAYEE_COL: 160, _ISSUE_ITEM_COL: 200})
        self._update_issues_counter()

    def _update_issues_counter(self) -> None:
        issues = list(self._issues_by_key.values())
        unmapped = sum(1 for i in issues if i.kind == KIND_UNMAPPED)
        bad = sum(1 for i in issues if i.kind == KIND_BAD_MAP)
        filled = sum(
            1 for i in issues if i.key in self._issue_drafts and all(self._issue_values(i))
        )
        scope = self._scope(self._issues_scope)
        where = _fmt_day(scope) if scope else "all days"
        self._issues_counter.setText(
            f"{where}: {unmapped:,} unmapped · {bad:,} bad map · {filled:,} filled, ready to save"
        )

    def _set_issue_draft(self, key: str, cell: int, value: str) -> None:
        issue = self._issues_by_key.get(key)
        if issue is None:
            return
        draft = self._issue_drafts.setdefault(key, {"payee": issue.payee, "item": issue.item})
        draft["payee" if cell == _ISSUE_PAYEE_COL else "item"] = value
        row = self._issue_row(issue)
        self._issues_model.update_row(key, cells=row.cells, cell_colors=row.cell_colors)
        self._update_issues_counter()

    def _on_issue_cell_edited(self, key, cell: int, text: str) -> None:
        issue = self._issues_by_key.get(key)
        if issue is None or self._refs is None:
            return
        text = " ".join(text.upper().split())
        current = self._issue_values(issue)[0 if cell == _ISSUE_PAYEE_COL else 1]
        if text == " ".join((current or "").upper().split()):
            return  # tabbed through without changing it
        if not text:
            self._set_issue_draft(key, cell, "")
            return
        if cell == _ISSUE_PAYEE_COL:
            known = self._refs.payees.resolve(text)
        else:
            ref = self._refs.items.resolve(text)
            known = ref.name if ref else None
        self._set_issue_draft(key, cell, known or text)
        if known is None:
            # Ask before the next box opens, like the cashier Day Issues.
            self._issues_entry.pause()
            QTimer.singleShot(0, lambda: self._offer_new_name(key, cell, text))

    def _offer_new_name(self, key: str, cell: int, text: str) -> None:
        try:
            if cell == _ISSUE_PAYEE_COL:
                payee = ask_new_payee(self, text)
                if payee:
                    self._start(self._create_name(key, cell, payee=payee))
                    return
            else:
                fields = ask_new_item(self, text)
                if fields:
                    self._start(self._create_name(key, cell, item_fields=fields))
                    return
            self._set_issue_draft(key, cell, "")
        finally:
            self._issues_entry.resume()

    async def _create_name(
        self,
        key: str,
        cell: int,
        *,
        payee: Optional[tuple] = None,
        item_fields: Optional[dict] = None,
    ) -> None:
        """Add a new Payee / Item on the server, then put it in the typed cell."""
        refs = self._refs
        if refs is None:
            return
        from tahmeed.services import mapping_assignment_service, payee_service
        from tahmeed.services.import_session_validation import ItemRef

        with pause_background_polls(self):
            show_widget_loading(self, "Adding Payee…" if payee else "Adding Item…")
            try:
                if payee:
                    name, currency = payee
                    await payee_service.add_payee(name, currency=currency or "TZS")
                    value = refs.payees.add(name) or name
                else:
                    name = str(item_fields.get("name") or "").strip()
                    category = await mapping_assignment_service.create_item_for_mapping(
                        name, item_fields
                    )
                    ref = refs.items.add(ItemRef(category._id, category.name))
                    value = ref.name if ref else category.name
            except Exception as exc:
                hide_widget_loading(self)
                self._set_issue_draft(key, cell, "")
                show_critical(self, "Add new name", f"Could not add it:\n\n{exc}")
                return
            hide_widget_loading(self)
        self._set_issue_draft(key, cell, " ".join(str(value).upper().split()))

    def _acted_issues(self, kinds) -> List[DescriptionIssue]:
        out = []
        for key in self._issues_view.acted_keys():
            issue = self._issues_by_key.get(key)
            if issue is not None and issue.kind in kinds:
                out.append(issue)
        return out

    def _open_map_dialog(self, issues: List[DescriptionIssue]) -> None:
        if self._refs is None or self._session is None:
            return
        if not issues:
            show_info(self, "Map descriptions", "Tick or highlight Unmapped / Bad map rows first.")
            return
        items = {i.item for i in issues}
        payees = {i.payee for i in issues}
        refs = self._refs
        dlg = MapDescriptionsDialog(
            [i.description for i in issues],
            item_names=refs.items.names(),
            payee_names=refs.payees.names(),
            resolve_item=lambda v: (refs.items.resolve(v).name if refs.items.resolve(v) else None),
            resolve_payee=refs.payees.resolve,
            initial_item=items.pop() if len(items) == 1 else "",
            initial_payee=payees.pop() if len(payees) == 1 else "",
            save_global=self._save_global_box.isChecked(),
            parent=self,
        )
        if dlg.exec() != MapDescriptionsDialog.Accepted or dlg.choice is None:
            return
        choice = dlg.choice
        self._save_global_box.setChecked(choice.save_global)
        self._issues_model.clear_checks()
        if choice.rows:
            rows, new_items, new_payees = choice.rows, choice.new_items, choice.new_payees
        else:
            rows = [(d, choice.item, choice.payee) for d in choice.descriptions]
            new_items = {choice.item: choice.new_item} if choice.new_item else {}
            new_payees = dict([choice.new_payee]) if choice.new_payee else {}
        self._start(
            self._save_mappings(
                rows,
                new_items=new_items,
                new_payees=new_payees,
                save_global=choice.save_global,
            )
        )

    def _on_map_selected(self) -> None:
        self._open_map_dialog(self._acted_issues({KIND_UNMAPPED, KIND_BAD_MAP}))

    def _on_map_all(self) -> None:
        issues = [i for i in self._issues_by_key.values() if i.kind == KIND_UNMAPPED]
        self._open_map_dialog(issues)

    def _on_save_filled(self) -> None:
        if self._session is None or self._refs is None:
            return
        filled = []
        for key, draft in self._issue_drafts.items():
            issue = self._issues_by_key.get(key)
            if issue is None or not draft.get("payee") or not draft.get("item"):
                continue
            filled.append((key, issue.description, draft["item"], draft["payee"]))
        if not filled:
            show_info(
                self,
                "Save Filled Mappings",
                "No rows have both PAYEE and ITEM typed in yet.\n\n"
                "Double-click the PAYEE / ITEM cells of a row to fill them.",
            )
            return
        # Names not in the lists: offer to add each one (Cancel stops the save).
        new_payees: Dict[str, str] = {}
        new_items: Dict[str, dict] = {}
        typed_payee: Dict[str, str] = {}  # typed name -> name it was added as
        typed_item: Dict[str, str] = {}
        rows = []
        for _key, desc, item, payee in filled:
            if self._refs.payees.resolve(payee) is None:
                if payee not in typed_payee:
                    created = ask_new_payee(self, payee)
                    if created is None:
                        return
                    new_payees[created[0]] = created[1]
                    typed_payee[payee] = created[0]
                payee = typed_payee[payee]
            if self._refs.items.resolve(item) is None:
                if item not in typed_item:
                    fields = ask_new_item(self, item)
                    if fields is None:
                        return
                    name = str(fields.get("name") or item).strip()
                    new_items[name] = fields
                    typed_item[item] = name
                item = typed_item[item]
            rows.append((desc, item, payee))
        self._start(
            self._save_mappings(
                rows,
                new_items=new_items,
                new_payees=new_payees,
                save_global=self._save_global_box.isChecked(),
            )
        )

    async def _save_mappings(
        self,
        rows: List[tuple],
        *,
        new_items: Dict[str, dict],
        new_payees: Dict[str, str],
        save_global: bool,
    ) -> None:
        """Create new Payees / Items on the server, then save ``(description, item, payee)``.

        ``save_global`` writes Description Maps (permanent, every PC) and drops any
        temporary mapping for those descriptions; otherwise they stay temporary.
        """
        session, refs = self._session, self._refs
        if session is None or refs is None or not rows:
            return
        from tahmeed.services import description_mapping_service, mapping_assignment_service, payee_service
        from tahmeed.services.import_session_validation import ItemRef

        saved_global = 0
        with pause_background_polls(self):
            show_widget_loading(self, "Saving mappings…")
            try:
                for n, (name, currency) in enumerate(new_payees.items(), 1):
                    if len(new_payees) > 1:
                        show_widget_loading(self, f"Adding Payees {n:,}/{len(new_payees):,}…")
                    await payee_service.add_payee(name, currency=currency or "TZS")
                    refs.payees.add(name)
                for n, (name, fields) in enumerate(new_items.items(), 1):
                    if len(new_items) > 1:
                        show_widget_loading(self, f"Adding Items {n:,}/{len(new_items):,}…")
                    category = await mapping_assignment_service.create_item_for_mapping(name, fields)
                    refs.items.add(ItemRef(category._id, category.name))
                show_widget_loading(self, "Saving mappings…")

                entries = []
                for desc, item_name, payee_name in rows:
                    item = refs.items.resolve(item_name)
                    payee = refs.payees.resolve(payee_name)
                    if save_global and item is not None and item.id is not None and payee:
                        entries.append((desc, item.id, item.name, payee))
                    else:
                        session.set_mapping(desc, item.name if item else item_name, payee or payee_name)
                if entries:
                    saved = await description_mapping_service.save_mappings_bulk(entries)
                    for mapping in saved:
                        if mapping.description_key:
                            refs.global_mappings[mapping.description_key] = mapping
                    for desc, *_rest in entries:
                        session.remove_mapping(desc)
                    session.note_saved_to_maps(desc for desc, *_rest in entries)
                    saved_global = len(saved)
            except Exception as exc:
                hide_widget_loading(self)
                show_critical(self, "Save mappings", f"Could not save:\n\n{exc}")
                return
            finally:
                for desc, *_rest in rows:
                    self._issue_drafts.pop(normalize_description(desc), None)
                self._changed()
            hide_widget_loading(self)

        def listed(names) -> str:
            names = list(names)
            more = f" … and {len(names) - 8:,} more" if len(names) > 8 else ""
            return ", ".join(names[:8]) + more

        created = []
        if new_payees:
            created.append(f"{len(new_payees)} new Payee(s): " + listed(new_payees))
        if new_items:
            created.append(f"{len(new_items)} new Item(s): " + listed(new_items))
        if created or saved_global:
            lines = created + [f"{len(rows):,} description(s) mapped."] + (
                [f"{saved_global:,} mapping(s) saved to Description Maps."] if saved_global else []
            )
            show_info(self, "Save mappings", "\n".join(lines))

    def _on_remove_temp_mapping(self) -> None:
        if self._session is None:
            return
        issues = self._acted_issues({KIND_BAD_MAP})
        removed = sum(1 for i in issues if self._session.remove_mapping(i.description))
        if not removed:
            show_info(
                self,
                "Remove Temp Mapping",
                "Tick or highlight Bad map rows that come from a temporary mapping.",
            )
            return
        self._changed()

    # ------------------------------------------------------------- truck issues

    def _truck_choices(self) -> List[str]:
        if self._refs is None:
            return []
        return self._refs.fleet_names() + list(self._refs.place_labels)

    def _truck_row(self, issue: TruckRowIssue) -> GridRow:
        draft = self._truck_drafts.get(issue.row_id)
        if issue.invalid:
            status, color = ("Wrong truck", BLOCKED_BG)
        elif issue.fix == "":
            status, color = ("No truck", READY_BG)
        elif issue.allowed:
            status, color = ("Allowed", READY_BG)
        else:
            status, color = ("Fixed", READY_BG)
        colors = {0: color}
        if draft is not None:
            colors[_TRUCK_FIX_COL] = DRAFT_BG
        amount = f"{issue.amount:,.2f} {issue.currency}".strip()
        return GridRow(
            key=issue.row_id,
            cells=[
                status,
                issue.batch_date,
                issue.source_file,
                issue.excel_row,
                issue.description,
                amount,
                issue.truck_raw,
                draft if draft is not None else (issue.fix or ""),
            ],
            cell_colors=colors,
            editable={_TRUCK_FIX_COL},
        )

    def _fill_trucks(self) -> None:
        self._truck_rows_by_id = {}
        if self._validation is not None and self._session is not None:
            for issue in self._validation.truck_rows(
                self._scope(self._trucks_scope),
                self._session.truck_fixes,
                self._session.truck_allowed,
            ):
                self._truck_rows_by_id[issue.row_id] = issue
        self._trucks_model.set_rows([self._truck_row(i) for i in self._truck_rows_by_id.values()])
        self._trucks_view.fit_columns(min_widths={_TRUCK_FIX_COL: 160})
        self._update_trucks_counter()

    def _update_trucks_counter(self) -> None:
        issues = list(self._truck_rows_by_id.values())
        wrong = sum(1 for i in issues if i.invalid)
        typed = sum(1 for i in issues if i.row_id in self._truck_drafts)
        scope = self._scope(self._trucks_scope)
        where = _fmt_day(scope) if scope else "all days"
        self._trucks_counter.setText(
            f"{where}: {wrong:,} row(s) with a wrong truck · {len(issues) - wrong:,} fixed · "
            f"{typed:,} typed, ready to save"
        )

    def _set_truck_draft(self, row_id: str, plate: Optional[str], *, allowed: bool = False) -> None:
        issue = self._truck_rows_by_id.get(row_id)
        if issue is None:
            return
        if plate:
            self._truck_drafts[row_id] = plate
        else:
            self._truck_drafts.pop(row_id, None)
        if plate and allowed:
            self._truck_draft_allowed.add(row_id)
        else:
            self._truck_draft_allowed.discard(row_id)
        row = self._truck_row(issue)
        self._trucks_model.update_row(row_id, cells=row.cells, cell_colors=row.cell_colors)
        self._update_trucks_counter()

    def _on_truck_cell_edited(self, key, _cell: int, text: str) -> None:
        issue = self._truck_rows_by_id.get(key)
        if issue is None or self._refs is None:
            return
        text = " ".join(text.upper().split())
        shown = self._truck_drafts.get(key, issue.fix or "")
        if text == shown:
            return  # tabbed through without changing it
        if not text:
            self._set_truck_draft(key, None)
            return
        plate = is_fleet_truck(text, self._refs)
        self._set_truck_draft(key, plate)
        if plate is None:
            self._trucks_entry.pause()
            QTimer.singleShot(0, lambda: self._offer_allow_truck(key, text))

    def _offer_allow_truck(self, row_id: str, text: str) -> None:
        try:
            answer = show_question(
                self,
                "Not one of our trucks",
                f"'{text}' is not in our Trucks, Trailers or Motorcycles & Cars.\n\n"
                "Allow it anyway on this row? Choose No to pick a plate from the list.",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._set_truck_draft(row_id, text, allowed=True)
        finally:
            self._trucks_entry.resume()

    def _wrong_trucks(self) -> List[WrongTruck]:
        """Wrong Excel truck values in the tab's scope, each with its rows."""
        groups: Dict[str, WrongTruck] = {}
        for issue in self._truck_rows_by_id.values():
            if not issue.invalid:
                continue
            key = truck_key(issue.truck_raw)
            group = groups.get(key)
            if group is None:
                group = groups[key] = WrongTruck(value=key, row_ids=[], examples=[])
            group.row_ids.append(issue.row_id)
            if len(group.examples) < 3:
                amount = f"{issue.amount:,.2f} {issue.currency}".strip()
                group.examples.append(
                    f"{_fmt_day(issue.batch_date)} · {issue.description} · {amount} · "
                    f"{issue.source_file} row {issue.excel_row}"
                )
        return list(groups.values())

    def _apply_wrong_truck(self, wrong: WrongTruck, plate: str, allowed: bool) -> None:
        if self._session is None:
            return
        for row_id in wrong.row_ids:
            self._session.set_truck_fix(row_id, plate, allowed=allowed)
            self._truck_drafts.pop(row_id, None)
            self._truck_draft_allowed.discard(row_id)

    def _on_fix_trucks(self, start_value: str = "") -> None:
        if self._session is None or self._refs is None:
            return
        wrong = self._wrong_trucks()
        if not wrong:
            show_info(self, "Fix Trucks", "No wrong trucks left in this view.")
            return
        refs = self._refs
        dialog = TruckFixDialog(
            wrong,
            choices=self._truck_choices(),
            resolve=lambda text: is_fleet_truck(text, refs),
            on_apply=self._apply_wrong_truck,
            start_value=start_value,
            parent=self,
        )
        dialog.exec()
        if dialog.applied_count:
            self._changed()

    def _on_truck_double_clicked(self, index) -> None:
        if index.flags() & Qt.ItemIsEditable:
            return  # the CORRECT TRUCK cell opens its editor instead
        issue = self._truck_rows_by_id.get(self._trucks_view.source_key(index.row()))
        if issue is not None and issue.invalid:
            self._on_fix_trucks(truck_key(issue.truck_raw))

    def _acted_truck_rows(self) -> List[TruckRowIssue]:
        return [
            self._truck_rows_by_id[k]
            for k in self._trucks_view.acted_keys()
            if k in self._truck_rows_by_id
        ]

    def _on_save_truck_fixes(self) -> None:
        if self._session is None:
            return
        drafts = {k: v for k, v in self._truck_drafts.items() if k in self._truck_rows_by_id}
        if not drafts:
            show_info(
                self,
                "Save Truck Fixes",
                "No rows have a correct truck typed in yet.\n\n"
                "Click the CORRECT TRUCK cell of a row and pick a truck or trailer.",
            )
            return
        for row_id, plate in drafts.items():
            self._session.set_truck_fix(
                row_id, plate, allowed=row_id in self._truck_draft_allowed
            )
            self._truck_drafts.pop(row_id, None)
            self._truck_draft_allowed.discard(row_id)
        self._changed()

    def _on_set_no_truck(self) -> None:
        if self._session is None:
            return
        rows = self._acted_truck_rows()
        if not rows:
            show_info(self, "Set No Truck", "Tick or highlight the rows that have no truck.")
            return
        answer = show_question(
            self,
            "Set No Truck",
            f"Save {len(rows):,} row(s) with no truck?\n\n"
            "Only for expenses that are not for any truck or trailer.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for issue in rows:
            self._session.set_truck_fix(issue.row_id, "")
            self._truck_drafts.pop(issue.row_id, None)
            self._truck_draft_allowed.discard(issue.row_id)
        self._trucks_model.clear_checks()
        self._changed()

    def _on_remove_truck_fix(self) -> None:
        if self._session is None:
            return
        removed = 0
        for issue in self._acted_truck_rows():
            removed += self._session.remove_truck_fix(issue.row_id)
            self._truck_draft_allowed.discard(issue.row_id)
            if self._truck_drafts.pop(issue.row_id, None) is not None:
                removed += 1
        if not removed:
            show_info(
                self,
                "Remove Fix",
                "Tick or highlight fixed rows (or rows with a typed truck) to undo them.",
            )
            return
        self._trucks_model.clear_checks()
        self._changed()

    # ------------------------------------------------------------- excel issues

    def _excel_headers(self) -> List[str]:
        headers: List[str] = []
        for loaded in (self._session.files.values() if self._session else []):
            for header in loaded.headers:
                if header not in headers:
                    headers.append(header)
        return headers

    def _fill_excel(self) -> None:
        lead = ["DAY", "FILE", "XL ROW", "ISSUE", "PAYEE", "ITEM", "TRUCK"]
        headers = self._excel_headers()
        self._excel_model.set_headers(lead + headers, right_align=[2])
        rows: List[GridRow] = []
        problems_only = self._excel_filter.currentIndex() == 0
        scope = self._scope(self._excel_scope)
        if self._session is not None and self._validation is not None:
            files = self._session.files
            for row in self._session.rows:
                if scope is not None and row.batch_date != scope:
                    continue
                res = self._validation.rows.get(row.row_id)
                if res is None:
                    continue
                if problems_only and (res.ok or res.ignored):
                    continue
                loaded = files.get(row.file_key)
                cells = [
                    row.batch_date,
                    loaded.name if loaded else row.source_file,
                    row.excel_row,
                    issue_text(res) or "OK",
                    res.payee,
                    res.item,
                    res.truck,
                ] + [row.excel_cells.get(h, "") for h in headers]
                bg = IGNORED_BG if res.ignored else (READY_BG if res.ok else BLOCKED_BG)
                rows.append(GridRow(key=row.row_id, cells=cells, cell_colors={3: bg}))
        self._excel_model.set_rows(rows)
        self._excel_view.fit_columns(max_width=260)
        self._excel_count.setText(f"{len(rows):,} row(s)")

    # -------------------------------------------------------------- skipped rows

    def _fill_skipped(self, keep_reason: bool = False) -> None:
        session = self._session
        skipped = list(session.skipped) if session else []
        scope = self._scope(self._skipped_scope)
        if scope is not None:
            skipped = [s for s in skipped if s.batch_date == scope]
        if not keep_reason:
            current = self._skipped_reason.currentData()
            self._skipped_reason.blockSignals(True)
            self._skipped_reason.clear()
            self._skipped_reason.addItem("All reasons", None)
            for reason in sorted({s.reason for s in skipped}):
                self._skipped_reason.addItem(SKIP_REASON_LABELS.get(reason, reason), reason)
            index = self._skipped_reason.findData(current)
            self._skipped_reason.setCurrentIndex(max(index, 0))
            self._skipped_reason.blockSignals(False)
        reason = self._skipped_reason.currentData()
        if reason:
            skipped = [s for s in skipped if s.reason == reason]
        headers = self._excel_headers()
        self._skipped_model.set_headers(
            ["DAY", "FILE", "XL ROW", "REASON", "DETAIL"] + headers, right_align=[2]
        )
        files = session.files if session else {}
        rows = []
        for index, s in enumerate(skipped):
            loaded = files.get(s.file_key)
            rows.append(
                GridRow(
                    key=index,
                    cells=[
                        s.batch_date,
                        loaded.name if loaded else s.source_file,
                        s.excel_row,
                        s.reason_label,
                        s.detail,
                    ]
                    + [s.excel_cells.get(h, "") for h in headers],
                    cell_colors={3: BLOCKED_BG},
                )
            )
        self._skipped_model.set_rows(rows)
        self._skipped_view.fit_columns(max_width=260)
        self._skipped_count.setText(f"{len(rows):,} skipped row(s)")

    def _on_export_skipped(self) -> None:
        model = self._skipped_model
        if model.rowCount() == 0:
            show_info(self, "Export Skipped", "There are no skipped rows to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export skipped rows", "Skipped rows.xlsx", "Excel files (*.xlsx)"
        )
        if not path:
            return
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Skipped Rows"
        ws.append([model.headerData(c, Qt.Horizontal) for c in range(model.columnCount())])
        for r in range(model.rowCount()):
            ws.append([model.data(model.index(r, c)) for c in range(model.columnCount())])
        try:
            wb.save(path)
        except OSError as exc:
            show_critical(self, "Export Skipped", f"Could not save the file:\n\n{exc}")
            return
        finally:
            wb.close()
        show_info(self, "Export Skipped", f"Saved {model.rowCount():,} row(s) to\n{path}")

    # ------------------------------------------------------------------ preview

    def _fill_preview(self) -> None:
        rows: List[GridRow] = []
        scope = self._scope(self._preview_scope)
        if self._session is not None and self._validation is not None:
            for row in self._session.rows:
                if scope is not None and row.batch_date != scope:
                    continue
                res = self._validation.rows.get(row.row_id)
                if res is None:
                    continue
                tzs = row.amount if row.currency == "TZS" else None
                status = issue_text(res) or "OK"
                bg = IGNORED_BG if res.ignored else (READY_BG if res.ok else BLOCKED_BG)
                rows.append(
                    GridRow(
                        key=row.row_id,
                        cells=[
                            row.batch_date,
                            row.excel_date or row.batch_date,
                            res.payee,
                            res.item,
                            row.description,
                            res.truck,
                            tzs,
                            row.amount_usd,
                            row.memo,
                            row.source_file,
                            SOURCE_LABELS.get(res.mapping_source, ""),
                            "Not pushed (ignored)" if res.ignored else status,
                        ],
                        cell_colors={11: bg},
                    )
                )
        self._preview_model.set_rows(rows)
        self._preview_view.fit_columns(max_width=280)
        where = _fmt_day(scope) if scope else "all days"
        to_push = sum(1 for r in rows if r.background is None)
        self._preview_count.setText(f"{where}: {to_push:,} of {len(rows):,} row(s) ready")

    # -------------------------------------------------------------------- files

    def _fill_files(self) -> None:
        rows = []
        for loaded in (self._session.files.values() if self._session else []):
            rows.append(
                GridRow(
                    key=loaded.file_key,
                    cells=[
                        loaded.name,
                        loaded.sheet,
                        loaded.row_count,
                        loaded.skipped_count,
                        " | ".join(loaded.warnings),
                        loaded.path,
                    ],
                    tooltip="\n".join(loaded.warnings) or loaded.path,
                )
            )
        rows.sort(key=lambda r: str(r.cells[0]).upper())
        self._files_model.set_rows(rows)
        self._files_view.fit_columns(max_width=360)

    def _on_remove_files(self) -> None:
        if self._session is None:
            return
        keys = self._files_view.acted_keys()
        if not keys:
            show_info(self, "Remove files", "Tick or highlight the files to remove.")
            return
        answer = show_question(
            self,
            "Remove files",
            f"Remove {len(keys)} file(s) and their rows from this session?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for key in keys:
            self._session.remove_file(key)
        self._files_model.clear_checks()
        self._changed()
        self._fill_session_combo()

    def _on_reload_files(self) -> None:
        if self._session is None:
            return
        keys = self._files_view.acted_keys()
        paths = [self._session.files[k].path for k in keys if k in self._session.files]
        if not paths:
            show_info(self, "Re-read files", "Tick or highlight the files to re-read.")
            return
        sheet = None
        sheets = {self._session.files[k].sheet for k in keys if k in self._session.files}
        if len(sheets) == 1:
            sheet = sheets.pop()
        self._start(self._load_files(paths, sheet_name=sheet, ask_sheet=False))

    # ---------------------------------------------------------------- add files

    def _on_add_files(self) -> None:
        if self._busy or self._session is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add daily Excel files",
            "",
            "Excel files (*.xlsx *.xlsm);;All files (*)",
        )
        if paths:
            self._start(self._load_files(paths, sheet_name=None, ask_sheet=True))

    def _ask_sheet(self, sheets: List[str]) -> Optional[str]:
        default = next((i for i, s in enumerate(sheets) if s.upper() == "MASTER"), 0)
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Choose worksheet")
        dlg.setLabelText("These files share several sheets. Which one holds the expenses?")
        dlg.setComboBoxItems(sheets)
        dlg.setTextValue(sheets[default])
        style_input_dialog(dlg)
        if dlg.exec() != QInputDialog.Accepted:
            raise UploadCancelled()
        return dlg.textValue()

    async def _load_files(
        self, paths: List[str], *, sheet_name: Optional[str], ask_sheet: bool
    ) -> None:
        session = self._session
        if session is None:
            return
        with pause_background_polls(self):
            if ask_sheet:
                try:
                    sheets = await asyncio.to_thread(common_sheet_names, paths)
                except Exception:
                    sheets = []
                if len(sheets) > 1:
                    sheet_name = self._ask_sheet(sheets)
            try:
                with UploadBusy(
                    self, "Reading Excel files…", title="Import Daily", cancellable=True
                ) as busy:
                    await busy.tick()

                    async def on_file(index: int, total: int, name: str) -> None:
                        busy.update(
                            f"Reading {name}  ({index} of {total})",
                            value=index - 1,
                            maximum=total,
                        )
                        await busy.tick()

                    errors = await add_files_to_session(
                        session,
                        paths,
                        sheet_name=sheet_name,
                        on_file=on_file,
                        should_cancel=busy.should_cancel,
                    )
            finally:
                # Files read before a cancel stay in the session.
                self._save()
                self._fill_session_combo()
                self._revalidate()
        if errors:
            listing = "\n".join(f"  · {Path(e.path).name}: {e.message}" for e in errors[:10])
            more = f"\n  · …and {len(errors) - 10} more" if len(errors) > 10 else ""
            show_warning(
                self,
                "Some files were not loaded",
                f"{len(errors)} of {len(paths)} file(s) could not be read:\n\n{listing}{more}",
            )

    # ---------------------------------------------------------- classic import

    def _on_classic_import(self) -> None:
        self._start(self._run_classic())

    async def _run_classic(self) -> None:
        from tahmeed.ui.accountant.daily_to_master_flow import run_daily_to_master_flow

        with pause_background_polls(self):
            await run_daily_to_master_flow(self, verified_by=getattr(self._user, "_id", None))
