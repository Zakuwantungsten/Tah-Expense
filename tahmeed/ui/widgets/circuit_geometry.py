"""Excel-inspired circuit watermark for page canvases (not Overview).

Distinct from Overview lattice: soft blue glow, concentric rings, a translucent
diamond cluster, and PCB-style traces with elbow bends + hollow nodes.

Seed is stable per page key so each tab looks slightly different without flicker.
"""

from __future__ import annotations

import math
import random
import re
from typing import Optional

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

_ACCENT = (0, 119, 197)  # brand blue #0077C5
_GLOW = (26, 140, 255)
_TRACE = (27, 43, 75)  # navy #1B2B4B
_DEFAULT_BG = "#F4F6F8"

_BG_ONLY_SS = re.compile(
    r"^\s*(?:[\w:#.\-]+\s*\{\s*)?background\s*:\s*#[0-9A-Fa-f]{3,8}\s*;?\s*\}?\s*$",
    re.IGNORECASE,
)


def seed_from_key(key: str) -> int:
    """Stable positive int from a page key (same key → same motif)."""
    # FNV-1a 32-bit — fast, dependency-free, stable across processes.
    h = 2166136261
    for ch in key.encode("utf-8"):
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _fade(x: float, y: float, ox: float, oy: float, radius: float) -> float:
    if radius <= 0:
        return 0.0
    t = math.hypot(x - ox, y - oy) / radius
    if t >= 1.0:
        return 0.0
    u = 1.0 - t
    return u * u


def _diamond(cx: float, cy: float, half: float) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(cx, cy - half)
    path.lineTo(cx + half, cy)
    path.lineTo(cx, cy + half)
    path.lineTo(cx - half, cy)
    path.closeSubpath()
    return path


def _trace_polyline(
    p: QPainter,
    points: list[tuple[float, float]],
    *,
    color: QColor,
    width: float = 1.35,
) -> None:
    if len(points) < 2:
        return
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))


def _hollow_node(p: QPainter, x: float, y: float, *, color: QColor, r: float = 3.2) -> None:
    pen = QPen(color)
    pen.setWidthF(1.35)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(x, y), r, r)


def paint_circuit_corner_geometry(p: QPainter, w: int, h: int, seed: int = 0) -> None:
    """Draw circuit watermark in the top-right. Caller fills the page background."""
    if w <= 0 or h <= 0:
        return

    rng = random.Random(int(seed) & 0xFFFFFFFF)
    ar, ag, ab = _ACCENT
    gr, gg, gb = _GLOW
    tr, tg, tb = _TRACE

    # Seeded anchor near the title / header corner.
    ox = w * (0.74 + rng.uniform(0.0, 0.08))
    oy = 48.0 + rng.uniform(0.0, 24.0)
    radius = max(w * (0.38 + rng.uniform(0.0, 0.08)), 260.0)

    glow0 = 50 + rng.randint(0, 16)
    ambient = QRadialGradient(ox, oy, radius)
    ambient.setColorAt(0.0, QColor(ar, ag, ab, glow0))
    ambient.setColorAt(0.28, QColor(ar, ag, ab, 28))
    ambient.setColorAt(0.58, QColor(gr, gg, gb, 12))
    ambient.setColorAt(1.0, QColor(ar, ag, ab, 0))
    p.fillRect(0, 0, w, h, ambient)

    # Concentric rings
    p.save()
    ring_base = 36.0 + rng.uniform(0.0, 12.0)
    for i, (mult, alpha, width) in enumerate(
        (
            (1.0, 36, 1.2),
            (1.85, 28, 1.15),
            (2.8, 20, 1.05),
            (3.85, 14, 1.0),
            (5.0, 10, 0.95),
        )
    ):
        r = ring_base * mult + rng.uniform(-4.0, 4.0)
        fade = _fade(ox + r * 0.1, oy, ox, oy, radius)
        a = int(alpha * max(fade, 0.35))
        if a < 4:
            continue
        pen = QPen(QColor(gr, gg, gb, a))
        pen.setWidthF(width)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(ox, oy), r, r)
    p.restore()

    # Translucent overlapping diamond cluster
    p.save()
    diamond_specs = [
        (-8, -6, 34.0, 42),
        (22, 4, 28.0, 36),
        (-30, 18, 24.0, 30),
        (8, 28, 22.0, 26),
        (-18, -28, 20.0, 24),
        (36, -18, 18.0, 22),
        (-44, -4, 16.0, 18),
        (14, -36, 15.0, 16),
    ]
    rng.shuffle(diamond_specs)
    keep = 6 + rng.randint(0, 2)
    for dx0, dy0, half0, alpha in diamond_specs[:keep]:
        dx = ox + dx0 + rng.uniform(-10.0, 10.0)
        dy = oy + dy0 + rng.uniform(-8.0, 8.0)
        half = half0 * rng.uniform(0.88, 1.12)
        fade = _fade(dx, dy, ox, oy, radius)
        a = int(alpha * max(fade, 0.45))
        if a < 6:
            continue
        path = _diamond(dx, dy, half)
        p.fillPath(path, QColor(255, 255, 255, a))
        edge = QPen(QColor(ar, ag, ab, max(8, a // 2)))
        edge.setWidthF(1.0)
        p.setPen(edge)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.restore()

    # PCB-style traces with elbows + hollow nodes
    p.save()
    left_len = 160.0 + rng.uniform(0.0, 70.0)
    right_len = 120.0 + rng.uniform(0.0, 60.0)
    traces: list[list[tuple[float, float]]] = [
        [
            (ox - 48, oy - 22),
            (ox - 110, oy - 22),
            (ox - 138, oy - 50),
            (ox - left_len, oy - 50),
        ],
        [
            (ox - 52, oy + 2),
            (ox - 128, oy + 2),
            (ox - 150, oy + 24),
            (ox - left_len - 20, oy + 24),
        ],
        [
            (ox - 40, oy + 26),
            (ox - 96, oy + 26),
            (ox - 118, oy + 48),
            (ox - left_len + 30, oy + 48),
        ],
        [
            (ox - 56, oy + 46),
            (ox - 84, oy + 46),
            (ox - 108, oy + 70),
            (ox - left_len + 50, oy + 70),
        ],
        [
            (ox + 46, oy - 18),
            (ox + 92, oy - 18),
            (ox + 114, oy - 40),
            (ox + right_len, oy - 40),
        ],
        [
            (ox + 50, oy + 8),
            (ox + 108, oy + 8),
            (ox + 128, oy - 12),
            (ox + right_len + 18, oy - 12),
        ],
        [
            (ox + 42, oy + 32),
            (ox + 88, oy + 32),
            (ox + 112, oy + 54),
            (ox + right_len - 10, oy + 54),
        ],
        [
            (ox - 12, oy + 52),
            (ox - 12, oy + 86),
            (ox - 36, oy + 110),
        ],
        [
            (ox + 20, oy + 56),
            (ox + 20, oy + 92),
            (ox + 44, oy + 116),
        ],
        # Extra seeded stub (sometimes included)
        [
            (ox - 24, oy - 40),
            (ox - 70, oy - 40),
            (ox - 96, oy - 66),
            (ox - left_len + 40, oy - 66),
        ],
    ]
    rng.shuffle(traces)
    n_traces = 7 + rng.randint(0, 3)
    for pts in traces[:n_traces]:
        # Slight vertical jitter so seeds diverge without looking broken.
        jy = rng.uniform(-6.0, 6.0)
        pts = [(x, y + jy) for x, y in pts]
        mid = pts[len(pts) // 2]
        fade = _fade(mid[0], mid[1], ox, oy, radius * 1.15)
        alpha = int(72 * max(fade, 0.25))
        if alpha < 8:
            continue
        color = QColor(tr, tg, tb, alpha)
        _trace_polyline(p, pts, color=color, width=1.25 + rng.uniform(0.0, 0.25))
        ex, ey = pts[-1]
        end_fade = _fade(ex, ey, ox, oy, radius * 1.2)
        node_a = int(90 * max(end_fade, 0.22))
        if node_a >= 10:
            _hollow_node(p, ex, ey, color=QColor(tr, tg, tb, node_a))

    junctions = [
        (ox - 48, oy - 22, 70),
        (ox - 52, oy + 2, 66),
        (ox + 46, oy - 18, 60),
        (ox - 40, oy + 26, 58),
        (ox + 50, oy + 8, 54),
    ]
    for jx0, jy0, ja in junctions:
        jx = jx0 + rng.uniform(-4.0, 4.0)
        jy = jy0 + rng.uniform(-4.0, 4.0)
        fade = _fade(jx, jy, ox, oy, radius)
        a = int(ja * max(fade, 0.4))
        if a >= 10:
            _hollow_node(p, jx, jy, color=QColor(ar, ag, ab, a), r=2.6 + rng.uniform(0.0, 0.6))
    p.restore()

    # Soft accent sweeps
    p.save()
    for y0, length_f, alpha in (
        (18.0 + rng.uniform(0.0, 10.0), 0.30 + rng.uniform(0.0, 0.08), 28),
        (90.0 + rng.uniform(0.0, 16.0), 0.24 + rng.uniform(0.0, 0.08), 18),
        (124.0 + rng.uniform(0.0, 16.0), 0.18 + rng.uniform(0.0, 0.08), 12),
    ):
        length = w * length_f
        x0 = w - length - 12
        fade = _fade(x0 + length * 0.55, y0, ox, oy, radius)
        a = int(alpha * max(fade, 0.2))
        if a < 4:
            continue
        pen = QPen(QColor(ar, ag, ab, a))
        pen.setWidthF(1.2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(x0, y0), QPointF(x0 + length, y0 + 4))
    p.restore()


def _neutralize_root_fills(ss: str, object_name: str, class_name: str) -> str:
    """Rewrite common opaque root backgrounds so the circuit host can show."""
    if not ss.strip():
        return ""
    out = ss
    # Bare `background:#hex` root fill
    out = re.sub(
        r"(?im)^\s*background\s*:\s*#[0-9A-Fa-f]{3,8}\s*;?\s*",
        "",
        out,
    )
    # `ClassName { … background:#hex … }`
    out = re.sub(
        rf"(?i)\b{re.escape(class_name)}\s*\{{([^}}]*?)background\s*:\s*#[0-9A-Fa-f]{{3,8}}",
        rf"{class_name} {{\1background: transparent",
        out,
    )
    # `QWidget { … background:#hex … }` / `QWidget#name { … }`
    out = re.sub(
        rf"(?i)\bQWidget(?:#{re.escape(object_name)})?\s*\{{([^}}]*?)background\s*:\s*#[0-9A-Fa-f]{{3,8}}",
        rf"QWidget#{object_name} {{\1background: transparent",
        out,
        count=1,
    )
    return out.strip()


def prepare_circuit_content(widget: QWidget) -> None:
    """Neutralize opaque root fills so CircuitGeometryPage shows through."""
    raw = widget.objectName() or f"circuitContent{abs(id(widget))}"
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    if not name or name[0].isdigit():
        name = f"c_{name}" if name else f"circuitContent{abs(id(widget))}"
    widget.setObjectName(name)

    ss = (widget.styleSheet() or "").strip()
    transparent = f"QWidget#{name} {{ background: transparent; border: none; }}"
    if not ss or _BG_ONLY_SS.match(ss):
        widget.setStyleSheet(transparent)
        return
    neutralized = _neutralize_root_fills(ss, name, type(widget).__name__)
    # Transparent rule last so it wins over same-specificity leftovers.
    widget.setStyleSheet((neutralized + "\n" if neutralized else "") + transparent)


def enable_circuit_panel(
    widget: QWidget,
    *,
    seed: str,
    bg: str = _DEFAULT_BG,
) -> None:
    """Mark a chrome panel to paint the seeded circuit in its own paintEvent."""
    widget._circuit_seed = seed_from_key(seed)  # type: ignore[attr-defined]
    widget._circuit_bg = bg  # type: ignore[attr-defined]
    widget.setAttribute(Qt.WA_StyledBackground, False)
    widget.setAutoFillBackground(False)


def paint_circuit_panel(widget: QWidget, painter: QPainter) -> None:
    """Fill panel bg + circuit. Call from the widget's paintEvent."""
    bg = getattr(widget, "_circuit_bg", _DEFAULT_BG)
    seed = int(getattr(widget, "_circuit_seed", 0) or 0)
    w, h = widget.width(), widget.height()
    if w <= 0 or h <= 0:
        return
    painter.fillRect(widget.rect(), QColor(bg))
    paint_circuit_corner_geometry(painter, w, h, seed=seed)


class CircuitChromeFrame(QFrame):
    """Transparent chrome strip so the page-level circuit shows through continuously.

    Does not paint its own motif (that caused ribbon/page seams). ``seed`` is kept
    for API compatibility / future page-space mapping; geometry comes from
    ``CircuitGeometryPage`` / ``wrap_circuit_page``.
    """

    def __init__(
        self,
        seed: str,
        *,
        bg: str = "#FFFFFF",
        border: str = "#E5E7EB",
        border_bottom: bool = True,
        border_top: bool = False,
        object_name: str = "",
        fixed_height: int | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._circuit_seed_key = seed
        self._circuit_bg = bg  # unused fill — kept for callers; strip stays clear
        safe = re.sub(r"[^A-Za-z0-9_]", "_", object_name or seed) or "chrome"
        if safe[0].isdigit():
            safe = f"c_{safe}"
        self.setObjectName(safe)
        if fixed_height is not None:
            self.setFixedHeight(fixed_height)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        parts = ["background: transparent;"]
        if border_bottom:
            parts.append(f"border-bottom: 1px solid {border};")
        if border_top:
            parts.append(f"border-top: 1px solid {border};")
        if not border_bottom and not border_top:
            parts.append("border: none;")
        self.setStyleSheet(f"QFrame#{self.objectName()} {{ {' '.join(parts)} }}")


def wrap_circuit_page(
    widget: QWidget,
    *,
    seed: str,
    bg: str = _DEFAULT_BG,
    skip_keys: frozenset[str] | None = None,
) -> QWidget:
    """Wrap a page in CircuitGeometryPage. Overview (and skip_keys) pass through."""
    if seed == "overview" or (skip_keys and seed in skip_keys):
        return widget
    if isinstance(widget, CircuitGeometryPage):
        return widget
    host = CircuitGeometryPage(seed=seed, bg=bg)
    host.set_content(widget)
    return host


class CircuitGeometryPage(QWidget):
    """Reusable page host that paints the seeded circuit watermark behind content."""

    def __init__(
        self,
        seed: str | int = 0,
        bg: str = _DEFAULT_BG,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._seed_key = str(seed)
        self._seed = seed_from_key(self._seed_key) if not isinstance(seed, int) else int(seed)
        self._bg = bg
        self._content: Optional[QWidget] = None

        safe = re.sub(r"[^A-Za-z0-9_]", "_", self._seed_key) or "page"
        self.setObjectName(f"circuitPage_{safe}")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setStyleSheet(
            f"QWidget#{self.objectName()} {{ background: transparent; border: none; }}"
        )

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    @property
    def content(self) -> Optional[QWidget]:
        return self._content

    def set_content(self, child: QWidget) -> None:
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.setParent(None)
        prepare_circuit_content(child)
        self._content = child
        self._layout.addWidget(child, 1)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            p.end()
            return
        p.fillRect(self.rect(), QColor(self._bg))
        paint_circuit_corner_geometry(p, w, h, seed=self._seed)
        p.end()

    def __getattr__(self, name: str):
        # Forward unknown attrs/methods to the wrapped page (refresh, etc.).
        if name.startswith("_"):
            raise AttributeError(name)
        content = object.__getattribute__(self, "_content")
        if content is None:
            raise AttributeError(name)
        return getattr(content, name)
