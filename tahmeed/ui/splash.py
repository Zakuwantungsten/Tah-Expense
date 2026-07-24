"""Branded splash shown while the desktop app finishes starting up."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from tahmeed.config import APP_NAME, APP_VERSION
from tahmeed.ui.branding import load_brand_logo

_BLUE = "#0077C5"
_BLUE_LIGHT = "#E8F4FD"
_NAVY = "#1B2B4B"


class SplashScreen(QWidget):
    """QuickBooks-style brand splash shown before the login window."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("splashRoot")
        self.setWindowFlags(
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(420, 320)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(
            f"""
            #splashRoot {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {_BLUE_LIGHT}, stop:0.45 #FFFFFF, stop:1 #FFFFFF);
                border: 1px solid #BFDBFE;
            }}
            QLabel#splashTitle {{
                color: {_NAVY};
                font-size: 22px;
                font-weight: 700;
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel#splashStatus {{
                color: {_BLUE};
                font-size: 13px;
                font-family: 'Segoe UI', sans-serif;
            }}
            QLabel#splashVersion {{
                color: #64748B;
                font-size: 11px;
                font-family: 'Segoe UI', sans-serif;
            }}
            QProgressBar {{
                background: #DBEAFE;
                border: none;
                border-radius: 3px;
                max-height: 6px;
                min-height: 6px;
            }}
            QProgressBar::chunk {{
                background: {_BLUE};
                border-radius: 3px;
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 40, 36, 28)
        root.setSpacing(0)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setAttribute(Qt.WA_TranslucentBackground, True)
        logo.setStyleSheet("background: transparent;")
        pix = load_brand_logo(160)
        if not pix.isNull():
            logo.setPixmap(pix)
        root.addWidget(logo)
        root.addSpacing(18)

        title = QLabel(APP_NAME)
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)
        root.addSpacing(8)

        self._status = QLabel("Loading…")
        self._status.setObjectName("splashStatus")
        self._status.setAlignment(Qt.AlignCenter)
        root.addWidget(self._status)
        root.addSpacing(22)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)
        root.addStretch(1)

        version = QLabel(f"Version {APP_VERSION}")
        version.setObjectName("splashVersion")
        version.setAlignment(Qt.AlignCenter)
        root.addWidget(version)

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        QApplication.processEvents()

    def show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )
        self.show()
        QApplication.processEvents()

    def finish(self, next_window: QWidget | None = None) -> None:
        if next_window is not None:
            next_window.show()
            QApplication.processEvents()
        self.close()
        self.deleteLater()
