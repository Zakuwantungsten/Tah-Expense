"""Shared desk receipt file viewer — image / PDF / open-externally."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import qtawesome as qta
from PySide6.QtCore import Qt, QByteArray, QSize, QTemporaryDir, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.receipt_chase_service import (
    fetch_receipt_file_bytes,
    fetch_receipt_file_meta,
)

_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_TEXT = "#1F2937"
_MUTED = "#6B7280"
_BORDER = "#E5E7EB"
_PRIMARY = "#1B2B4B"

_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_PDF_TYPES = {"application/pdf"}
_PDF_EXTS = {".pdf"}

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView

    _HAS_PDF = True
except ImportError:  # pragma: no cover - optional Qt module
    QPdfDocument = None  # type: ignore[misc, assignment]
    QPdfView = None  # type: ignore[misc, assignment]
    _HAS_PDF = False


def _ghost_btn(text: str, icon: str) -> QPushButton:
    btn = QPushButton(qta.icon(icon, color=_PRIMARY), text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(32)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background:{_CARD}; color:{_TEXT}; border:1px solid {_BORDER};
            border-radius:6px; padding:0 12px; font:600 12px 'Segoe UI';
        }}
        QPushButton:hover {{ background:{_BG}; }}
        """
    )
    return btn


def _primary_btn(text: str, icon: str) -> QPushButton:
    btn = QPushButton(qta.icon(icon, color="#FFFFFF"), text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(32)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background:{_PRIMARY}; color:#FFFFFF; border:none;
            border-radius:6px; padding:0 14px; font:600 12px 'Segoe UI';
        }}
        QPushButton:hover {{ background:#243a63; }}
        """
    )
    return btn


def _normalize_mime(raw: str) -> str:
    return (raw or "").split(";", 1)[0].strip().lower()


def _kind_for(filename: str, content_type: str) -> str:
    mime = _normalize_mime(content_type)
    ext = Path(filename or "").suffix.lower()
    if mime in _IMAGE_TYPES or ext in _IMAGE_EXTS:
        return "image"
    if mime in _PDF_TYPES or ext in _PDF_EXTS:
        return "pdf"
    return "generic"


class ReceiptFileViewer(QDialog):
    """Modal viewer: loads bytes via API and previews or opens externally."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        file_id: str,
        title_hint: str = "",
    ) -> None:
        super().__init__(parent)
        self._file_id = (file_id or "").strip()
        self._meta: dict[str, Any] = {}
        self._bytes: bytes = b""
        self._content_type = "application/octet-stream"
        self._filename = title_hint or "Receipt"
        self._temp_dir = QTemporaryDir()
        self._temp_path: Path | None = None
        self._pdf_doc = None
        self._full_pixmap: QPixmap | None = None
        self._kind = "generic"

        self.setWindowTitle(f"Receipt — {self._filename}")
        self.setModal(True)
        self.resize(900, 700)
        self.setMinimumSize(520, 420)
        self.setStyleSheet(f"QDialog {{ background:{_BG}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self._header = QLabel("Loading…")
        self._header.setWordWrap(True)
        self._header.setStyleSheet(
            f"color:{_TEXT}; font:600 13px 'Segoe UI'; background:transparent;"
        )
        root.addWidget(self._header)

        self._sub = QLabel("")
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet(
            f"color:{_MUTED}; font:11px 'Segoe UI'; background:transparent;"
        )
        root.addWidget(self._sub)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(
            f"QStackedWidget {{ background:{_CARD}; border:1px solid {_BORDER}; border-radius:8px; }}"
        )

        # 0 — loading / message
        self._msg = QLabel("Fetching file…")
        self._msg.setAlignment(Qt.AlignCenter)
        self._msg.setStyleSheet(f"color:{_MUTED}; font:13px 'Segoe UI';")
        msg_page = QWidget()
        msg_lay = QVBoxLayout(msg_page)
        msg_lay.addWidget(self._msg, 1)
        self._stack.addWidget(msg_page)

        # 1 — image
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self._image_label)
        self._stack.addWidget(scroll)

        # 2 — PDF
        if _HAS_PDF:
            self._pdf_view = QPdfView()
            self._pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self._stack.addWidget(self._pdf_view)
        else:
            pdf_fallback = QLabel("PDF preview module not available.")
            pdf_fallback.setAlignment(Qt.AlignCenter)
            self._stack.addWidget(pdf_fallback)

        # 3 — generic
        self._generic = QLabel()
        self._generic.setAlignment(Qt.AlignCenter)
        self._generic.setWordWrap(True)
        self._generic.setStyleSheet(f"color:{_TEXT}; font:13px 'Segoe UI'; padding:24px;")
        self._stack.addWidget(self._generic)

        root.addWidget(self._stack, 1)

        actions = QHBoxLayout()
        self._open_ext_btn = _ghost_btn("Open externally", "mdi.open-in-new")
        self._open_ext_btn.clicked.connect(self._open_externally)
        self._open_ext_btn.setEnabled(False)
        self._print_btn = _ghost_btn("Print", "mdi.printer")
        self._print_btn.clicked.connect(self._print)
        self._print_btn.setEnabled(False)
        close_btn = _primary_btn("Close", "mdi.close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(self._open_ext_btn)
        actions.addWidget(self._print_btn)
        actions.addStretch()
        actions.addWidget(close_btn)
        root.addLayout(actions)

        if not self._file_id:
            self._msg.setText("No file id.")
            return
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        try:
            self._meta = await fetch_receipt_file_meta(self._file_id)
            self._filename = str(
                self._meta.get("original_filename") or self._filename or "file"
            )
            self._content_type = str(self._meta.get("content_type") or "")
            if not self._meta.get("file_present", True):
                self._header.setText(self._filename)
                self._sub.setText("Indexed on the server but the file bytes are missing.")
                self._msg.setText("File missing on disk.")
                return
            payload, headers = await fetch_receipt_file_bytes(self._file_id)
            self._bytes = payload
            hdr_type = _normalize_mime(headers.get("content_type") or "")
            if hdr_type and hdr_type != "application/octet-stream":
                self._content_type = hdr_type
            if headers.get("filename"):
                self._filename = headers["filename"]
        except Exception as exc:
            self._header.setText(self._filename)
            self._sub.setText("")
            self._msg.setText(f"Could not open receipt:\n{exc}")
            return

        status = str(self._meta.get("inbox_status") or "")
        size = int(self._meta.get("size") or len(self._bytes) or 0)
        txs = self._meta.get("transaction_ids") or []
        self.setWindowTitle(f"Receipt — {self._filename}")
        self._header.setText(self._filename)
        self._sub.setText(
            f"{self._content_type or 'unknown type'} · {size:,} bytes"
            + (f" · {status}" if status else "")
            + (f" · linked rows: {len(txs)}" if txs else "")
        )
        self._open_ext_btn.setEnabled(True)
        self._print_btn.setEnabled(True)
        self._render()

    def _ensure_temp_file(self) -> Path | None:
        if self._temp_path and self._temp_path.is_file():
            return self._temp_path
        if not self._temp_dir.isValid() or not self._bytes:
            return None
        suffix = Path(self._filename).suffix or ""
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in self._filename)[
            :80
        ] or "receipt"
        if suffix and not safe.lower().endswith(suffix.lower()):
            safe = f"{safe}{suffix}"
        path = Path(self._temp_dir.path()) / safe
        try:
            path.write_bytes(self._bytes)
        except OSError:
            fd, name = tempfile.mkstemp(suffix=suffix or ".bin", prefix="tahmeed_receipt_")
            import os

            os.close(fd)
            path = Path(name)
            path.write_bytes(self._bytes)
        self._temp_path = path
        return path

    def _render(self) -> None:
        kind = _kind_for(self._filename, self._content_type)
        self._kind = kind
        self._full_pixmap = None
        if kind == "image":
            pix = QPixmap()
            if pix.loadFromData(QByteArray(self._bytes)):
                self._full_pixmap = pix
                # Fit reasonably; scroll area handles overflow
                max_w = max(400, self.width() - 48)
                shown = pix
                if pix.width() > max_w:
                    shown = pix.scaledToWidth(max_w, Qt.SmoothTransformation)
                self._image_label.setPixmap(shown)
                self._stack.setCurrentIndex(1)
                return
            kind = "generic"
            self._kind = kind

        if kind == "pdf":
            path = self._ensure_temp_file()
            if _HAS_PDF and path is not None:
                self._pdf_doc = QPdfDocument(self)
                err = self._pdf_doc.load(str(path))
                # QPdfDocument.Error.None_ == 0 on success in Qt6
                if int(err) == 0:
                    self._pdf_view.setDocument(self._pdf_doc)
                    self._stack.setCurrentIndex(2)
                    return
            # Fallback: open externally
            self._generic.setText(
                f"PDF preview unavailable.\nUse Open externally / Print (opens system app):\n"
                f"{self._filename}"
            )
            self._stack.setCurrentIndex(3)
            return

        self._generic.setText(
            f"No in-app preview for this file type.\n\n"
            f"{self._filename}\n{self._content_type or 'application/octet-stream'}\n\n"
            f"Click Open externally to view or Print to open the system app."
        )
        self._stack.setCurrentIndex(3)

    def _open_externally(self) -> None:
        path = self._ensure_temp_file()
        if path is None:
            QMessageBox.warning(self, "Receipt", "Nothing to open yet.")
            return
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if not ok:
            QMessageBox.warning(
                self,
                "Receipt",
                f"Could not open with the system viewer:\n{path}",
            )

    def _print(self) -> None:
        if not self._bytes:
            QMessageBox.information(self, "Print", "Nothing loaded to print yet.")
            return
        kind = self._kind or _kind_for(self._filename, self._content_type)

        # Non-image / non-previewable PDF → OS app (user prints from there).
        if kind == "generic" or (
            kind == "pdf" and (not _HAS_PDF or self._pdf_doc is None)
        ):
            reply = QMessageBox.question(
                self,
                "Print",
                "This file type needs the system app to print.\nOpen it externally now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._open_externally()
            return

        try:
            from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        except ImportError:
            QMessageBox.information(
                self,
                "Print",
                "Print support is not available in this build.\nUse Open externally instead.",
            )
            return

        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(self._filename or "receipt")
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print receipt")
        if dlg.exec() != QDialog.Accepted:
            return

        if kind == "image":
            self._print_image(printer)
            return
        if kind == "pdf":
            self._print_pdf(printer)

    @staticmethod
    def _printer_page_rect(printer: Any):
        """Device-pixel paint rect across Qt6 printer APIs."""
        try:
            layout = printer.pageLayout()
            rect = layout.paintRectPixels(int(printer.resolution()))
            if rect is not None and rect.width() > 0 and rect.height() > 0:
                return rect
        except Exception:
            pass
        try:
            return printer.pageRect(printer.Unit.DevicePixel)
        except Exception:
            return printer.pageRect()

    def _print_image(self, printer: Any) -> None:
        pix = self._full_pixmap
        if pix is None or pix.isNull():
            pix = QPixmap()
            if not pix.loadFromData(QByteArray(self._bytes)):
                QMessageBox.warning(self, "Print", "Could not decode image for printing.")
                return
        page = self._printer_page_rect(printer)
        if page is None or page.width() <= 0 or page.height() <= 0:
            QMessageBox.warning(self, "Print", "Printer page size is invalid.")
            return
        scaled = pix.scaled(
            int(page.width()),
            int(page.height()),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        painter = QPainter(printer)
        try:
            x = int(page.x() + (page.width() - scaled.width()) / 2)
            y = int(page.y() + (page.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        finally:
            painter.end()

    def _print_pdf(self, printer: Any) -> None:
        doc = self._pdf_doc
        if doc is None or not _HAS_PDF:
            self._open_externally()
            return
        page_count = int(doc.pageCount())
        if page_count <= 0:
            QMessageBox.warning(self, "Print", "PDF has no pages.")
            return
        page = self._printer_page_rect(printer)
        if page is None or page.width() <= 0 or page.height() <= 0:
            QMessageBox.warning(self, "Print", "Printer page size is invalid.")
            return
        target = QSize(max(1, int(page.width())), max(1, int(page.height())))
        painter = QPainter(printer)
        try:
            for index in range(page_count):
                if index > 0:
                    printer.newPage()
                image = doc.render(index, target)
                if image is None or image.isNull():
                    continue
                pm = QPixmap.fromImage(image)
                scaled = pm.scaled(
                    target,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                x = int(page.x() + (page.width() - scaled.width()) / 2)
                y = int(page.y() + (page.height() - scaled.height()) / 2)
                painter.drawPixmap(x, y, scaled)
        finally:
            painter.end()


def open_receipt_viewer(
    parent: QWidget | None, file_id: str, *, title_hint: str = ""
) -> ReceiptFileViewer | None:
    """Show the modal viewer for ``file_id``. Returns None if id empty."""
    fid = (file_id or "").strip()
    if not fid:
        return None
    dlg = ReceiptFileViewer(parent, file_id=fid, title_hint=title_hint)
    # Keep alive while async load runs (parent may not own the dialog strongly).
    if parent is not None:
        holders: list[ReceiptFileViewer] = getattr(parent, "_receipt_viewers", None) or []
        holders = [d for d in holders if d.isVisible()]
        holders.append(dlg)
        setattr(parent, "_receipt_viewers", holders)
        dlg.finished.connect(
            lambda _=0, d=dlg: getattr(parent, "_receipt_viewers", []).remove(d)
            if d in getattr(parent, "_receipt_viewers", [])
            else None
        )
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
