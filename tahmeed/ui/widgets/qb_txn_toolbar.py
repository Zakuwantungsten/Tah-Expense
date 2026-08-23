"""QuickBooks-style transaction toolbar for cashier Form / Table (and reuse later)."""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QToolButton, QCheckBox, QWidget, QSizePolicy,
    QMenu,
)


_BG = "#F0F0F0"
_BORDER = "#D0D0D0"
_TEXT = "#1B2B4B"
_BLUE = "#0077C5"
_MUTED = "#9CA3AF"
_HOVER = "#E5E7EB"


class QbTxnToolbar(QFrame):
    """Classic QB Desktop-style strip: icon above label, one shared row.

    Core: Undo, Redo, Find, New, Save, Delete, Copy, Print, Attach File.
    With ``register_actions=True`` (cashier Table): also Export, Import,
    Today, Edit, Submit day — same icon style as New/Save.
    """

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
    today_clicked = Signal()
    edit_clicked = Signal()
    submit_clicked = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        register_actions: bool = False,
    ) -> None:
        super().__init__(parent)
        self._register_actions = register_actions
        self.setObjectName("qbTxnToolbar")
        self.setFixedHeight(56)
        self.setStyleSheet(
            f"""
            QFrame#qbTxnToolbar {{
                background: {_BG};
                border-bottom: 1px solid {_BORDER};
            }}
            QToolButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                color: {_TEXT};
                font-size: 10px;
                font-family: 'Segoe UI';
                padding: 2px 6px;
            }}
            QToolButton:hover:!disabled {{
                background: {_HOVER};
                border-color: {_BORDER};
            }}
            QToolButton:pressed:!disabled {{
                background: #DCE3EC;
            }}
            QToolButton:disabled {{
                color: {_MUTED};
            }}
            QToolButton#qbEditActive {{
                background: #FEF3C7;
                border-color: #F59E0B;
                color: #92400E;
            }}
            QLabel#qbSep {{
                color: {_BORDER};
                background: transparent;
                font-size: 16px;
                padding: 0 2px;
            }}
            QCheckBox {{
                color: {_TEXT};
                font-size: 11px;
                font-family: 'Segoe UI';
                spacing: 4px;
                background: transparent;
            }}
            QCheckBox:disabled {{
                color: {_MUTED};
            }}
            """
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(2)

        self._btn_undo: Optional[QToolButton] = None
        self._btn_redo: Optional[QToolButton] = None
        if register_actions:
            self._btn_undo = self._tool_btn(
                "mdi.undo", "Undo", "Undo last change (Ctrl+Z)"
            )
            self._btn_redo = self._tool_btn(
                "mdi.redo", "Redo", "Redo (Ctrl+Y)"
            )
            self._btn_undo.clicked.connect(self.undo_clicked.emit)
            self._btn_redo.clicked.connect(self.redo_clicked.emit)
            self._btn_undo.setEnabled(False)
            self._btn_redo.setEnabled(False)
            lay.addWidget(self._btn_undo)
            lay.addWidget(self._btn_redo)
            lay.addWidget(self._sep())

        self._btn_find_prev = self._icon_btn(
            "mdi.chevron-left", "Previous", "Find previous entry"
        )
        self._btn_find_next = self._icon_btn(
            "mdi.chevron-right", "Next", "Find next entry"
        )
        self._btn_find_prev.clicked.connect(self.find_prev.emit)
        self._btn_find_next.clicked.connect(self.find_next.emit)
        find_wrap = QWidget()
        find_wrap.setStyleSheet("background: transparent;")
        fl = QHBoxLayout(find_wrap)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        fl.addWidget(self._btn_find_prev)
        fl.addWidget(self._btn_find_next)
        find_col = QWidget()
        find_col.setStyleSheet("background: transparent;")
        fc = QHBoxLayout(find_col)
        fc.setContentsMargins(0, 0, 0, 0)
        fc.setSpacing(4)
        fc.addWidget(find_wrap)
        find_lbl = QLabel("Find")
        find_lbl.setStyleSheet(
            f"color:{_TEXT};font-size:10px;font-family:'Segoe UI';background:transparent;"
        )
        fc.addWidget(find_lbl)
        lay.addWidget(find_col)

        lay.addWidget(self._sep())

        self._btn_new = self._tool_btn("mdi.file-plus-outline", "New", "New entry")
        self._btn_save = self._tool_btn("mdi.content-save-outline", "Save", "Save")
        self._btn_delete = self._tool_btn("mdi.close-box-outline", "Delete", "Delete entry")
        self._btn_copy = self._tool_btn("mdi.content-copy", "Create a Copy", "Duplicate entry")
        self._btn_new.clicked.connect(self.new_clicked.emit)
        self._btn_save.clicked.connect(self.save_clicked.emit)
        self._btn_delete.clicked.connect(self.delete_clicked.emit)
        self._btn_copy.clicked.connect(self.copy_clicked.emit)
        for b in (self._btn_new, self._btn_save, self._btn_delete, self._btn_copy):
            lay.addWidget(b)

        lay.addWidget(self._sep())

        self._btn_print = self._tool_btn(
            "fa5s.print", "Print", "Export PDF"
        )
        self._btn_print.clicked.connect(self.print_clicked.emit)
        lay.addWidget(self._btn_print)

        self._btn_export: Optional[QToolButton] = None
        self._btn_import: Optional[QToolButton] = None
        self._btn_today: Optional[QToolButton] = None
        self._btn_edit: Optional[QToolButton] = None
        self._btn_submit: Optional[QToolButton] = None

        if register_actions:
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
            # Keep icon+label under icon; InstantPopup still shows the pixmap.
            self._btn_export.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            lay.addWidget(self._btn_export)

            self._btn_import = self._tool_btn(
                "mdi.file-upload-outline", "Import", "Import Excel"
            )
            self._btn_import.clicked.connect(self.import_clicked.emit)
            lay.addWidget(self._btn_import)

            self._btn_today = self._tool_btn(
                "mdi.calendar-today", "Today", "Jump to today"
            )
            self._btn_today.clicked.connect(self.today_clicked.emit)
            lay.addWidget(self._btn_today)

            self._btn_edit = self._tool_btn(
                "mdi.pencil-outline", "Edit", "Edit saved rows"
            )
            self._btn_edit.clicked.connect(self.edit_clicked.emit)
            lay.addWidget(self._btn_edit)

            self._btn_submit = self._tool_btn(
                "mdi.send-check-outline", "Submit day", "Submit day to Verify"
            )
            self._btn_submit.clicked.connect(self.submit_clicked.emit)
            lay.addWidget(self._btn_submit)

        lay.addWidget(self._sep())

        self._btn_attach = self._tool_btn(
            "mdi.paperclip", "Attach File", "Attach receipt or file"
        )
        self._btn_attach.clicked.connect(self.attach_clicked.emit)
        lay.addWidget(self._btn_attach)

        self._vat_cb = QCheckBox("Amts Inc VAT")
        self._vat_cb.setEnabled(False)
        self._vat_cb.setToolTip("Tax-inclusive amounts — coming in a later phase")
        lay.addWidget(self._vat_cb)

        lay.addStretch(1)

        self._attach_badge = QLabel("")
        self._attach_badge.setStyleSheet(
            "color:#0369A1;font-size:11px;font-family:'Segoe UI';"
            "background:transparent;padding:0 6px;"
        )
        self._attach_badge.hide()
        lay.addWidget(self._attach_badge)

    def _sep(self) -> QLabel:
        s = QLabel("│")
        s.setObjectName("qbSep")
        s.setAlignment(Qt.AlignVCenter)
        return s

    def _icon_btn(self, icon: str, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonIconOnly)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        try:
            b.setIcon(self._load_icon(icon) or qta.icon(icon, color=_BLUE))
            b.setIconSize(QSize(18, 18))
        except Exception:
            b.setText(text[:1])
        return b

    def _tool_btn(self, icon: str, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.setText(text)
        b.setAutoRaise(True)
        b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        pix = self._load_icon(icon)
        if pix is not None:
            b.setIcon(pix)
            b.setIconSize(QSize(20, 20))
        return b

    def _load_icon(self, *names: str):
        """Return the first qtawesome icon that resolves; try common aliases."""
        candidates: list[str] = []
        for name in names:
            candidates.append(name)
            # Common aliases when a Material name is missing / empty.
            if "print" in name.lower():
                candidates.extend(["fa5s.print", "mdi.printer"])
            elif "download" in name:
                candidates.extend(["mdi.download", "fa5s.file-download", "mdi.file-download-outline"])
            elif "upload" in name:
                candidates.extend(["mdi.upload", "fa5s.file-upload", "mdi.file-upload-outline"])
        seen: set[str] = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            try:
                icon = qta.icon(name, color=_BLUE)
                if icon is not None and not icon.isNull():
                    return icon
            except Exception:
                continue
        return None

    def set_attachment_count(self, count: int) -> None:
        if count > 0:
            self._attach_badge.setText(
                f"{count} file{'s' if count != 1 else ''} attached"
            )
            self._attach_badge.show()
        else:
            self._attach_badge.hide()

    def set_edit_state(self, active: bool, dirty_count: int = 0) -> None:
        """Mirror register Edit/Cancel on the toolbar Edit button."""
        if self._btn_edit is None:
            return
        if active:
            self._btn_edit.setText("Cancel")
            self._btn_edit.setObjectName("qbEditActive")
            try:
                self._btn_edit.setIcon(qta.icon("mdi.close", color="#B45309"))
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
        # Force stylesheet re-apply for objectName change
        self._btn_edit.style().unpolish(self._btn_edit)
        self._btn_edit.style().polish(self._btn_edit)

    def set_undo_redo_enabled(self, *, can_undo: bool, can_redo: bool) -> None:
        """Enable Undo/Redo when the register has history (table toolbar only)."""
        if self._btn_undo is not None:
            self._btn_undo.setEnabled(can_undo)
        if self._btn_redo is not None:
            self._btn_redo.setEnabled(can_redo)

    def set_actions_enabled(
        self,
        *,
        find: bool = True,
        new: bool = True,
        save: bool = True,
        delete: bool = True,
        copy: bool = True,
        print_: bool = True,
        attach: bool = True,
    ) -> None:
        self._btn_find_prev.setEnabled(find)
        self._btn_find_next.setEnabled(find)
        self._btn_new.setEnabled(new)
        self._btn_save.setEnabled(save)
        self._btn_delete.setEnabled(delete)
        self._btn_copy.setEnabled(copy)
        self._btn_print.setEnabled(print_)
        self._btn_attach.setEnabled(attach)

    def set_mutation_busy(self, busy: bool) -> None:
        """Disable Save / Submit / Import while a register mutation is in flight."""
        self._btn_save.setEnabled(not busy)
        if self._btn_submit is not None:
            self._btn_submit.setEnabled(not busy)
        if self._btn_import is not None:
            self._btn_import.setEnabled(not busy)
        if self._btn_edit is not None:
            self._btn_edit.setEnabled(not busy)
        if busy:
            if self._btn_undo is not None:
                self._btn_undo.setEnabled(False)
            if self._btn_redo is not None:
                self._btn_redo.setEnabled(False)
