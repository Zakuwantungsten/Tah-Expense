"""Excel-style compact ribbon for Daily Register (Table page).

Grouped icon-above-label commands (Separate Expenses / FeedRibbon language).
Totals live in the grid footer — not on this strip. Sidebar is untouched.
"""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from tahmeed.ui.widgets.qb_txn_toolbar import _font_size_icon

_BG = "#F8FAFC"
_BORDER = "#E5E7EB"
_TEXT = "#1B2B4B"
_BLUE = "#0077C5"
_MUTED = "#9CA3AF"
_HOVER = "#E5E7EB"
_FIND_BG = "#DBEAFE"
_FIND_BORDER = "#93C5FD"
_DELETE = "#E57373"
_GROUP = "#6B7280"
_EDIT_ACTIVE = "#B45309"


class RegisterRibbonToolbar(QFrame):
    """Compact Home ribbon for the Daily Register Table page."""

    find_prev = Signal()
    find_next = Signal()
    undo_clicked = Signal()
    redo_clicked = Signal()
    new_clicked = Signal()
    save_clicked = Signal()
    delete_clicked = Signal()
    copy_clicked = Signal()
    print_clicked = Signal()
    attach_clicked = Signal()
    export_clicked = Signal(str)  # "xlsx" | "csv" | "pdf"
    import_clicked = Signal()
    day_issues_clicked = Signal()
    today_clicked = Signal()
    edit_clicked = Signal()
    submit_clicked = Signal()
    clear_table_clicked = Signal()
    font_larger_clicked = Signal()
    font_smaller_clicked = Signal()
    wrap_clicked = Signal()
    filter_clicked = Signal()
    fix_parked_trucks_clicked = Signal()
    clear_parked_clicked = Signal()
    revive_skipped_clicked = Signal()
    discard_skipped_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("registerRibbonToolbar")
        self.setFixedHeight(86)
        # Transparent so the page-level circuit flows through continuously.
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            f"""
            QFrame#registerRibbonToolbar {{
                background: transparent;
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
            QToolButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                color: {_TEXT};
                font-size: 10px;
                font-family: 'Segoe UI';
                padding: 2px 4px 1px 4px;
            }}
            QToolButton:hover:!disabled {{
                background: {_HOVER};
                border-color: {_BORDER};
            }}
            QToolButton:pressed:!disabled {{
                background: #DCE3EC;
            }}
            QToolButton:checked {{
                background: #DBEAFE;
                border-color: #93C5FD;
            }}
            QToolButton:disabled {{
                color: {_MUTED};
            }}
            QToolButton#registerRibbonFind {{
                background: {_FIND_BG};
                border-color: {_FIND_BORDER};
                min-width: 52px;
                padding: 4px 8px;
            }}
            QToolButton#registerRibbonFind:hover:!disabled {{
                background: #BFDBFE;
                border-color: #60A5FA;
            }}
            QToolButton#registerEditActive {{
                background: #FEF3C7;
                border-color: #F59E0B;
                color: #92400E;
            }}
            QLabel#registerRibbonGroup {{
                color: {_GROUP};
                background: transparent;
                font-size: 9px;
                font-weight: 600;
                font-family: 'Segoe UI';
                letter-spacing: 0.6px;
            }}
            QLabel#registerRibbonSep {{
                color: {_BORDER};
                background: transparent;
                font-size: 18px;
                padding: 0 2px;
            }}
            """
        )

        self._parked_mode = False
        self._skipped_mode = False
        self._parked_count = 0
        self._skipped_count = 0
        self._narrow = False
        self._overflow_keys: list[str] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 2)
        root.setSpacing(0)
        self._root = root

        # ── Clipboard ───────────────────────────────────────────────────
        self._btn_undo = self._tool_btn("mdi.undo", "Undo", "Undo last change (Ctrl+Z)")
        self._btn_redo = self._tool_btn("mdi.redo", "Redo", "Redo (Ctrl+Y)")
        self._btn_undo.clicked.connect(self.undo_clicked.emit)
        self._btn_redo.clicked.connect(self.redo_clicked.emit)
        self._btn_undo.setEnabled(False)
        self._btn_redo.setEnabled(False)
        self._grp_clipboard = self._group("CLIPBOARD", [self._btn_undo, self._btn_redo])
        root.addWidget(self._grp_clipboard)
        root.addWidget(self._sep())

        # ── Font ────────────────────────────────────────────────────────
        self._btn_font_larger = self._icon_tool_btn(
            _font_size_icon(larger=True), "Larger", "Increase table text size"
        )
        self._btn_font_smaller = self._icon_tool_btn(
            _font_size_icon(larger=False), "Smaller", "Decrease table text size"
        )
        self._btn_wrap = self._tool_btn(
            "mdi.format-text-wrapping-wrap",
            "Wrap",
            "Wrap text in selected columns (or all if none selected)",
        )
        self._btn_wrap.setCheckable(True)
        self._btn_font_larger.clicked.connect(self.font_larger_clicked.emit)
        self._btn_font_smaller.clicked.connect(self.font_smaller_clicked.emit)
        self._btn_wrap.clicked.connect(lambda *_: self.wrap_clicked.emit())
        self._grp_font = self._group(
            "FONT",
            [self._btn_font_larger, self._btn_font_smaller, self._btn_wrap],
        )
        root.addWidget(self._grp_font)
        root.addWidget(self._sep())

        # ── Find ────────────────────────────────────────────────────────
        self._btn_find = self._tool_btn(
            "mdi.magnify", "Find", "Find next entry",
            object_name="registerRibbonFind",
        )
        self._btn_find_prev = self._icon_only(
            "mdi.chevron-left", "Find previous entry"
        )
        self._btn_find_next = self._icon_only(
            "mdi.chevron-right", "Find next entry"
        )
        self._btn_find.clicked.connect(self.find_next.emit)
        self._btn_find_prev.clicked.connect(self.find_prev.emit)
        self._btn_find_next.clicked.connect(self.find_next.emit)
        find_row = QWidget()
        find_row.setStyleSheet("background:transparent;")
        fr = QHBoxLayout(find_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.setSpacing(0)
        fr.addWidget(self._btn_find)
        fr.addWidget(self._btn_find_prev)
        fr.addWidget(self._btn_find_next)
        self._grp_find = self._group("FIND", [find_row])
        root.addWidget(self._grp_find)
        root.addWidget(self._sep())

        # ── Entries ─────────────────────────────────────────────────────
        self._btn_new = self._tool_btn(
            "mdi.file-plus-outline", "New", "New blank table for fresh data entry"
        )
        self._btn_save = self._tool_btn(
            "mdi.content-save-outline", "Save", "Save"
        )
        self._btn_delete = self._tool_btn(
            "mdi.close-box-outline", "Delete", "Delete entry", color=_DELETE
        )
        self._btn_copy = self._tool_btn(
            "mdi.content-copy", "Copy", "Duplicate entry"
        )
        self._btn_clear_table = self._tool_btn(
            "mdi.table-remove",
            "Clear",
            "Clear all unsaved rows and typed data (saved entries stay)",
        )
        self._btn_new.clicked.connect(self.new_clicked.emit)
        self._btn_save.clicked.connect(self.save_clicked.emit)
        self._btn_delete.clicked.connect(self.delete_clicked.emit)
        self._btn_copy.clicked.connect(self.copy_clicked.emit)
        self._btn_clear_table.clicked.connect(self.clear_table_clicked.emit)
        self._grp_entries = self._group(
            "ENTRIES",
            [
                self._btn_new,
                self._btn_save,
                self._btn_delete,
                self._btn_copy,
                self._btn_clear_table,
            ],
        )
        root.addWidget(self._grp_entries)
        self._sep_after_entries = self._sep()
        root.addWidget(self._sep_after_entries)

        # ── Day ─────────────────────────────────────────────────────────
        self._btn_today = self._tool_btn(
            "mdi.calendar-today", "Today", "Jump to today"
        )
        self._btn_edit = self._tool_btn(
            "mdi.pencil-outline", "Edit", "Edit saved rows"
        )
        self._btn_submit = self._tool_btn(
            "mdi.send-check-outline", "Submit", "Submit day to Verify"
        )
        self._btn_day_issues = self._tool_btn(
            "mdi.alert-decagram-outline",
            "Issues",
            "Map unmapped descriptions (Payee + Item)",
        )
        self._btn_today.clicked.connect(self.today_clicked.emit)
        self._btn_edit.clicked.connect(self.edit_clicked.emit)
        self._btn_submit.clicked.connect(self.submit_clicked.emit)
        self._btn_day_issues.clicked.connect(self.day_issues_clicked.emit)
        self._grp_day = self._group(
            "DAY",
            [
                self._btn_today,
                self._btn_edit,
                self._btn_submit,
                self._btn_day_issues,
            ],
        )
        root.addWidget(self._grp_day)
        self._sep_after_day = self._sep()
        root.addWidget(self._sep_after_day)

        # ── Data ────────────────────────────────────────────────────────
        self._btn_print = self._tool_btn("fa5s.print", "Print", "Export PDF")
        self._btn_export = self._tool_btn(
            "mdi.file-download-outline", "Export", "Export register"
        )
        export_menu = QMenu(self._btn_export)
        export_menu.setStyleSheet(
            "QMenu {"
            "  background: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 6px;"
            "  padding: 4px;"
            "}"
            "QMenu::item {"
            "  padding: 6px 16px; border-radius: 4px;"
            "  font-size: 12px; color: #111827;"
            "}"
            "QMenu::item:selected { background: #EFF6FF; color: #1D4ED8; }"
        )
        for label, fmt in (
            ("Excel workbook (.xlsx)", "xlsx"),
            ("CSV spreadsheet (.csv)", "csv"),
            ("PDF report (.pdf)", "pdf"),
        ):
            act = QAction(label, export_menu)
            act.triggered.connect(
                lambda _checked=False, f=fmt: self.export_clicked.emit(f)
            )
            export_menu.addAction(act)
        self._btn_export.setMenu(export_menu)
        self._btn_export.setPopupMode(QToolButton.InstantPopup)
        self._btn_export.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)

        self._btn_import = self._tool_btn(
            "mdi.file-upload-outline", "Import", "Import Excel"
        )
        self._btn_attach = self._tool_btn(
            "mdi.paperclip", "Attach", "Attach receipt or file"
        )
        self._btn_print.clicked.connect(self.print_clicked.emit)
        self._btn_import.clicked.connect(self.import_clicked.emit)
        self._btn_attach.clicked.connect(self.attach_clicked.emit)
        self._grp_data = self._group(
            "DATA",
            [
                self._btn_print,
                self._btn_export,
                self._btn_import,
                self._btn_attach,
            ],
        )
        root.addWidget(self._grp_data)
        self._sep_after_data = self._sep()
        root.addWidget(self._sep_after_data)

        # ── View ────────────────────────────────────────────────────────
        self._btn_filter = self._tool_btn(
            "mdi.filter-variant",
            "Filter",
            "Column filters on — click to clear filters and hide ▾",
        )
        self._btn_filter.setCheckable(True)
        self._btn_filter.setChecked(True)
        self._btn_filter.clicked.connect(lambda *_: self.filter_clicked.emit())
        self._grp_view = self._group("VIEW", [self._btn_filter])
        root.addWidget(self._grp_view)
        self._sep_after_view = self._sep()
        root.addWidget(self._sep_after_view)

        # ── Context (Parked / Skipped) ───────────────────────────────────
        self._btn_fix_parked = self._tool_btn(
            "mdi.truck-check-outline",
            "Fix trucks",
            "Parked tab — open truck correction for parked rows",
        )
        self._btn_clear_parked = self._tool_btn(
            "mdi.parking",
            "Clear parked",
            "Parked tab — move all parked rows to Trash",
            color="#B91C1C",
        )
        self._btn_revive_skipped = self._tool_btn(
            "mdi.restore",
            "Revive",
            "Skipped tab — move skipped rows back onto My entries",
        )
        self._btn_discard_skipped = self._tool_btn(
            "mdi.delete-outline",
            "Discard",
            "Skipped tab — permanently drop skipped rows from this device",
            color="#B91C1C",
        )
        self._btn_fix_parked.clicked.connect(self.fix_parked_trucks_clicked.emit)
        self._btn_clear_parked.clicked.connect(self.clear_parked_clicked.emit)
        self._btn_revive_skipped.clicked.connect(self.revive_skipped_clicked.emit)
        self._btn_discard_skipped.clicked.connect(self.discard_skipped_clicked.emit)
        self._grp_context = self._group(
            "CONTEXT",
            [
                self._btn_fix_parked,
                self._btn_clear_parked,
                self._btn_revive_skipped,
                self._btn_discard_skipped,
            ],
        )
        root.addWidget(self._grp_context)

        # ── More (overflow) ─────────────────────────────────────────────
        self._btn_more = self._tool_btn(
            "mdi.dots-horizontal", "More", "More actions"
        )
        self._more_menu = QMenu(self._btn_more)
        self._more_menu.setStyleSheet(
            "QMenu {"
            "  background: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 6px;"
            "  padding: 4px;"
            "}"
            "QMenu::item {"
            "  padding: 6px 14px; border-radius: 4px;"
            "  font-size: 12px; color: #111827;"
            "}"
            "QMenu::item:selected { background: #EFF6FF; color: #1D4ED8; }"
            "QMenu::separator { height: 1px; background: #E5E7EB; margin: 4px 8px; }"
        )
        self._btn_more.setMenu(self._more_menu)
        self._btn_more.setPopupMode(QToolButton.InstantPopup)
        self._btn_more.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._grp_more = self._group("MORE", [self._btn_more])
        self._grp_more.hide()
        root.addWidget(self._grp_more)

        root.addStretch(1)

        self._attach_badge = QLabel("")
        self._attach_badge.setStyleSheet(
            "color:#0369A1;font-size:11px;font-family:'Segoe UI';"
            "background:transparent;padding:0 6px;"
        )
        self._attach_badge.hide()
        root.addWidget(self._attach_badge, 0, Qt.AlignVCenter)

        # Overflow mapping: key → (button, full label for menu)
        self._overflow_catalog: list[tuple[str, QToolButton, str, object]] = [
            ("copy", self._btn_copy, "Create a Copy", self.copy_clicked),
            ("clear", self._btn_clear_table, "Clear Table", self.clear_table_clicked),
            ("today", self._btn_today, "Today", self.today_clicked),
            ("edit", self._btn_edit, "Edit", self.edit_clicked),
            ("submit", self._btn_submit, "Submit day", self.submit_clicked),
            ("issues", self._btn_day_issues, "Day Issues", self.day_issues_clicked),
            ("print", self._btn_print, "Print", self.print_clicked),
            ("import", self._btn_import, "Import", self.import_clicked),
            ("attach", self._btn_attach, "Attach File", self.attach_clicked),
            ("filter", self._btn_filter, "Filter", self.filter_clicked),
            (
                "fix_parked",
                self._btn_fix_parked,
                "Fix trucks",
                self.fix_parked_trucks_clicked,
            ),
            (
                "clear_parked",
                self._btn_clear_parked,
                "Clear parked",
                self.clear_parked_clicked,
            ),
            (
                "revive",
                self._btn_revive_skipped,
                "Revive",
                self.revive_skipped_clicked,
            ),
            (
                "discard",
                self._btn_discard_skipped,
                "Discard",
                self.discard_skipped_clicked,
            ),
        ]

        self._refresh_parked_actions()
        self._apply_narrow(False)

    # ── builders ──────────────────────────────────────────────────────────

    def _sep(self) -> QLabel:
        s = QLabel("│")
        s.setObjectName("registerRibbonSep")
        s.setAlignment(Qt.AlignVCenter)
        return s

    def _group(self, caption: str, widgets: list) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(wrap)
        vl.setContentsMargins(3, 0, 3, 0)
        vl.setSpacing(0)

        row = QWidget()
        row.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(1, 0, 1, 0)
        hl.setSpacing(1)
        hl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for w in widgets:
            hl.addWidget(w, 0, Qt.AlignTop)
        vl.addWidget(row, 1)

        lbl = QLabel(caption)
        lbl.setObjectName("registerRibbonGroup")
        lbl.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        vl.addWidget(lbl)
        return wrap

    def _apply_icon(self, btn: QToolButton, *, enabled: bool) -> None:
        icon = getattr(btn, "_ribbon_icon", "")
        color = getattr(btn, "_ribbon_color", _BLUE)
        custom = getattr(btn, "_ribbon_qicon", None)
        if custom is not None:
            btn.setIcon(custom)
            return
        if not icon:
            return
        try:
            btn.setIcon(qta.icon(icon, color=color if enabled else _MUTED))
        except Exception:
            alt = self._colored_icon(
                color if enabled else _MUTED, icon
            )
            if alt is not None:
                btn.setIcon(alt)

    def _tool_btn(
        self,
        icon: str,
        text: str,
        tip: str,
        *,
        color: str = _BLUE,
        object_name: str = "",
    ) -> QToolButton:
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setText(text)
        b.setAutoRaise(True)
        b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        if object_name:
            b.setObjectName(object_name)
        b._ribbon_icon = icon  # type: ignore[attr-defined]
        b._ribbon_color = color  # type: ignore[attr-defined]
        self._apply_icon(b, enabled=True)
        b.setIconSize(QSize(20, 20))
        return b

    def _icon_tool_btn(self, icon: QIcon, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setText(text)
        b.setAutoRaise(True)
        b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        b._ribbon_qicon = icon  # type: ignore[attr-defined]
        b.setIcon(icon)
        b.setIconSize(QSize(20, 20))
        return b

    def _icon_only(self, icon: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b._ribbon_icon = icon  # type: ignore[attr-defined]
        b._ribbon_color = _BLUE  # type: ignore[attr-defined]
        self._apply_icon(b, enabled=True)
        b.setIconSize(QSize(16, 16))
        return b

    def _colored_icon(self, color: str, *names: str) -> Optional[QIcon]:
        seen: set[str] = set()
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                icon = qta.icon(name, color=color)
                if icon is not None and not icon.isNull():
                    return icon
            except Exception:
                continue
        return None

    # ── overflow / resize ─────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Sidebar open on laptop widths — collapse secondary groups into More.
        self._apply_narrow(self.width() < 1180)

    def _apply_narrow(self, narrow: bool) -> None:
        self._narrow = bool(narrow)
        # Wide: show Day + Data + View groups; narrow: tuck into More.
        show_day = not narrow
        show_data = not narrow
        show_view = not narrow
        # Copy/Clear stay on Entries when wide; go to More when narrow.
        self._btn_copy.setVisible(not narrow)
        self._btn_clear_table.setVisible(not narrow)

        self._grp_day.setVisible(show_day)
        self._sep_after_day.setVisible(show_day)
        self._grp_data.setVisible(show_data)
        self._sep_after_data.setVisible(show_data)
        self._grp_view.setVisible(show_view)
        self._sep_after_view.setVisible(show_view and self._grp_context.isVisible())

        keys: list[str] = []
        if narrow:
            keys.extend(
                [
                    "copy",
                    "clear",
                    "today",
                    "edit",
                    "submit",
                    "issues",
                    "print",
                    "import",
                    "attach",
                    "filter",
                ]
            )
            # Export stays as InstantPopup on ribbon when wide; in narrow add menu acts.
            # Keep Export visible in Entries area? Put in More as three actions via rebuild.
        if self._parked_mode:
            keys.extend(["fix_parked", "clear_parked"])
        if self._skipped_mode:
            keys.extend(["revive", "discard"])

        # Context group visibility: only when mode active AND wide enough to show it.
        ctx_visible = (self._parked_mode or self._skipped_mode) and not narrow
        self._grp_context.setVisible(ctx_visible)
        if not ctx_visible:
            self._sep_after_view.setVisible(show_view and False)

        self._rebuild_more_menu(keys)
        self._grp_more.setVisible(bool(keys) or narrow)
        # Export: when narrow, expose via More (xlsx/csv/pdf) and hide ribbon Export.
        self._btn_export.setVisible(not narrow)

    def _rebuild_more_menu(self, keys: list[str]) -> None:
        self._more_menu.clear()
        self._overflow_keys = list(keys)
        seen: set[str] = set()
        for key, btn, label, signal in self._overflow_catalog:
            if key not in keys or key in seen:
                continue
            seen.add(key)
            if not btn.isEnabled() and key in (
                "fix_parked",
                "clear_parked",
                "revive",
                "discard",
            ):
                # Still list disabled items so users discover them; keep disabled.
                act = QAction(btn.icon(), label, self._more_menu)
                act.setEnabled(False)
                self._more_menu.addAction(act)
                continue
            act = QAction(btn.icon(), label, self._more_menu)
            act.setEnabled(btn.isEnabled())
            if key == "filter":
                act.setCheckable(True)
                act.setChecked(self._btn_filter.isChecked())
                act.triggered.connect(lambda *_: self.filter_clicked.emit())
            elif key == "edit":
                act.setText(self._btn_edit.text())
                act.triggered.connect(lambda *_: self.edit_clicked.emit())
            else:
                act.triggered.connect(lambda *_a, s=signal: s.emit())
            self._more_menu.addAction(act)

        if self._narrow:
            if self._more_menu.actions():
                self._more_menu.addSeparator()
            for label, fmt in (
                ("Export Excel (.xlsx)", "xlsx"),
                ("Export CSV (.csv)", "csv"),
                ("Export PDF (.pdf)", "pdf"),
            ):
                act = QAction(label, self._more_menu)
                act.triggered.connect(
                    lambda _c=False, f=fmt: self.export_clicked.emit(f)
                )
                self._more_menu.addAction(act)

    # ── public API (compatible with QbTxnToolbar register usage) ──────────

    def set_compact(self, compact: bool) -> None:
        """No-op — ribbon always uses icon+label; overflow handles narrow widths."""
        _ = compact

    def set_attachment_count(self, count: int) -> None:
        if count > 0:
            self._attach_badge.setText(
                f"{count} file{'s' if count != 1 else ''} attached"
            )
            self._attach_badge.show()
        else:
            self._attach_badge.hide()

    def set_edit_state(self, active: bool, dirty_count: int = 0) -> None:
        if active:
            self._btn_edit.setText("Cancel")
            self._btn_edit.setObjectName("registerEditActive")
            try:
                self._btn_edit.setIcon(qta.icon("mdi.close", color=_EDIT_ACTIVE))
            except Exception:
                pass
            self._btn_edit.setToolTip(
                f"Cancel edit  ·  {dirty_count} unsaved change"
                f"{'' if dirty_count == 1 else 's'}"
            )
        else:
            self._btn_edit.setText("Edit")
            self._btn_edit.setObjectName("")
            try:
                self._btn_edit.setIcon(qta.icon("mdi.pencil-outline", color=_BLUE))
            except Exception:
                pass
            self._btn_edit.setToolTip("Edit saved rows")
        self._btn_edit.style().unpolish(self._btn_edit)
        self._btn_edit.style().polish(self._btn_edit)
        if self._narrow:
            self._apply_narrow(True)

    def set_undo_redo_enabled(self, *, can_undo: bool, can_redo: bool) -> None:
        self._btn_undo.setEnabled(can_undo)
        self._btn_redo.setEnabled(can_redo)
        self._apply_icon(self._btn_undo, enabled=can_undo)
        self._apply_icon(self._btn_redo, enabled=can_redo)

    def set_font_size_controls_enabled(
        self, *, can_larger: bool, can_smaller: bool
    ) -> None:
        self._btn_font_larger.setEnabled(can_larger)
        self._btn_font_smaller.setEnabled(can_smaller)

    def set_wrap_active(self, active: bool) -> None:
        self._btn_wrap.setChecked(bool(active))

    def set_filter_active(self, active: bool) -> None:
        active = bool(active)
        self._btn_filter.setChecked(active)
        self._btn_filter.setToolTip(
            "Column filters on — click to clear filters and hide ▾"
            if active
            else "Column filters off — click to show ▾ on headers"
        )
        if self._narrow:
            self._apply_narrow(True)

    def set_mutation_busy(self, busy: bool) -> None:
        self._btn_save.setEnabled(not busy)
        self._btn_submit.setEnabled(not busy)
        self._btn_import.setEnabled(not busy)
        self._btn_edit.setEnabled(not busy)
        if busy:
            self._btn_undo.setEnabled(False)
            self._btn_redo.setEnabled(False)
            self._btn_fix_parked.setEnabled(False)
            self._btn_clear_parked.setEnabled(False)
            self._btn_revive_skipped.setEnabled(False)
            self._btn_discard_skipped.setEnabled(False)
        else:
            self._refresh_parked_actions()
        if self._narrow:
            self._apply_narrow(True)

    def set_parked_mode(self, mode: str) -> None:
        if isinstance(mode, bool):
            mode = "merged" if mode else "my"
        self._parked_mode = mode == "parked"
        self._skipped_mode = mode == "skipped"
        self._refresh_parked_actions()
        self._apply_narrow(self._narrow)

    def set_parked_count(self, count: int) -> None:
        self._parked_count = max(0, int(count or 0))
        self._refresh_parked_actions()
        if self._narrow:
            self._apply_narrow(True)

    def set_skipped_count(self, count: int) -> None:
        self._skipped_count = max(0, int(count or 0))
        self._refresh_parked_actions()
        if self._narrow:
            self._apply_narrow(True)

    def _refresh_parked_actions(self) -> None:
        on = bool(self._parked_mode)
        skipped_on = bool(self._skipped_mode)
        has_skipped = self._skipped_count > 0
        self._btn_fix_parked.setEnabled(on)
        self._btn_clear_parked.setEnabled(on and self._parked_count > 0)
        fix_color = _BLUE if on else _MUTED
        clear_color = "#B91C1C" if on and self._parked_count > 0 else _MUTED
        fix_icon = self._colored_icon(
            fix_color,
            "mdi.truck-check-outline",
            "mdi.truck-outline",
            "mdi.truck",
            "fa5s.truck",
        )
        if fix_icon is not None:
            self._btn_fix_parked.setIcon(fix_icon)
        clear_icon = self._colored_icon(clear_color, "mdi.parking", "fa5s.parking")
        if clear_icon is not None:
            self._btn_clear_parked.setIcon(clear_icon)

        self._btn_revive_skipped.setEnabled(skipped_on and has_skipped)
        self._btn_discard_skipped.setEnabled(skipped_on and has_skipped)
        revive_color = _BLUE if skipped_on and has_skipped else _MUTED
        discard_color = "#B91C1C" if skipped_on and has_skipped else _MUTED
        revive_icon = self._colored_icon(revive_color, "mdi.restore", "fa5s.undo")
        if revive_icon is not None:
            self._btn_revive_skipped.setIcon(revive_icon)
        discard_icon = self._colored_icon(
            discard_color, "mdi.delete-outline", "fa5s.trash"
        )
        if discard_icon is not None:
            self._btn_discard_skipped.setIcon(discard_icon)

        # Only the relevant context buttons for the active mode.
        self._btn_fix_parked.setVisible(on)
        self._btn_clear_parked.setVisible(on)
        self._btn_revive_skipped.setVisible(skipped_on)
        self._btn_discard_skipped.setVisible(skipped_on)
