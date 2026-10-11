"""Fluent-style confirmation dialog (Clear Table mockup).

Custom chrome with title bar, warning badge, footer actions, parent dimming,
and light/dark variants. Escape and the close button always cancel.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tahmeed.ui.branding import is_dark_theme

_FONT = '"Segoe UI Variable", "Segoe UI"'
_DESTRUCTIVE_RE = re.compile(
    r"\b(delete|clear|discard|remove|ban|permanently|wipe|replace|force logout)\b",
    re.IGNORECASE,
)


def _split_message(text: str) -> Tuple[str, str, str]:
    """Split message box text into heading / body / note."""
    cleaned = (text or "").replace("\r\n", "\n").strip()
    if not cleaned:
        return "", "", ""
    parts = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    if len(parts) == 1:
        lines = parts[0].split("\n")
        if len(lines) > 1:
            return lines[0].strip(), "\n".join(lines[1:]).strip(), ""
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], "\n\n".join(parts[2:])


def _is_destructive(title: str, text: str, accept_label: str) -> bool:
    blob = f"{title} {text} {accept_label}"
    return bool(_DESTRUCTIVE_RE.search(blob))


class _WarningBadge(QWidget):
    """44px circle with a 24px warning triangle."""

    def __init__(self, *, dark: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._dark = dark
        self.setFixedSize(44, 44)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        circle = QColor("#433519" if self._dark else "#FFF4CE")
        tri = QColor("#FCE100" if self._dark else "#9D5D00")
        p.setPen(Qt.NoPen)
        p.setBrush(circle)
        p.drawEllipse(0, 0, 44, 44)

        # ~24px triangle centered in the 44px circle
        cx, cy = 22.0, 23.0
        half_w, height = 12.0, 21.0
        top = QPointF(cx, cy - height / 2)
        bl = QPointF(cx - half_w, cy + height / 2)
        br = QPointF(cx + half_w, cy + height / 2)
        poly = QPolygonF([top, br, bl])

        if self._dark:
            p.setBrush(tri)
            p.setPen(Qt.NoPen)
            p.drawPolygon(poly)
            mark = QColor("#2B2B2B")
        else:
            p.setBrush(Qt.NoBrush)
            pen = QPen(tri, 2.0)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawPolygon(poly)
            mark = tri

        # Exclamation mark
        p.setPen(Qt.NoPen)
        p.setBrush(mark)
        p.drawRoundedRect(QRectF(cx - 1.2, cy - 6.5, 2.4, 8.5), 1.0, 1.0)
        p.drawEllipse(QPointF(cx, cy + 5.5), 1.6, 1.6)
        p.end()


class _FocusRingHost(QWidget):
    """Wraps Cancel so a 2px focus ring can sit 2px outside the button."""

    def __init__(self, button: QPushButton, *, ring: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ring = QColor(ring)
        self._button = button
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        lay.addWidget(button)
        button.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._button and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        ):
            self.update()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        if not self._button.hasFocus():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._ring, 2.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # Button geometry is inset by 4px; ring at 2px offset → 2px from host edge.
        r = QRectF(self._button.geometry()).adjusted(-2.0, -2.0, 2.0, 2.0)
        p.drawRoundedRect(r, 6.0, 6.0)
        p.end()


class ConfirmDialog(QDialog):
    """Modal confirm matching the Clear Table Fluent mockup."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str,
        heading: str,
        body: str = "",
        note: str = "",
        accept_label: str = "Yes",
        cancel_label: str = "Cancel",
        destructive: bool = True,
        default_cancel: bool = True,
    ) -> None:
        super().__init__(parent)
        self._dark = is_dark_theme()
        self._overlay: Optional[QWidget] = None
        self.setObjectName("fluentConfirmDialog")
        self.setModal(True)
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui(
            title=title,
            heading=heading,
            body=body,
            note=note,
            accept_label=accept_label,
            cancel_label=cancel_label,
            destructive=destructive,
            default_cancel=default_cancel,
        )

    def _colors(self) -> dict[str, str]:
        if self._dark:
            return {
                "card": "#2B2B2B",
                "border": "#454545",
                "footer": "#202020",
                "footer_border": "#3A3A3A",
                "title": "#FFFFFF",
                "heading": "#FFFFFF",
                "body": "#E0E0E0",
                "note": "#B3B3B3",
                "cancel_bg": "#3A3A3A",
                "cancel_border": "#5A5A5A",
                "cancel_fg": "#FFFFFF",
                "close_hover": "#3A3A3A",
                "ring": "#479EF5",
                "accept": "#C42B1C",
                "accept_primary": "#0F6CBD",
            }
        return {
            "card": "#FFFFFF",
            "border": "#D6D6D6",
            "footer": "#F3F3F3",
            "footer_border": "#E5E5E5",
            "title": "#1B1B1B",
            "heading": "#1B1B1B",
            "body": "#3D3D3D",
            "note": "#5C5C5C",
            "cancel_bg": "#FFFFFF",
            "cancel_border": "#C8C8C8",
            "cancel_fg": "#1B1B1B",
            "close_hover": "#F3F3F3",
            "ring": "#0F6CBD",
            "accept": "#C42B1C",
            "accept_primary": "#0F6CBD",
        }

    def _build_ui(
        self,
        *,
        title: str,
        heading: str,
        body: str,
        note: str,
        accept_label: str,
        cancel_label: str,
        destructive: bool,
        default_cancel: bool,
    ) -> None:
        c = self._colors()
        outer = QVBoxLayout(self)
        # Room for drop shadow around the card
        outer.setContentsMargins(24, 20, 24, 28)
        outer.setSpacing(0)

        card = QWidget(self)
        card.setObjectName("confirmCard")
        card.setFixedWidth(440)
        card.setStyleSheet(
            f"""
            QWidget#confirmCard {{
                background: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                font-family: {_FONT};
            }}
            QLabel {{
                background: transparent;
                font-family: {_FONT};
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 71))  # ~28% black
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = QWidget(card)
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet("background: transparent;")
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(12, 0, 0, 0)
        title_row.setSpacing(8)

        icon_lbl = QLabel(title_bar)
        icon_lbl.setFixedSize(16, 16)
        app = QApplication.instance()
        if app is not None and not app.windowIcon().isNull():
            icon_lbl.setPixmap(app.windowIcon().pixmap(16, 16))
        title_row.addWidget(icon_lbl, 0, Qt.AlignVCenter)

        title_lbl = QLabel(title, title_bar)
        title_lbl.setStyleSheet(
            f"color: {c['title']}; font-size: 12px; font-family: {_FONT};"
        )
        title_row.addWidget(title_lbl, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        close_btn = QPushButton("✕", title_bar)
        close_btn.setFixedSize(46, 40)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFlat(True)
        close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-top-right-radius: 8px;
                color: {c['title']};
                font-size: 12px;
                font-family: {_FONT};
            }}
            QPushButton:hover {{
                background: {c['close_hover']};
            }}
            """
        )
        close_btn.clicked.connect(self.reject)
        title_row.addWidget(close_btn, 0, Qt.AlignTop)
        card_layout.addWidget(title_bar)

        # ── Body ──────────────────────────────────────────────────────────
        body_wrap = QWidget(card)
        body_wrap.setStyleSheet("background: transparent;")
        body_row = QHBoxLayout(body_wrap)
        body_row.setContentsMargins(28, 12, 28, 28)
        body_row.setSpacing(16)

        body_row.addWidget(_WarningBadge(dark=self._dark, parent=body_wrap), 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(8)

        heading_lbl = QLabel(heading, body_wrap)
        heading_lbl.setWordWrap(True)
        heading_lbl.setStyleSheet(
            f"color: {c['heading']}; font-size: 18px; font-weight: 600; "
            f"font-family: {_FONT};"
        )
        text_col.addWidget(heading_lbl)

        if body:
            body_lbl = QLabel(body, body_wrap)
            body_lbl.setWordWrap(True)
            body_lbl.setStyleSheet(
                f"color: {c['body']}; font-size: 14px; font-family: {_FONT};"
            )
            text_col.addWidget(body_lbl)

        if note:
            note_lbl = QLabel(note, body_wrap)
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet(
                f"color: {c['note']}; font-size: 13px; font-family: {_FONT};"
            )
            text_col.addWidget(note_lbl)

        text_col.addStretch(1)
        body_row.addLayout(text_col, 1)
        card_layout.addWidget(body_wrap)

        # ── Footer ────────────────────────────────────────────────────────
        footer = QWidget(card)
        footer.setStyleSheet(
            f"""
            QWidget {{
                background: {c['footer']};
                border-top: 1px solid {c['footer_border']};
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }}
            """
        )
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(20, 16, 20, 16)
        footer_row.setSpacing(8)
        footer_row.addStretch(1)

        cancel_btn = QPushButton(cancel_label, footer)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFixedHeight(36)
        cancel_btn.setMinimumWidth(96)
        cancel_btn.setFocusPolicy(Qt.StrongFocus)
        cancel_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {c['cancel_bg']};
                color: {c['cancel_fg']};
                border: 1px solid {c['cancel_border']};
                border-radius: 4px;
                padding: 0 16px;
                font-size: 14px;
                font-weight: 600;
                font-family: {_FONT};
            }}
            QPushButton:hover {{
                background: {c['close_hover']};
            }}
            """
        )
        cancel_btn.clicked.connect(self.reject)
        footer_row.addWidget(
            _FocusRingHost(cancel_btn, ring=c["ring"], parent=footer)
        )

        accept_bg = c["accept"] if destructive else c["accept_primary"]
        accept_btn = QPushButton(accept_label, footer)
        accept_btn.setCursor(Qt.PointingHandCursor)
        accept_btn.setFixedHeight(36)
        accept_btn.setMinimumWidth(112)
        accept_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {accept_bg};
                color: #FFFFFF;
                border: 1px solid {accept_bg};
                border-radius: 4px;
                padding: 0 16px;
                font-size: 14px;
                font-weight: 600;
                font-family: {_FONT};
            }}
            QPushButton:hover {{
                background: {accept_bg};
            }}
            """
        )
        accept_btn.clicked.connect(self.accept)
        footer_row.addWidget(accept_btn)

        card_layout.addWidget(footer)
        outer.addWidget(card)

        if default_cancel:
            cancel_btn.setDefault(True)
            cancel_btn.setFocus(Qt.TabFocusReason)
        else:
            accept_btn.setDefault(True)
            accept_btn.setFocus(Qt.TabFocusReason)

        self._cancel_btn = cancel_btn
        self._accept_btn = accept_btn
        self.adjustSize()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _show_overlay(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        host = parent.window()
        if host is None:
            return
        overlay = QWidget(host)
        overlay.setObjectName("confirmDimOverlay")
        overlay.setAttribute(Qt.WA_StyledBackground, True)
        overlay.setStyleSheet("background-color: rgba(0, 0, 0, 71);")
        overlay.setGeometry(0, 0, host.width(), host.height())
        overlay.show()
        overlay.raise_()
        self._overlay = overlay

    def _hide_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None

    def exec(self) -> int:  # noqa: A003
        self._show_overlay()
        try:
            return super().exec()
        finally:
            self._hide_overlay()


def ask_confirm(
    parent: Optional[QWidget],
    *,
    title: str,
    heading: str,
    body: str = "",
    note: str = "",
    accept_label: str = "Yes",
    cancel_label: str = "Cancel",
    destructive: bool = True,
    default_cancel: bool = True,
) -> bool:
    """Show the Fluent confirm dialog. ``True`` = accept (Yes / Clear)."""
    dlg = ConfirmDialog(
        parent,
        title=title,
        heading=heading,
        body=body,
        note=note,
        accept_label=accept_label,
        cancel_label=cancel_label,
        destructive=destructive,
        default_cancel=default_cancel,
    )
    return dlg.exec() == QDialog.DialogCode.Accepted


def ask_standard_question(
    parent: Optional[QWidget],
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton = (
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    ),
    default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    *,
    destructive: Optional[bool] = None,
) -> int:
    """Fluent confirm for standard Yes/No/Cancel/Discard sets.

    Returns a ``QMessageBox.StandardButton`` value. Cancel/close/Escape map to
    ``No`` when the set includes No, otherwise ``Cancel``.
    """
    yes = QMessageBox.StandardButton.Yes
    no = QMessageBox.StandardButton.No
    cancel = QMessageBox.StandardButton.Cancel
    discard = QMessageBox.StandardButton.Discard

    has_yes = bool(buttons & yes)
    has_no = bool(buttons & no)
    has_cancel = bool(buttons & cancel)
    has_discard = bool(buttons & discard)

    # Only handle simple two-button confirms with this chrome.
    simple = (
        (has_yes and has_no and not has_discard and not has_cancel)
        or (has_yes and has_cancel and not has_no and not has_discard)
        or (has_discard and has_cancel and not has_yes and not has_no)
    )
    if not simple:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, buttons, parent)
        if default_button != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(default_button)
        from tahmeed.ui.dialog_theme import style_message_box

        return style_message_box(box).exec()

    heading, body, note = _split_message(text)
    if has_discard:
        accept_label = "Discard"
        accept_result = discard
        reject_result = cancel
    else:
        accept_label = "Yes"
        accept_result = yes
        reject_result = no if has_no else cancel

    if destructive is None:
        destructive = _is_destructive(title, text, accept_label)

    default_cancel = True
    if default_button in (yes, discard):
        default_cancel = False
    elif default_button in (no, cancel):
        default_cancel = True

    accepted = ask_confirm(
        parent,
        title=title,
        heading=heading or title,
        body=body,
        note=note,
        accept_label=accept_label,
        cancel_label="Cancel",
        destructive=destructive,
        default_cancel=default_cancel,
    )
    return accept_result if accepted else reject_result
