"""QuickBooks-style ribbon toolbar for Separate Expenses feed pages."""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

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


class FeedRibbonToolbar(QFrame):
    """Classic ribbon: icon-above-label buttons grouped with bottom captions.

    Groups: FIND | ENTRY | IMPORT | OUTPUT | VIEW
    """

    find_clicked = Signal()
    new_clicked = Signal()
    save_clicked = Signal()
    delete_clicked = Signal()
    copy_clicked = Signal()
    import_clicked = Signal()
    bulk_import_clicked = Signal()
    download_template_clicked = Signal()
    attach_clicked = Signal()
    print_clicked = Signal()
    export_clicked = Signal(str)  # "filtered" | "all"
    clear_filters_clicked = Signal()
    recalculate_clicked = Signal()
    columns_clicked = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        import_label: str = "Import",
        import_tip: str = "Import spreadsheet",
        template_label: str = "Download\nTemplate",
        show_template: bool = True,
        bulk_import_label: str = "",
        bulk_import_tip: str = "Bulk import all sheets",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("feedRibbonToolbar")
        self.setFixedHeight(86)
        self.setStyleSheet(
            f"""
            QFrame#feedRibbonToolbar {{
                background: {_BG};
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
            QToolButton:disabled {{
                color: {_MUTED};
            }}
            QToolButton#feedRibbonFind {{
                background: {_FIND_BG};
                border-color: {_FIND_BORDER};
                min-width: 52px;
                padding: 4px 8px;
            }}
            QToolButton#feedRibbonFind:hover:!disabled {{
                background: #BFDBFE;
                border-color: #60A5FA;
            }}
            QLabel#feedRibbonGroup {{
                color: {_GROUP};
                background: transparent;
                font-size: 9px;
                font-weight: 600;
                font-family: 'Segoe UI';
                letter-spacing: 0.6px;
            }}
            QLabel#feedRibbonSep {{
                color: {_BORDER};
                background: transparent;
                font-size: 18px;
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

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 2)
        root.setSpacing(0)

        # ── FIND ──────────────────────────────────────────────────────────
        self._btn_find = self._tool_btn(
            "mdi.magnify", "Find", "Focus the search box",
            object_name="feedRibbonFind",
        )
        self._btn_find.clicked.connect(self.find_clicked.emit)
        root.addWidget(self._group("FIND", [self._btn_find]))
        root.addWidget(self._sep())

        # ── ENTRY ─────────────────────────────────────────────────────────
        self._btn_new = self._tool_btn(
            "mdi.file-plus-outline", "New", "New entry (coming later)",
        )
        self._btn_save = self._tool_btn(
            "mdi.content-save-outline", "Save", "Save entry (coming later)",
        )
        self._btn_delete = self._tool_btn(
            "mdi.delete-outline", "Delete", "Delete selected entry (coming later)",
            color=_DELETE,
        )
        self._btn_copy = self._tool_btn(
            "mdi.content-copy", "Create a\nCopy", "Duplicate entry (coming later)",
        )
        root.addWidget(self._group(
            "ENTRY",
            [self._btn_new, self._btn_save, self._btn_delete, self._btn_copy],
        ))
        root.addWidget(self._sep())

        # ── IMPORT ────────────────────────────────────────────────────────
        self._btn_import = self._tool_btn(
            "mdi.upload-outline", import_label, import_tip,
        )
        self._btn_template = self._tool_btn(
            "mdi.download-outline", template_label, "Save an empty import template",
        )
        self._btn_bulk: Optional[QToolButton] = None
        self._btn_attach = self._tool_btn(
            "mdi.paperclip", "Attach\nFile", "Attach a file (coming later)",
        )
        self._btn_import.clicked.connect(self.import_clicked.emit)
        self._btn_template.clicked.connect(self.download_template_clicked.emit)
        self._btn_attach.clicked.connect(self.attach_clicked.emit)
        if not show_template:
            self._btn_template.hide()

        import_widgets: list = [self._btn_import]
        if bulk_import_label:
            self._btn_bulk = self._tool_btn(
                "mdi.file-multiple-outline", bulk_import_label, bulk_import_tip,
            )
            self._btn_bulk.clicked.connect(self.bulk_import_clicked.emit)
            import_widgets.append(self._btn_bulk)
        if show_template:
            import_widgets.append(self._btn_template)
        import_widgets.append(self._btn_attach)

        vat_wrap = QWidget()
        vat_wrap.setStyleSheet("background:transparent;")
        vat_vl = QVBoxLayout(vat_wrap)
        vat_vl.setContentsMargins(6, 8, 6, 0)
        vat_vl.setSpacing(0)
        vat_vl.setAlignment(Qt.AlignCenter)
        self._vat_cb = QCheckBox("Amts Inc VAT")
        self._vat_cb.setChecked(True)
        self._vat_cb.setEnabled(False)
        self._vat_cb.setToolTip("Amts Inc VAT — coming in a later phase")
        vat_vl.addWidget(self._vat_cb, 0, Qt.AlignCenter)
        import_widgets.append(vat_wrap)

        root.addWidget(self._group("IMPORT", import_widgets))
        root.addWidget(self._sep())

        # ── OUTPUT ────────────────────────────────────────────────────────
        self._btn_print = self._tool_btn(
            "fa5s.print", "Print", "Print the current table view",
        )
        self._btn_export = self._tool_btn(
            "mdi.file-export-outline", "Export", "Export to Excel",
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
        for label, key in (
            ("Export filtered…", "filtered"),
            ("Export all…", "all"),
        ):
            act = QAction(label, export_menu)
            act.triggered.connect(
                lambda _checked=False, k=key: self.export_clicked.emit(k)
            )
            export_menu.addAction(act)
        self._btn_export.setMenu(export_menu)
        self._btn_export.setPopupMode(QToolButton.InstantPopup)
        self._btn_export.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._btn_print.clicked.connect(self.print_clicked.emit)
        root.addWidget(self._group("OUTPUT", [self._btn_print, self._btn_export]))
        root.addWidget(self._sep())

        # ── VIEW ──────────────────────────────────────────────────────────
        self._btn_clear = self._tool_btn(
            "mdi.filter-remove-outline", "Clear\nFilters", "Clear search and date filters",
        )
        self._btn_recalc = self._tool_btn(
            "mdi.refresh", "Recalculate", "Reload totals and table data",
        )
        self._btn_columns = self._tool_btn(
            "mdi.view-column-outline", "Columns", "Auto-fit column widths",
        )
        self._btn_clear.clicked.connect(self.clear_filters_clicked.emit)
        self._btn_recalc.clicked.connect(self.recalculate_clicked.emit)
        self._btn_columns.clicked.connect(self.columns_clicked.emit)
        root.addWidget(self._group(
            "VIEW",
            [self._btn_clear, self._btn_recalc, self._btn_columns],
        ))

        root.addStretch(1)

        self.set_entry_actions_enabled(new=False, save=False, delete=False, copy=False)
        self._set_btn_enabled(self._btn_attach, False)

    # ── builders ──────────────────────────────────────────────────────────

    def _sep(self) -> QLabel:
        s = QLabel("│")
        s.setObjectName("feedRibbonSep")
        s.setAlignment(Qt.AlignVCenter)
        return s

    def _group(self, caption: str, widgets: list) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(wrap)
        vl.setContentsMargins(4, 0, 4, 0)
        vl.setSpacing(0)

        row = QWidget()
        row.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(2)
        hl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for w in widgets:
            hl.addWidget(w, 0, Qt.AlignTop)
        vl.addWidget(row, 1)

        lbl = QLabel(caption)
        lbl.setObjectName("feedRibbonGroup")
        lbl.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        vl.addWidget(lbl)
        return wrap

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
        b.setIconSize(QSize(22, 22))
        return b

    @staticmethod
    def _apply_icon(btn: QToolButton, *, enabled: bool) -> None:
        icon = getattr(btn, "_ribbon_icon", "")
        color = getattr(btn, "_ribbon_color", _BLUE)
        if not icon:
            return
        try:
            btn.setIcon(qta.icon(icon, color=color if enabled else _MUTED))
        except Exception:
            pass

    def _set_btn_enabled(self, btn: QToolButton, enabled: bool) -> None:
        btn.setEnabled(enabled)
        self._apply_icon(btn, enabled=enabled)

    def set_entry_actions_enabled(self, *, new: bool = False, save: bool = False,
                                  delete: bool = False, copy: bool = False) -> None:
        self._set_btn_enabled(self._btn_new, new)
        self._set_btn_enabled(self._btn_save, save)
        self._set_btn_enabled(self._btn_delete, delete)
        self._set_btn_enabled(self._btn_copy, copy)

    def set_filter_actions_enabled(self, enabled: bool) -> None:
        self._set_btn_enabled(self._btn_clear, enabled)
        self._set_btn_enabled(self._btn_find, enabled)
