import asyncio
import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QCheckBox, QFrame, QSizePolicy,
    QToolButton, QGraphicsDropShadowEffect, QMessageBox,
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QSettings, QRectF, QPointF, QSize, QEvent, QStandardPaths,
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QLinearGradient, QRadialGradient,
    QPen, QPixmap, QIcon,
)

from tahmeed.config import APP_NAME, APP_VERSION
from tahmeed.services.auth import authenticate, any_user_exists, create_user
from tahmeed.services.api_client import ApiError
from tahmeed.services.password_policy import (
    password_placeholder,
    password_policy_errors,
)
from tahmeed.ui.branding import is_dark_theme, load_brand_logo

_SETTINGS_ORG = "Tahmeed"
_SETTINGS_APP = "meedexpe"
_SETTINGS_USER_KEY = "login/remember_username"
_SETTINGS_REMEMBER_KEY = "login/remember_me"


@dataclass(frozen=True)
class _Theme:
    dark: bool
    bg_left: str
    bg_left_top: str
    bg_left_bottom: str
    bg_right: str
    blue: str
    blue_hot: str
    blue_hover: str
    blue_press: str
    blue_soft: str
    ink: str
    muted: str
    field_bg: str
    field_border: str
    field_hover: str
    field_focus: str
    err: str
    accent_rgb: tuple[int, int, int]
    glow_rgb: tuple[int, int, int]
    wave_fill_bottom: tuple[int, int, int]
    lattice_alpha: int
    ambient_alpha: int
    btn_shadow_alpha: int
    forgot_hover: str


def _theme() -> _Theme:
    if is_dark_theme():
        return _Theme(
            dark=True,
            bg_left="#070B14",
            bg_left_top="#05080F",
            bg_left_bottom="#0A1528",
            bg_right="#0C1220",
            blue="#1A8CFF",
            blue_hot="#3AA0FF",
            blue_hover="#1576DB",
            blue_press="#0F5FB3",
            blue_soft="#1E3A5F",
            ink="#F4F7FB",
            muted="#8B95A8",
            field_bg="#121A2A",
            field_border="#2A3548",
            field_hover="#3A4A63",
            field_focus="#1A8CFF",
            err="#F07178",
            accent_rgb=(26, 140, 255),
            glow_rgb=(58, 160, 255),
            wave_fill_bottom=(5, 10, 20),
            lattice_alpha=18,
            ambient_alpha=48,
            btn_shadow_alpha=90,
            forgot_hover="#7EC4FF",
        )
    return _Theme(
        dark=False,
        bg_left="#E8F2FC",
        bg_left_top="#F5FAFF",
        bg_left_bottom="#D6E8F8",
        bg_right="#F7FAFD",
        blue="#0077C5",
        blue_hot="#0088E0",
        blue_hover="#005EA3",
        blue_press="#004B82",
        blue_soft="#A8C8E0",
        ink="#1B2B4B",
        muted="#64748B",
        field_bg="#FFFFFF",
        field_border="#CBD5E1",
        field_hover="#94A3B8",
        field_focus="#0077C5",
        err="#DC2626",
        accent_rgb=(0, 119, 197),
        glow_rgb=(26, 140, 255),
        wave_fill_bottom=(220, 235, 250),
        lattice_alpha=28,
        ambient_alpha=40,
        btn_shadow_alpha=55,
        forgot_hover="#005EA3",
    )


def _logo_pixmap(width: int, theme: _Theme) -> QPixmap:
    return load_brand_logo(width, for_dark_bg=theme.dark)


def _check_icon_url() -> str:
    """White checkmark for the Remember-me checkbox (stylesheet image url)."""
    cache = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation))
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "meedexpe_login_check.png"
    if not path.is_file():
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(3.5, 8.5), QPointF(6.5, 11.5))
        p.drawLine(QPointF(6.5, 11.5), QPointF(12.5, 4.5))
        p.end()
        pm.save(str(path), "PNG")
    return path.as_posix()


def _icon_pixmap(kind: str, size: int = 18, color: str = "#8B95A8") -> QPixmap:
    """Tiny line icons drawn to pixmap (user / lock / eye / login)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = float(size)

    if kind == "user":
        p.drawEllipse(QPointF(s * 0.5, s * 0.36), s * 0.18, s * 0.18)
        path = QPainterPath()
        path.moveTo(s * 0.18, s * 0.88)
        path.cubicTo(s * 0.18, s * 0.62, s * 0.82, s * 0.62, s * 0.82, s * 0.88)
        p.drawPath(path)
    elif kind == "lock":
        p.drawRoundedRect(QRectF(s * 0.22, s * 0.42, s * 0.56, s * 0.42), 2.5, 2.5)
        arc = QPainterPath()
        arc.moveTo(s * 0.32, s * 0.42)
        arc.cubicTo(s * 0.32, s * 0.14, s * 0.68, s * 0.14, s * 0.68, s * 0.42)
        p.drawPath(arc)
        p.drawEllipse(QPointF(s * 0.5, s * 0.62), 1.4, 1.4)
        p.drawLine(QPointF(s * 0.5, s * 0.64), QPointF(s * 0.5, s * 0.74))
    elif kind == "eye":
        path = QPainterPath()
        path.moveTo(s * 0.12, s * 0.5)
        path.cubicTo(s * 0.28, s * 0.22, s * 0.72, s * 0.22, s * 0.88, s * 0.5)
        path.cubicTo(s * 0.72, s * 0.78, s * 0.28, s * 0.78, s * 0.12, s * 0.5)
        p.drawPath(path)
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.12, s * 0.12)
    elif kind == "eye_off":
        path = QPainterPath()
        path.moveTo(s * 0.12, s * 0.5)
        path.cubicTo(s * 0.28, s * 0.22, s * 0.72, s * 0.22, s * 0.88, s * 0.5)
        path.cubicTo(s * 0.72, s * 0.78, s * 0.28, s * 0.78, s * 0.12, s * 0.5)
        p.drawPath(path)
        p.drawEllipse(QPointF(s * 0.5, s * 0.5), s * 0.12, s * 0.12)
        p.drawLine(QPointF(s * 0.2, s * 0.8), QPointF(s * 0.8, s * 0.2))
    elif kind == "login":
        p.drawLine(QPointF(s * 0.18, s * 0.22), QPointF(s * 0.18, s * 0.78))
        p.drawLine(QPointF(s * 0.18, s * 0.22), QPointF(s * 0.34, s * 0.22))
        p.drawLine(QPointF(s * 0.18, s * 0.78), QPointF(s * 0.34, s * 0.78))
        p.drawLine(QPointF(s * 0.38, s * 0.5), QPointF(s * 0.82, s * 0.5))
        p.drawLine(QPointF(s * 0.66, s * 0.32), QPointF(s * 0.82, s * 0.5))
        p.drawLine(QPointF(s * 0.66, s * 0.68), QPointF(s * 0.82, s * 0.5))
    p.end()
    return pm


class _BrandPanel(QWidget):
    """Left pane: logo + layered geometry (waves, arcs, glow divider)."""

    def __init__(self, theme: _Theme, parent=None):
        super().__init__(parent)
        self._t = theme
        self.setObjectName("brandPanel")
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(0)
        root.addStretch(2)

        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setAttribute(Qt.WA_TranslucentBackground, True)
        self._logo.setStyleSheet("background: transparent;")
        pix = _logo_pixmap(200, theme)
        if not pix.isNull():
            self._logo.setPixmap(pix)
        root.addWidget(self._logo)

        root.addSpacing(12)

        powered = QLabel(f"Powered by {APP_NAME}")
        powered.setAlignment(Qt.AlignCenter)
        powered.setStyleSheet(
            f"color: {theme.muted}; font-size: 12px; letter-spacing: 0.4px;"
            "background: transparent;"
        )
        root.addWidget(powered)
        root.addStretch(3)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = self._t
        ar, ag, ab = t.accent_rgb
        gr, gg, gb = t.glow_rgb

        base = QLinearGradient(0, 0, w * 0.2, h)
        base.setColorAt(0.0, QColor(t.bg_left_top))
        base.setColorAt(0.45, QColor(t.bg_left))
        base.setColorAt(1.0, QColor(t.bg_left_bottom))
        p.fillRect(self.rect(), base)

        ambient = QRadialGradient(w * 0.48, h * 0.38, max(w, h) * 0.55)
        ambient.setColorAt(0.0, QColor(ar, ag, ab, t.ambient_alpha))
        ambient.setColorAt(0.45, QColor(ar, ag, ab, max(8, t.ambient_alpha // 3)))
        ambient.setColorAt(1.0, QColor(ar, ag, ab, 0))
        p.fillRect(self.rect(), ambient)

        p.save()
        lattice = QPen(QColor(ar, ag, ab, t.lattice_alpha))
        lattice.setWidthF(1.0)
        p.setPen(lattice)
        step = 28
        for y in range(-step, int(h * 0.62), step):
            for x in range(-step, w + step, step):
                cx = x + (step // 2 if (y // step) % 2 else 0)
                cy = y
                p.drawLine(cx, cy - 8, cx + 10, cy)
                p.drawLine(cx + 10, cy, cx, cy + 8)
                p.drawLine(cx, cy + 8, cx - 10, cy)
                p.drawLine(cx - 10, cy, cx, cy - 8)
        p.restore()

        p.save()
        cx, cy = w * 0.55, h * 0.32
        for i, (r, a) in enumerate(((90, 22), (130, 16), (175, 12), (225, 8))):
            alpha = a if t.dark else min(a + 10, 48)
            pen = QPen(QColor(gr, gg, gb, alpha))
            pen.setWidthF(1.2 if i % 2 == 0 else 0.8)
            p.setPen(pen)
            p.drawArc(
                QRectF(cx - r, cy - r, r * 2, r * 2),
                int(-35 * 16),
                int(95 * 16),
            )
        tick = QPen(QColor(gr, gg, gb, 40 if t.dark else 55))
        tick.setWidthF(1.4)
        p.setPen(tick)
        for deg in range(-30, 70, 12):
            rad = math.radians(deg)
            x0 = cx + math.cos(rad) * 218
            y0 = cy + math.sin(rad) * 218
            x1 = cx + math.cos(rad) * 232
            y1 = cy + math.sin(rad) * 232
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.restore()

        p.save()
        for i, (y0, length, alpha) in enumerate(
            (
                (h * 0.18, w * 0.55, 28),
                (h * 0.22, w * 0.42, 18),
                (h * 0.26, w * 0.62, 12),
                (h * 0.55, w * 0.38, 16),
                (h * 0.58, w * 0.50, 10),
            )
        ):
            a = alpha if t.dark else alpha + 8
            grad = QLinearGradient(0, y0, length, y0 + length * 0.08)
            grad.setColorAt(0.0, QColor(ar, ag, ab, 0))
            grad.setColorAt(0.4, QColor(ar, ag, ab, a))
            grad.setColorAt(1.0, QColor(ar, ag, ab, 0))
            pen = QPen(grad, 1.5 if i % 2 == 0 else 1.0)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(20, y0), QPointF(20 + length, y0 + length * 0.08))
        p.restore()

        self._paint_waves(p, w, h)

        for ox, oy, r, a in (
            (w * 0.18, h * 0.72, 70, 22),
            (w * 0.72, h * 0.78, 50, 16),
            (w * 0.42, h * 0.88, 90, 12),
        ):
            orb = QRadialGradient(ox, oy, r)
            orb.setColorAt(0.0, QColor(gr, gg, gb, a if t.dark else a + 6))
            orb.setColorAt(1.0, QColor(gr, gg, gb, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(orb)
            p.drawEllipse(QPointF(ox, oy), r, r)

        p.save()
        p.setPen(Qt.NoPen)
        seed = (w * 17 + h * 31) & 0xFFFF
        for i in range(42):
            n = (seed * (i + 3) * 1103515245 + 12345) & 0x7FFFFFFF
            px = n % max(w, 1)
            py = ((n // max(w, 1)) % max(int(h * 0.7), 1)) + int(h * 0.05)
            sz = 1.0 + (n % 3) * 0.6
            alpha = (18 + (n % 40)) if t.dark else (28 + (n % 35))
            p.setBrush(QColor(140, 200, 255, alpha))
            p.drawEllipse(QPointF(float(px), float(py)), sz, sz)
        p.restore()

        vignette = QLinearGradient(0, h * 0.78, 0, h)
        if t.dark:
            vignette.setColorAt(0.0, QColor(5, 8, 15, 0))
            vignette.setColorAt(1.0, QColor(5, 8, 15, 140))
        else:
            vignette.setColorAt(0.0, QColor(200, 220, 240, 0))
            vignette.setColorAt(1.0, QColor(180, 205, 230, 90))
        p.fillRect(QRectF(0, h * 0.78, w, h * 0.22), vignette)

        glow = QLinearGradient(w - 10, 0, w, 0)
        glow.setColorAt(0.0, QColor(ar, ag, ab, 0))
        glow.setColorAt(1.0, QColor(ar, ag, ab, 55 if t.dark else 70))
        p.fillRect(QRectF(w - 10, 0, 10, h), glow)

        core = QLinearGradient(0, 0, 0, h)
        core.setColorAt(0.0, QColor(ar, ag, ab, 0))
        core.setColorAt(0.35, QColor(gr, gg, gb, 40))
        core.setColorAt(0.5, QColor(140, 210, 255, 210 if t.dark else 180))
        core.setColorAt(0.65, QColor(gr, gg, gb, 40))
        core.setColorAt(1.0, QColor(ar, ag, ab, 0))
        p.fillRect(QRectF(w - 2, 0, 2, h), core)

        flare = QRadialGradient(w - 1, h * 0.5, 18)
        flare.setColorAt(0.0, QColor(200, 235, 255, 220 if t.dark else 180))
        flare.setColorAt(0.35, QColor(gr, gg, gb, 90))
        flare.setColorAt(1.0, QColor(gr, gg, gb, 0))
        p.setBrush(flare)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(w - 1, h * 0.5), 18, 18)

        p.end()

    def _paint_waves(self, p: QPainter, w: int, h: int) -> None:
        t = self._t
        ar, ag, ab = t.accent_rgb
        gr, gg, gb = t.glow_rgb
        wr, wg, wb = t.wave_fill_bottom
        layers = (
            (0.58, 0.10, 2.1, 28, True),
            (0.64, 0.08, 2.6, 38, False),
            (0.70, 0.11, 1.8, 22, True),
            (0.76, 0.07, 3.1, 48, False),
            (0.82, 0.09, 2.3, 30, True),
        )
        for base_y, amp, freq, alpha, fill in layers:
            path = QPainterPath()
            y0 = h * base_y
            path.moveTo(0, h)
            path.lineTo(0, y0)
            steps = max(48, w // 6)
            for i in range(steps + 1):
                x = w * (i / steps)
                y = y0 + math.sin(x * freq * 0.012 + base_y * 8) * (h * amp)
                y += math.sin(x * 0.031 + freq) * (h * amp * 0.35)
                path.lineTo(x, y)
            path.lineTo(w, h)
            path.closeSubpath()

            a = alpha if t.dark else min(alpha + 12, 60)
            if fill:
                grad = QLinearGradient(0, y0 - h * 0.05, 0, h)
                grad.setColorAt(0.0, QColor(ar, ag, ab, a))
                grad.setColorAt(0.55, QColor(ar, ag, ab, a // 2))
                grad.setColorAt(1.0, QColor(wr, wg, wb, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(grad)
                p.drawPath(path)
            else:
                stroke = QPainterPath()
                stroke.moveTo(0, y0)
                for i in range(steps + 1):
                    x = w * (i / steps)
                    y = y0 + math.sin(x * freq * 0.012 + base_y * 8) * (h * amp)
                    y += math.sin(x * 0.031 + freq) * (h * amp * 0.35)
                    stroke.lineTo(x, y)
                pen = QPen(QColor(gr, gg, gb, a + 20))
                pen.setWidthF(1.4)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(stroke)


def _field_qss(theme: _Theme, border: str) -> str:
    return f"""
        QFrame#iconField {{
            background: {theme.field_bg};
            border: 1px solid {border};
            border-radius: 10px;
        }}
        QFrame#iconField:hover {{
            border-color: {theme.field_hover};
        }}
        QLineEdit {{
            background: transparent;
            border: none;
            color: {theme.ink};
            font-size: 14px;
            padding: 0 4px;
            selection-background-color: {theme.blue};
        }}
        QLineEdit::placeholder {{
            color: {theme.muted};
        }}
        QToolButton {{
            background: transparent;
            border: none;
            padding: 0 6px;
        }}
    """


class _IconField(QFrame):
    """Rounded field with leading icon and optional trailing tool button."""

    def __init__(
        self,
        theme: _Theme,
        icon: str,
        placeholder: str,
        *,
        password: bool = False,
        height: int = 44,
        parent=None,
    ):
        super().__init__(parent)
        self._t = theme
        self._icon_kind = icon
        self.setObjectName("iconField")
        self.setFixedHeight(height)
        self._focused = False
        self._apply_border(theme.field_border)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(6)

        self._ico = QLabel()
        self._ico.setFixedSize(20, 20)
        self._ico.setPixmap(_icon_pixmap(icon, 18, theme.muted))
        self._ico.setStyleSheet("background: transparent; border: none;")
        row.addWidget(self._ico)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFrame(False)
        if password:
            self.edit.setEchoMode(QLineEdit.Password)
        row.addWidget(self.edit, 1)

        self._toggle = None
        if password:
            self._toggle = QToolButton()
            self._toggle.setCursor(Qt.PointingHandCursor)
            self._toggle.setIcon(QIcon(_icon_pixmap("eye", 18, theme.muted)))
            self._toggle.setIconSize(QSize(18, 18))
            self._toggle.setToolTip("Show password")
            self._toggle.clicked.connect(self._toggle_echo)
            row.addWidget(self._toggle)

        self.edit.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.edit:
            if event.type() == QEvent.FocusIn:
                self._focused = True
                self._apply_border(self._t.field_focus)
            elif event.type() == QEvent.FocusOut:
                self._focused = False
                self._apply_border(self._t.field_border)
        return super().eventFilter(obj, event)

    def _apply_border(self, border: str) -> None:
        self.setStyleSheet(_field_qss(self._t, border))

    def _toggle_echo(self) -> None:
        muted = self._t.muted
        if self.edit.echoMode() == QLineEdit.Password:
            self.edit.setEchoMode(QLineEdit.Normal)
            self._toggle.setIcon(QIcon(_icon_pixmap("eye_off", 18, muted)))
            self._toggle.setToolTip("Hide password")
        else:
            self.edit.setEchoMode(QLineEdit.Password)
            self._toggle.setIcon(QIcon(_icon_pixmap("eye", 18, muted)))
            self._toggle.setToolTip("Show password")

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(value)

    def clear(self) -> None:
        self.edit.clear()


class LoginWindow(QWidget):
    login_successful = Signal(object)

    def __init__(self):
        super().__init__()
        self._t = _theme()
        self.setWindowTitle(APP_NAME)
        # Compact shell — form is 360px wide; avoid large empty bands around it.
        self.setFixedSize(880, 440)
        self.setObjectName("loginRoot")
        self._apply_root_style()
        self._build_ui()
        self._restore_remembered()

    def _apply_root_style(self) -> None:
        t = self._t
        self.setStyleSheet(
            f"""
            QWidget#loginRoot {{
                background: {t.bg_right};
            }}
            QCheckBox {{
                color: {t.muted};
                font-size: 12px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {t.field_border};
                background: {t.field_bg};
            }}
            QCheckBox::indicator:checked {{
                background: {t.blue};
                border-color: {t.blue};
                image: url("{_check_icon_url()}");
            }}
            """
        )

    def _build_ui(self) -> None:
        t = self._t
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._brand = _BrandPanel(t)
        root.addWidget(self._brand, 5)

        right = QWidget()
        right.setObjectName("loginRight")
        right.setStyleSheet(f"QWidget#loginRight {{ background: {t.bg_right}; }}")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_setup_page())
        right_layout.addWidget(self._stack)
        root.addWidget(right, 6)

    def _form_shell(self) -> tuple[QWidget, QVBoxLayout]:
        t = self._t
        page = QWidget()
        page.setStyleSheet(f"background: {t.bg_right};")
        outer = QVBoxLayout(page)
        # Modest padding — trim unused bands without crowding the form.
        outer.setContentsMargins(28, 18, 28, 12)
        outer.setSpacing(0)

        form = QWidget()
        # ~2/3 of the previous wide form so fields don't stretch edge-to-edge.
        form.setFixedWidth(360)
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        outer.addWidget(form, 0, Qt.AlignHCenter | Qt.AlignTop)
        outer.addSpacing(14)
        outer.addStretch(1)

        version = QLabel(f"v{APP_VERSION}")
        version.setAlignment(Qt.AlignRight)
        version.setStyleSheet(
            f"color: {t.muted}; font-size: 11px; background: transparent;"
        )
        outer.addWidget(version, 0, Qt.AlignRight)
        return page, layout

    def _build_login_page(self) -> QWidget:
        t = self._t
        page, layout = self._form_shell()

        title = QLabel(
            "<span style=\"font-size:26px; font-weight:700; font-family:"
            f"'Segoe UI',Arial; color:{t.ink};\">Welcome </span>"
            "<span style=\"font-size:26px; font-weight:700; font-family:"
            f"'Segoe UI',Arial; color:{t.blue_hot};\">Back</span>"
        )
        title.setAlignment(Qt.AlignLeft)
        layout.addWidget(title)

        sub = QLabel("Sign in to continue")
        sub.setStyleSheet(
            f"color: {t.muted}; font-size: 13px; background: transparent;"
        )
        layout.addWidget(sub)
        layout.addSpacing(6)

        self._username = _IconField(t, "user", "Username")
        layout.addWidget(self._username)

        self._password = _IconField(t, "lock", "Password", password=True)
        self._password.edit.returnPressed.connect(self._on_login)
        layout.addWidget(self._password)

        row = QHBoxLayout()
        row.setContentsMargins(2, 2, 2, 0)
        self._remember = QCheckBox("Remember me")
        row.addWidget(self._remember)
        row.addStretch(1)

        forgot = QPushButton("Forgot password?")
        forgot.setCursor(Qt.PointingHandCursor)
        forgot.setFlat(True)
        forgot.setStyleSheet(
            f"""
            QPushButton {{
                color: {t.blue_hot};
                background: transparent;
                border: none;
                font-size: 12px;
                padding: 0;
            }}
            QPushButton:hover {{ color: {t.forgot_hover}; text-decoration: underline; }}
            """
        )
        forgot.clicked.connect(self._on_forgot)
        row.addWidget(forgot)
        layout.addLayout(row)

        self._login_error = QLabel("")
        self._login_error.setStyleSheet(f"color: {t.err}; font-size: 12px;")
        self._login_error.setWordWrap(True)
        self._login_error.setMinimumHeight(16)
        layout.addWidget(self._login_error)

        self._login_btn = QPushButton("  Sign In")
        self._login_btn.setIcon(QIcon(_icon_pixmap("login", 18, "#FFFFFF")))
        self._login_btn.setIconSize(QSize(18, 18))
        self._login_btn.setFixedHeight(46)
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {t.blue};
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 15px;
                font-weight: 600;
                letter-spacing: 0.3px;
            }}
            QPushButton:hover {{ background-color: {t.blue_hover}; }}
            QPushButton:pressed {{ background-color: {t.blue_press}; }}
            QPushButton:disabled {{
                background-color: {t.blue_soft};
                color: {"#A8B4C8" if t.dark else "#F8FAFC"};
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self._login_btn)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(*t.accent_rgb, t.btn_shadow_alpha))
        self._login_btn.setGraphicsEffect(shadow)
        self._login_btn.clicked.connect(self._on_login)
        layout.addWidget(self._login_btn)

        return page

    def _build_setup_page(self) -> QWidget:
        t = self._t
        page, layout = self._form_shell()

        title = QLabel(
            "<span style=\"font-size:22px; font-weight:700; font-family:"
            f"'Segoe UI',Arial; color:{t.ink};\">Create </span>"
            "<span style=\"font-size:22px; font-weight:700; font-family:"
            f"'Segoe UI',Arial; color:{t.blue_hot};\">Admin</span>"
        )
        layout.addWidget(title)

        sub = QLabel("No accounts found — create the admin account to get started.")
        sub.setStyleSheet(f"color: {t.muted}; font-size: 13px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(6)

        self._setup_fullname = _IconField(t, "user", "Full Name", height=40)
        layout.addWidget(self._setup_fullname)

        self._setup_username = _IconField(t, "user", "Username", height=40)
        layout.addWidget(self._setup_username)

        self._setup_password = _IconField(
            t,
            "lock",
            f"Password ({password_placeholder().lower()})",
            password=True,
            height=40,
        )
        layout.addWidget(self._setup_password)

        self._setup_password2 = _IconField(
            t, "lock", "Confirm Password", password=True, height=40
        )
        layout.addWidget(self._setup_password2)

        self._setup_error = QLabel("")
        self._setup_error.setStyleSheet(f"color: {t.err}; font-size: 12px;")
        self._setup_error.setWordWrap(True)
        layout.addWidget(self._setup_error)

        self._setup_btn = QPushButton("Create Admin Account")
        self._setup_btn.setFixedHeight(42)
        self._setup_btn.setCursor(Qt.PointingHandCursor)
        self._setup_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {t.blue};
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {t.blue_hover}; }}
            QPushButton:pressed {{ background-color: {t.blue_press}; }}
            QPushButton:disabled {{
                background-color: {t.blue_soft};
                color: {"#A8B4C8" if t.dark else "#F8FAFC"};
            }}
            """
        )
        self._setup_btn.clicked.connect(self._on_setup)
        layout.addWidget(self._setup_btn)

        return page

    def _settings(self) -> QSettings:
        return QSettings(_SETTINGS_ORG, _SETTINGS_APP)

    def _restore_remembered(self) -> None:
        s = self._settings()
        remember = bool(s.value(_SETTINGS_REMEMBER_KEY, False, type=bool))
        self._remember.setChecked(remember)
        if remember:
            user = s.value(_SETTINGS_USER_KEY, "", type=str) or ""
            if user:
                self._username.setText(user)
                self._password.edit.setFocus()

    def _persist_remember(self, username: str) -> None:
        s = self._settings()
        if self._remember.isChecked():
            s.setValue(_SETTINGS_REMEMBER_KEY, True)
            s.setValue(_SETTINGS_USER_KEY, username)
        else:
            s.setValue(_SETTINGS_REMEMBER_KEY, False)
            s.remove(_SETTINGS_USER_KEY)

    def _on_forgot(self) -> None:
        QMessageBox.information(
            self,
            "Forgot password",
            "Ask your administrator to reset your password from the user management tools.\n\n"
            "If you are the admin and locked out, contact your system operator.",
        )

    # ------------------------------------------------------------------
    # First-run detection
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Defer off any active asyncio task (splash reveal / logout return).
        # Python 3.14 + qasync forbids create_task while another Task is running.
        QTimer.singleShot(0, self._kick_first_run_check)

    def _kick_first_run_check(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._check_first_run())

    async def _check_first_run(self) -> None:
        try:
            has_users = await any_user_exists()
            self._stack.setCurrentIndex(0 if has_users else 1)
        except Exception:
            self._login_error.setText(
                "Cannot reach the server. Check your connection and try again."
            )
            self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    def clear_fields(self) -> None:
        if not self._remember.isChecked():
            self._username.clear()
        self._password.clear()
        self._login_error.setText("")
        # Sign In is disabled during authenticate(); re-enable so logout → login works.
        self._login_btn.setEnabled(True)
        self._login_btn.setText("  Sign In")
        self._login_btn.setIcon(QIcon(_icon_pixmap("login", 18, "#FFFFFF")))

    # Login flow
    # ------------------------------------------------------------------

    def _on_login(self) -> None:
        asyncio.ensure_future(self._do_login())

    async def _do_login(self) -> None:
        self._login_btn.setEnabled(False)
        self._login_btn.setText("  Signing in…")
        self._login_btn.setIcon(QIcon())
        self._login_error.setText("")

        username = self._username.text().strip()
        password = self._password.text()

        if not username or not password:
            self._login_error.setText("Enter username and password.")
            self._login_btn.setEnabled(True)
            self._login_btn.setText("  Sign In")
            self._login_btn.setIcon(QIcon(_icon_pixmap("login", 18, "#FFFFFF")))
            return

        try:
            user = await authenticate(username, password)
        except ApiError as exc:
            # Prefer short auth messages over raw transport dumps in the login UI.
            detail = str(exc).strip()
            if len(detail) > 120 or "\n" in detail or "Traceback" in detail:
                detail = "Sign-in failed. Please try again."
            self._login_error.setText(detail or "Sign-in failed. Please try again.")
            self._login_btn.setEnabled(True)
            self._login_btn.setText("  Sign In")
            self._login_btn.setIcon(QIcon(_icon_pixmap("login", 18, "#FFFFFF")))
            return
        if user is None:
            self._login_error.setText("Invalid username or password.")
            self._password.clear()
            self._login_btn.setEnabled(True)
            self._login_btn.setText("  Sign In")
            self._login_btn.setIcon(QIcon(_icon_pixmap("login", 18, "#FFFFFF")))
            return

        self._persist_remember(username)
        self.login_successful.emit(user)
        # Keep button disabled while this window is hidden; clear_fields()
        # re-enables it when the user logs out and returns here.

    # ------------------------------------------------------------------
    # First-run setup flow
    # ------------------------------------------------------------------

    def _on_setup(self) -> None:
        asyncio.ensure_future(self._do_setup())

    async def _do_setup(self) -> None:
        self._setup_btn.setEnabled(False)
        self._setup_error.setText("")

        full_name = self._setup_fullname.text().strip()
        username = self._setup_username.text().strip()
        password = self._setup_password.text()
        password2 = self._setup_password2.text()

        if not all([full_name, username, password, password2]):
            self._setup_error.setText("All fields are required.")
            self._setup_btn.setEnabled(True)
            return
        if password != password2:
            self._setup_error.setText("Passwords do not match.")
            self._setup_btn.setEnabled(True)
            return
        policy_errors = password_policy_errors(password)
        if policy_errors:
            self._setup_error.setText(policy_errors[0])
            self._setup_btn.setEnabled(True)
            return

        try:
            user = await create_user(username, password, "admin", full_name)
            self.login_successful.emit(user)
        except Exception as exc:
            self._setup_error.setText(f"Error creating account: {exc}")
            self._setup_btn.setEnabled(True)
