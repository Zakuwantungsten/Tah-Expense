"""Accountant page: Import daily cashier Excel straight into Master Expenses."""

from __future__ import annotations

from typing import Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from tahmeed.models.user import User

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"


class ImportDailyMasterWidget(QWidget):
    """Landing page + launcher for daily Excel → Master (bypass Verify)."""

    def __init__(
        self,
        user: User,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._busy = False
        self._build()

    def refresh(self) -> None:
        """No data to reload — wizard is on-demand."""

    def _build(self) -> None:
        self.setObjectName("importDailyMaster")
        self.setStyleSheet(
            f"QWidget#importDailyMaster {{ background: {_BG}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        try:
            icon = QLabel()
            icon.setPixmap(
                qta.icon("mdi.file-upload-outline", color=_BLUE).pixmap(28, 28)
            )
            icon.setFixedSize(28, 28)
            icon.setStyleSheet("background: transparent;")
            hdr.addWidget(icon)
        except Exception:
            pass
        title = QLabel("Import Daily → Master Expenses")
        title.setStyleSheet(
            f"color:{_T1};font-size:18px;font-weight:700;"
            "font-family:'Segoe UI';background:transparent;"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        root.addLayout(hdr)

        card = QFrame()
        card.setObjectName("importDailyCard")
        card.setStyleSheet(
            f"QFrame#importDailyCard {{"
            f"  background:{_WHITE}; border:1px solid {_BORDER};"
            "  border-radius:10px;"
            "}"
        )
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(20, 18, 20, 18)
        cvl.setSpacing(12)

        intro = QLabel(
            "Upload a cashier Daily Register / MATUMIZI Excel and push it "
            "straight into <b>Master Expenses</b> — no Save, Submit, or Verify."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color:{_T2};font-size:13px;font-family:'Segoe UI';"
            "background:transparent;"
        )
        cvl.addWidget(intro)

        steps = QLabel(
            "<b>What happens</b><ol style='margin-top:6px;'>"
            "<li>Map each unknown description to an Item, or assign it to a "
            "new item (same Add Item form as the Items tab; saved for next time)</li>"
            "<li>Resolve mixed dates if the file spans more than one day</li>"
            "<li>Confirm a preview of the rows</li>"
            "<li>Check / correct vehicle numbers (add to registry, allow, or skip)</li>"
            "<li>Rows are inserted as <b>verified</b> Master Expenses and Daily Transactions</li>"
            "<li>Re-open the batch later under Table → Uploads (Open / Go To Date)</li>"
            "</ol>"
            "<span style='color:#9CA3AF;font-size:12px;'>"
            "Duplicate checking is not run for this import. "
            "Live cashier work should still use Table → Submit → Verify."
            "</span>"
        )
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.RichText)
        steps.setStyleSheet(
            f"color:{_T1};font-size:13px;font-family:'Segoe UI';"
            "background:transparent;"
        )
        cvl.addWidget(steps)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._import_btn = QPushButton("  Import Daily Excel…")
        self._import_btn.setCursor(Qt.PointingHandCursor)
        self._import_btn.setFixedHeight(38)
        try:
            self._import_btn.setIcon(
                qta.icon("mdi.microsoft-excel", color="#FFFFFF")
            )
            self._import_btn.setIconSize(QSize(18, 18))
        except Exception:
            pass
        self._import_btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;"
            f"border-radius:6px;font-size:13px;font-weight:600;"
            f"font-family:'Segoe UI';padding:0 18px;}}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
        self._import_btn.clicked.connect(self._on_import)
        btn_row.addWidget(self._import_btn)
        cvl.addLayout(btn_row)

        root.addWidget(card)

        tip = QLabel(
            "Tip: seed Trucks / Trailers / Motorcycles & Cars first so fewer "
            "plates need correction during import."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"color:{_TM};font-size:12px;font-family:'Segoe UI';"
            "background:transparent;"
        )
        root.addWidget(tip)
        root.addStretch()

    def _on_import(self) -> None:
        if self._busy:
            return
        from tahmeed.ui.async_utils import create_task

        create_task(self._run_import())

    def _dashboard(self):
        w = self.parent()
        while w is not None:
            if hasattr(w, "pause_notification_polling"):
                return w
            w = w.parent()
        return None

    async def _run_import(self) -> None:
        from tahmeed.ui.accountant.daily_to_master_flow import run_daily_to_master_flow
        from tahmeed.ui.dialog_theme import show_critical

        dash = self._dashboard()
        if dash is not None:
            dash.pause_notification_polling()

        self._busy = True
        self._import_btn.setEnabled(False)
        try:
            await run_daily_to_master_flow(
                self,
                verified_by=getattr(self._user, "_id", None),
            )
        except Exception as exc:
            show_critical(self, "Import Error", f"Import failed:\n\n{exc}")
        finally:
            self._busy = False
            self._import_btn.setEnabled(True)
            if dash is not None:
                dash.resume_notification_polling()
