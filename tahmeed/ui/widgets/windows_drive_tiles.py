"""Mounted-storage tiles for Receipt Explorer (server storages overview)."""

from __future__ import annotations

from typing import Any

import qtawesome as qta
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_BG = "#FFFFFF"
_TEXT = "#1F2937"
_MUTED = "#6B7280"
_BORDER = "#E5E7EB"
_HOVER = "#F3F4F6"
_SEL = "#E0F2FE"
_BAR_TRACK = "#E5E7EB"
_BAR_OK = "#0078D4"  # Windows Explorer blue
_BAR_WARN = "#E81123"  # Windows near-full red
_NEAR_FULL = 0.90  # used ratio → red (Windows-ish)


class CapacityBar(QWidget):
    """Flat capacity meter; turns red when nearly full."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ratio = 0.0
        self.setFixedHeight(6)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_ratio(self, used: float, total: float) -> None:
        if total and total > 0:
            self._ratio = max(0.0, min(1.0, float(used) / float(total)))
        else:
            self._ratio = 0.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        r = self.rect()
        track = QRectF(r.x(), r.y(), r.width(), r.height())
        p.fillRect(track, QColor(_BAR_TRACK))
        fill_w = track.width() * self._ratio
        if fill_w > 0:
            color = QColor(_BAR_WARN if self._ratio >= _NEAR_FULL else _BAR_OK)
            p.fillRect(QRectF(track.x(), track.y(), fill_w, track.height()), color)
        p.setPen(QPen(QColor("#D1D5DB"), 1))
        p.drawRect(track.adjusted(0, 0, -1, -1))
        p.end()


class DriveTile(QFrame):
    """One drive / mount card — icon, name, capacity bar, free-of-total."""

    activated = Signal(dict)
    selected = Signal(dict)

    def __init__(self, mount: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._mount = dict(mount)
        self._selected = False
        self.setObjectName("DriveTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(268, 92)
        self._build()
        self._apply_chrome()

    def mount(self) -> dict[str, Any]:
        return dict(self._mount)

    def set_selected(self, on: bool) -> None:
        self._selected = bool(on)
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        border = "#60A5FA" if self._selected else _BORDER
        bg = _SEL if self._selected else _BG
        self.setStyleSheet(
            f"#DriveTile{{background:{bg};border:1px solid {border};border-radius:4px;}}"
        )

    def _build(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        icon = QLabel()
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background:transparent;border:none;")
        role = str(self._mount.get("role") or "primary")
        color = "#1B2B4B" if role == "primary" else "#64748B"
        icon.setPixmap(
            qta.icon("mdi.harddisk", color=color).pixmap(40, 40)
        )
        lay.addWidget(icon, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        name = QLabel(str(self._mount.get("name") or "Storage"))
        name.setStyleSheet(
            f"color:{_TEXT};font-weight:600; font-size:13px; font-family:\"Segoe UI\";background:transparent;border:none;"
        )
        col.addWidget(name)

        free_b = self._mount.get("free_bytes")
        total_b = self._mount.get("total_bytes")
        used_b = self._mount.get("used_bytes")
        if used_b is None and free_b is not None and total_b is not None:
            try:
                used_b = max(0, int(total_b) - int(free_b))
            except (TypeError, ValueError):
                used_b = 0

        bar = CapacityBar()
        try:
            bar.set_ratio(float(used_b or 0), float(total_b or 0))
        except (TypeError, ValueError):
            bar.set_ratio(0, 0)
        col.addWidget(bar)

        free_h = str(self._mount.get("free_human") or "—")
        total_h = str(self._mount.get("total_human") or "—")
        path = str(self._mount.get("path") or self._mount.get("mount_hint") or "")
        sub = f"{free_h} free of {total_h}"
        if path:
            # Keep path soft — Windows shows capacity first
            tip = path
        else:
            tip = sub
        cap = QLabel(sub)
        cap.setStyleSheet(
            f"color:{_MUTED};font-size:11px; font-family:\"Segoe UI\";background:transparent;border:none;"
        )
        cap.setToolTip(tip)
        col.addWidget(cap)
        lay.addLayout(col, 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.mount())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.mount())
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.activated.emit(self.mount())
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._selected:
            self.setStyleSheet(
                f"#DriveTile{{background:{_HOVER};border:1px solid {_BORDER};border-radius:4px;}}"
            )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._apply_chrome()
        super().leaveEvent(event)


class WindowsDevicesAndDrives(QWidget):
    """Collapsible “Mounted storages” section (server-side receipt mounts)."""

    mount_activated = Signal(dict)
    mount_selected = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mounts: list[dict[str, Any]] = []
        self._tiles: list[DriveTile] = []
        self._expanded = True
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(10)
        self.setStyleSheet("background:transparent;")

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(6)
        self._chevron = QToolButton()
        self._chevron.setAutoRaise(True)
        self._chevron.setCursor(Qt.PointingHandCursor)
        self._chevron.setFixedSize(22, 22)
        self._chevron.clicked.connect(self._toggle)
        hdr.addWidget(self._chevron)

        title = QLabel("Mounted storages")
        title.setStyleSheet(
            f"color:{_TEXT};font-weight:600; font-size:13px; font-family:\"Segoe UI\";background:transparent;border:none;"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        root.addLayout(hdr)

        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;border:none;")
        self._flow = QHBoxLayout(self._body)
        self._flow.setContentsMargins(4, 4, 4, 4)
        self._flow.setSpacing(16)
        self._flow.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        root.addWidget(self._body, 0)

        self._empty = QLabel(
            "No server storages are mounted yet. Set a receipt disk path to begin."
        )
        self._empty.setStyleSheet(
            f"color:{_MUTED};font-size:12px; font-family:\"Segoe UI\";background:transparent;"
            "border:none;padding:4px;"
        )
        self._empty.setVisible(False)
        root.addWidget(self._empty)
        root.addStretch(1)
        self._sync_chevron()

    def _sync_chevron(self) -> None:
        icon = "mdi.chevron-down" if self._expanded else "mdi.chevron-right"
        self._chevron.setIcon(qta.icon(icon, color=_MUTED))
        self._body.setVisible(self._expanded)
        self._empty.setVisible(self._expanded and not self._tiles)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._sync_chevron()

    def set_mounts(self, mounts: list[dict[str, Any]]) -> None:
        self._mounts = [dict(m) for m in (mounts or [])]
        while self._flow.count():
            item = self._flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._tiles = []
        for m in self._mounts:
            tile = DriveTile(m)
            tile.activated.connect(self.mount_activated.emit)
            tile.selected.connect(self._on_tile_selected)
            self._flow.addWidget(tile)
            self._tiles.append(tile)
        self._flow.addStretch(1)
        self._sync_chevron()

    def _on_tile_selected(self, mount: dict[str, Any]) -> None:
        mid = str(mount.get("id") or "")
        for tile in self._tiles:
            tile.set_selected(str(tile.mount().get("id") or "") == mid)
        self.mount_selected.emit(mount)

    def select_mount_id(self, mount_id: str) -> None:
        mid = str(mount_id or "")
        for tile in self._tiles:
            tile.set_selected(str(tile.mount().get("id") or "") == mid)


def mounts_from_storage_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Explorer mount tiles from GET /v1/receipts/storage (ready for multi-mount)."""
    if not isinstance(status, dict):
        return []
    raw = status.get("mounts")
    if isinstance(raw, list) and raw:
        return [dict(m) for m in raw if isinstance(m, dict)]

    probe = status.get("probe") if isinstance(status.get("probe"), dict) else {}
    path = str(status.get("effective_path") or probe.get("path") or "").strip()
    free_b = probe.get("free_bytes")
    total_b = probe.get("total_bytes")
    used_b = probe.get("used_bytes")
    if used_b is None and free_b is not None and total_b is not None:
        try:
            used_b = max(0, int(total_b) - int(free_b))
        except (TypeError, ValueError):
            used_b = None
    drive = str(probe.get("drive") or "").strip()
    hint = str(probe.get("mount_hint") or drive or "").strip()
    name = "Receipt disk"
    if drive:
        letter = drive.rstrip(":\\/")
        name = f"Receipt disk ({letter}:)"
    return [
        {
            "id": "primary",
            "name": name,
            "role": "primary",
            "path": path,
            "mount_hint": hint,
            "free_bytes": free_b,
            "total_bytes": total_b,
            "used_bytes": used_b,
            "free_human": probe.get("free_human"),
            "total_human": probe.get("total_human"),
            "used_human": probe.get("used_human"),
            "writable": probe.get("writable"),
            "open_prefix": "",
        }
    ]
