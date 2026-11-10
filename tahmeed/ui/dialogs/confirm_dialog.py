"""Fluent-style confirmation / alert dialogs (Clear Table mockup).

Custom chrome with title bar, status badge, footer actions, parent dimming,
and light/dark variants. Escape and the close button always cancel / dismiss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Literal, Optional, Sequence, Tuple

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

BadgeKind = Literal["warning", "info", "error", "question"]
ActionRole = Literal["primary", "destructive", "secondary", "cancel"]


@dataclass(frozen=True)
class DialogAction:
    """One footer button for ``ask_choice`` / Fluent message dialogs."""

    id: str
    label: str
    role: ActionRole = "secondary"


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


class _StatusBadge(QWidget):
    """32px status circle matching QMessageBox icon scale."""

    def __init__(
        self,
        *,
        kind: BadgeKind = "warning",
        dark: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._dark = dark
        self.setFixedSize(32, 32)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        kind = self._kind
        if kind == "info":
            self._paint_info(p)
        elif kind == "error":
            self._paint_error(p)
        elif kind == "question":
            self._paint_question(p)
        else:
            self._paint_warning(p)
        p.end()

    def _paint_warning(self, p: QPainter) -> None:
        circle = QColor("#433519" if self._dark else "#FFF4CE")
        tri = QColor("#FCE100" if self._dark else "#9D5D00")
        p.setPen(Qt.NoPen)
        p.setBrush(circle)
        p.drawEllipse(0, 0, 32, 32)

        cx, cy = 16.0, 16.5
        half_w, height = 8.5, 15.0
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
            pen = QPen(tri, 1.6)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawPolygon(poly)
            mark = tri

        p.setPen(Qt.NoPen)
        p.setBrush(mark)
        p.drawRoundedRect(QRectF(cx - 0.9, cy - 4.5, 1.8, 6.0), 0.8, 0.8)
        p.drawEllipse(QPointF(cx, cy + 4.0), 1.2, 1.2)

    def _paint_info(self, p: QPainter) -> None:
        circle = QColor("#0C3B5E" if self._dark else "#E8F4FC")
        mark = QColor("#479EF5" if self._dark else "#0077C5")
        p.setPen(Qt.NoPen)
        p.setBrush(circle)
        p.drawEllipse(0, 0, 32, 32)
        p.setBrush(mark)
        p.drawEllipse(QPointF(16.0, 10.0), 1.6, 1.6)
        p.drawRoundedRect(QRectF(14.4, 14.0, 3.2, 10.5), 1.0, 1.0)

    def _paint_error(self, p: QPainter) -> None:
        circle = QColor("#5C1A16" if self._dark else "#FDE7E9")
        mark = QColor("#FF99A4" if self._dark else "#C42B1C")
        p.setPen(Qt.NoPen)
        p.setBrush(circle)
        p.drawEllipse(0, 0, 32, 32)
        # X mark
        pen = QPen(mark, 2.4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(11.0, 11.0), QPointF(21.0, 21.0))
        p.drawLine(QPointF(21.0, 11.0), QPointF(11.0, 21.0))

    def _paint_question(self, p: QPainter) -> None:
        circle = QColor("#0C3B5E" if self._dark else "#E8F4FC")
        mark = QColor("#479EF5" if self._dark else "#0077C5")
        p.setPen(Qt.NoPen)
        p.setBrush(circle)
        p.drawEllipse(0, 0, 32, 32)
        p.setPen(QPen(mark, 2.0))
        p.setBrush(Qt.NoBrush)
        # Simplified "?"
        p.drawArc(QRectF(10.5, 8.0, 11.0, 10.0), 30 * 16, 240 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(mark)
        p.drawEllipse(QPointF(16.0, 23.0), 1.5, 1.5)


# Back-compat alias used by older tests / imports.
_WarningBadge = _StatusBadge


class _FocusRingHost(QWidget):
    """Wraps Cancel so a 2px focus ring can sit 2px outside the button.

    Must stay strongly referenced from ConfirmDialog: PySide can GC the Python
    wrapper while Qt still delivers focus events, and a resurrected wrapper has
    no ``_button`` — which used to raise inside ``eventFilter`` and abort the
    Daily Register post-save reload.
    """

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
        button = getattr(self, "_button", None)
        if button is not None and watched is button and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        ):
            self.update()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        button = getattr(self, "_button", None)
        if button is None:
            return
        try:
            focused = button.hasFocus()
        except RuntimeError:
            return
        if not focused:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self._ring, 2.0)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = QRectF(button.geometry()).adjusted(-2.0, -2.0, 2.0, 2.0)
        p.drawRoundedRect(r, 6.0, 6.0)
        p.end()


class FluentMessageDialog(QDialog):
    """Frameless Fluent card with status badge + flexible footer actions."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str,
        heading: str,
        body: str = "",
        note: str = "",
        actions: Sequence[DialogAction],
        badge: BadgeKind = "warning",
        default_action_id: Optional[str] = None,
        dismiss_id: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        if not actions:
            raise ValueError("FluentMessageDialog requires at least one action")
        self._dark = is_dark_theme()
        self._overlay: Optional[QWidget] = None
        self._chosen_id: Optional[str] = None
        self._actions = list(actions)
        # Escape / ✕ map here — prefer explicit dismiss, else cancel-role, else first.
        self._cancel_id = (
            dismiss_id
            or next((a.id for a in self._actions if a.role == "cancel"), None)
            or self._actions[0].id
        )
        self.setObjectName("fluentConfirmDialog")
        self.setModal(True)
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
        )
        # Frameless root needs translucency for rounded shadow; card itself is opaque.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui(
            title=title,
            heading=heading,
            body=body,
            note=note,
            actions=self._actions,
            badge=badge,
            default_action_id=default_action_id,
        )

    def chosen_id(self) -> Optional[str]:
        return self._chosen_id

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

    def _button_style(self, role: ActionRole, c: dict[str, str]) -> str:
        if role in ("primary", "destructive"):
            bg = c["accept"] if role == "destructive" else c["accept_primary"]
            return f"""
            QPushButton {{
                background: {bg};
                color: #FFFFFF;
                border: 1px solid {bg};
                border-radius: 5px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 600;
                font-family: {_FONT};
            }}
            QPushButton:hover {{
                background: {bg};
            }}
            """
        return f"""
        QPushButton {{
            background: {c['cancel_bg']};
            color: {c['cancel_fg']};
            border: 1px solid {c['cancel_border']};
            border-radius: 5px;
            padding: 0 16px;
            font-size: 13px;
            font-weight: 600;
            font-family: {_FONT};
        }}
        QPushButton:hover {{
            background: {c['close_hover']};
        }}
        """

    def _build_ui(
        self,
        *,
        title: str,
        heading: str,
        body: str,
        note: str,
        actions: List[DialogAction],
        badge: BadgeKind,
        default_action_id: Optional[str],
    ) -> None:
        c = self._colors()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 18)
        outer.setSpacing(0)

        card = QWidget(self)
        card.setObjectName("confirmCard")
        card.setAttribute(Qt.WA_TranslucentBackground, False)
        card.setAttribute(Qt.WA_NativeWindow, False)
        # Wider when 3+ long labels so buttons stay one row.
        card.setFixedWidth(420 if len(actions) >= 3 else 380)
        card.setStyleSheet(
            f"""
            QWidget#confirmCard {{
                background: {c['card']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                font-family: {_FONT};
            }}
            QLabel {{
                background: transparent;
                font-family: {_FONT};
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 56))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = QWidget(card)
        title_bar.setFixedHeight(32)
        title_bar.setStyleSheet("background: transparent;")
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(10, 0, 0, 0)
        title_row.setSpacing(6)

        icon_lbl = QLabel(title_bar)
        icon_lbl.setFixedSize(14, 14)
        app = QApplication.instance()
        if app is not None and not app.windowIcon().isNull():
            icon_lbl.setPixmap(app.windowIcon().pixmap(14, 14))
        title_row.addWidget(icon_lbl, 0, Qt.AlignVCenter)

        title_lbl = QLabel(title, title_bar)
        title_lbl.setStyleSheet(
            f"color: {c['title']}; font-size: 12px; font-family: {_FONT};"
        )
        title_row.addWidget(title_lbl, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        close_btn = QPushButton("✕", title_bar)
        close_btn.setFixedSize(36, 32)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFlat(True)
        close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-top-right-radius: 6px;
                color: {c['title']};
                font-size: 11px;
                font-family: {_FONT};
            }}
            QPushButton:hover {{
                background: {c['close_hover']};
            }}
            """
        )
        close_btn.clicked.connect(self._on_dismiss)
        title_row.addWidget(close_btn, 0, Qt.AlignTop)
        card_layout.addWidget(title_bar)

        # ── Body ──────────────────────────────────────────────────────────
        body_wrap = QWidget(card)
        body_wrap.setStyleSheet("background: transparent;")
        body_row = QHBoxLayout(body_wrap)
        body_row.setContentsMargins(16, 6, 16, 14)
        body_row.setSpacing(12)

        body_row.addWidget(
            _StatusBadge(kind=badge, dark=self._dark, parent=body_wrap),
            0,
            Qt.AlignTop,
        )

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        heading_lbl = QLabel(heading, body_wrap)
        heading_lbl.setWordWrap(True)
        heading_lbl.setStyleSheet(
            f"color: {c['heading']}; font-size: 13px; font-weight: 600; "
            f"font-family: {_FONT};"
        )
        text_col.addWidget(heading_lbl)

        if body:
            body_lbl = QLabel(body, body_wrap)
            body_lbl.setWordWrap(True)
            body_lbl.setStyleSheet(
                f"color: {c['body']}; font-size: 13px; font-family: {_FONT};"
            )
            text_col.addWidget(body_lbl)

        if note:
            note_lbl = QLabel(note, body_wrap)
            note_lbl.setWordWrap(True)
            note_lbl.setStyleSheet(
                f"color: {c['note']}; font-size: 12px; font-family: {_FONT};"
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
                border-bottom-left-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            """
        )
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(12, 10, 12, 10)
        footer_row.setSpacing(6)
        footer_row.addStretch(1)

        self._buttons: dict[str, QPushButton] = {}
        self._cancel_btn: Optional[QPushButton] = None
        self._accept_btn: Optional[QPushButton] = None
        self._cancel_ring_host: Optional[_FocusRingHost] = None

        default_id = default_action_id
        if default_id is None:
            cancel_actions = [a for a in actions if a.role == "cancel"]
            default_id = cancel_actions[0].id if cancel_actions else actions[-1].id

        for action in actions:
            btn = QPushButton(action.label, footer)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setMinimumWidth(72)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setStyleSheet(self._button_style(action.role, c))
            btn.clicked.connect(self._make_chooser(action.id))
            self._buttons[action.id] = btn
            if action.role == "cancel":
                self._cancel_btn = btn
                self._cancel_ring_host = _FocusRingHost(
                    btn, ring=c["ring"], parent=footer
                )
                footer_row.addWidget(self._cancel_ring_host)
            else:
                if action.role in ("primary", "destructive"):
                    self._accept_btn = btn
                footer_row.addWidget(btn)

        card_layout.addWidget(footer)
        outer.addWidget(card)

        default_btn = self._buttons.get(default_id) or next(iter(self._buttons.values()))
        default_btn.setDefault(True)
        default_btn.setFocus(Qt.TabFocusReason)
        self.adjustSize()

    def _make_chooser(self, action_id: str) -> Callable[[], None]:
        def _choose() -> None:
            self._chosen_id = action_id
            role = next(
                (a.role for a in self._actions if a.id == action_id),
                "secondary",
            )
            # Cancel-role closes as Rejected; every other action is Accepted so
            # callers can read chosen_id even when dismiss_id == primary OK.
            if role == "cancel":
                self.reject()
            else:
                self.accept()

        return _choose

    def _on_dismiss(self) -> None:
        self._chosen_id = self._cancel_id
        self.reject()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._on_dismiss()
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
        overlay.setAttribute(Qt.WA_TranslucentBackground, False)
        overlay.setAttribute(Qt.WA_NativeWindow, False)
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


class ConfirmDialog(FluentMessageDialog):
    """Modal confirm matching the Clear Table Fluent mockup (2-button)."""

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
        accept_role: ActionRole = "destructive" if destructive else "primary"
        actions = (
            DialogAction("cancel", cancel_label, "cancel"),
            DialogAction("accept", accept_label, accept_role),
        )
        super().__init__(
            parent,
            title=title,
            heading=heading,
            body=body,
            note=note,
            actions=actions,
            badge="warning",
            default_action_id="cancel" if default_cancel else "accept",
        )


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


def ask_alert(
    parent: Optional[QWidget],
    *,
    title: str,
    text: str,
    level: BadgeKind = "info",
    ok_label: str = "OK",
) -> bool:
    """Fluent OK-only alert (info / warning / error / question).

    Returns ``True`` when OK is clicked; Escape / close also dismisses (``False``
    is rare for patched ``QMessageBox.information`` which always maps to Ok).
    """
    heading, body, note = _split_message(text)
    dlg = FluentMessageDialog(
        parent,
        title=title,
        heading=heading or title,
        body=body,
        note=note,
        actions=(DialogAction("ok", ok_label, "primary"),),
        badge=level,
        default_action_id="ok",
    )
    result = dlg.exec()
    return result == QDialog.DialogCode.Accepted or dlg.chosen_id() == "ok"


def ask_choice(
    parent: Optional[QWidget],
    *,
    title: str,
    text: str = "",
    heading: str = "",
    body: str = "",
    note: str = "",
    actions: Sequence[DialogAction],
    badge: BadgeKind = "question",
    default_action_id: Optional[str] = None,
    dismiss_id: Optional[str] = None,
) -> Optional[str]:
    """Fluent multi-action dialog. Returns the chosen action ``id``, or dismiss id."""
    if text and not (heading or body or note):
        heading, body, note = _split_message(text)
    dlg = FluentMessageDialog(
        parent,
        title=title,
        heading=heading or title,
        body=body,
        note=note,
        actions=actions,
        badge=badge,
        default_action_id=default_action_id,
        dismiss_id=dismiss_id,
    )
    dlg.exec()
    return dlg.chosen_id()


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
    ok = QMessageBox.StandardButton.Ok

    has_yes = bool(buttons & yes)
    has_no = bool(buttons & no)
    has_cancel = bool(buttons & cancel)
    has_discard = bool(buttons & discard)
    has_ok = bool(buttons & ok)

    # OK-only → alert chrome.
    if has_ok and not (has_yes or has_no or has_cancel or has_discard):
        ask_alert(parent, title=title, text=text, level="question")
        return int(ok)

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
        cancel_label="Cancel" if has_cancel or has_discard else ("No" if has_no else "Cancel"),
        destructive=destructive,
        default_cancel=default_cancel,
    )
    return accept_result if accepted else reject_result
