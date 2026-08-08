"""Dialog to view / add / open / remove transaction attachments."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from bson import ObjectId
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox, QWidget,
)

from tahmeed.services.attachment_service import (
    add_attachment, get_attachments, remove_attachment, resolve_attachment_path,
)


class AttachmentDialog(QDialog):
    """Manage files attached to a single saved transaction."""

    def __init__(
        self,
        tx_id: ObjectId,
        *,
        description: str = "",
        actor_id: ObjectId | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._tx_id = tx_id
        self._actor_id = actor_id
        self._items: List[dict] = []
        self.setWindowTitle("Attachments")
        self.setMinimumSize(420, 320)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        title = description.strip() or "Transaction"
        if len(title) > 60:
            title = title[:57] + "…"
        hdr = QLabel(f"Files for: {title}")
        hdr.setStyleSheet("font-size:13px;font-weight:600;color:#111827;")
        hdr.setWordWrap(True)
        lay.addWidget(hdr)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{border:1px solid #D1D5DB;border-radius:6px;}"
            "QListWidget::item{padding:6px 8px;}"
        )
        self._list.itemDoubleClicked.connect(lambda _: self._open_selected())
        lay.addWidget(self._list, 1)

        self._hint = QLabel("No files attached yet.")
        self._hint.setStyleSheet("color:#6B7280;font-size:12px;")
        lay.addWidget(self._hint)

        row = QHBoxLayout()
        add_btn = QPushButton("Add file…")
        add_btn.clicked.connect(self._add)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(self._open_selected)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row.addWidget(add_btn)
        row.addWidget(open_btn)
        row.addWidget(remove_btn)
        row.addStretch()
        row.addWidget(close_btn)
        lay.addLayout(row)

        asyncio.ensure_future(self._reload())

    @property
    def attachment_count(self) -> int:
        return len(self._items)

    async def _reload(self) -> None:
        try:
            self._items = await get_attachments(self._tx_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load attachments:\n{exc}")
            self._items = []
        self._list.clear()
        for meta in self._items:
            name = meta.get("filename") or meta.get("stored_name") or "file"
            size = int(meta.get("size") or 0)
            size_lbl = _fmt_size(size)
            item = QListWidgetItem(f"{name}  ({size_lbl})")
            item.setData(Qt.UserRole, meta.get("id"))
            self._list.addItem(item)
        self._hint.setVisible(not self._items)

    def _selected_meta(self) -> Optional[dict]:
        item = self._list.currentItem()
        if not item:
            return None
        att_id = item.data(Qt.UserRole)
        return next((a for a in self._items if a.get("id") == att_id), None)

    def _add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Attach file",
            "",
            "Documents & Images (*.pdf *.png *.jpg *.jpeg *.gif *.webp "
            "*.bmp *.tif *.tiff *.doc *.docx *.xls *.xlsx *.csv *.txt);;"
            "All files (*.*)",
        )
        if not path:
            return
        asyncio.ensure_future(self._do_add(path))

    async def _do_add(self, path: str) -> None:
        try:
            await add_attachment(self._tx_id, path, actor_id=self._actor_id)
            await self._reload()
        except Exception as exc:
            QMessageBox.warning(self, "Attach failed", str(exc))

    def _open_selected(self) -> None:
        meta = self._selected_meta()
        if not meta:
            QMessageBox.information(self, "Attachments", "Select a file first.")
            return
        path = resolve_attachment_path(self._tx_id, meta)
        if path is None:
            QMessageBox.warning(
                self, "Missing file",
                "The file is recorded but missing on this computer.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _remove_selected(self) -> None:
        meta = self._selected_meta()
        if not meta:
            QMessageBox.information(self, "Attachments", "Select a file first.")
            return
        name = meta.get("filename") or "this file"
        if (
            QMessageBox.question(
                self, "Remove attachment",
                f'Remove "{name}"?',
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_remove(str(meta.get("id") or "")))

    async def _do_remove(self, attachment_id: str) -> None:
        try:
            await remove_attachment(
                self._tx_id, attachment_id, actor_id=self._actor_id
            )
            await self._reload()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to remove:\n{exc}")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
