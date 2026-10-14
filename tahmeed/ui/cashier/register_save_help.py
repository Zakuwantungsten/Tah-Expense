"""Cashier Help — Register Save & Recovery (Phase 5)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_FALLBACK = """# Daily Register — Save, Recovered, and Offline

## When to click Save

- After typing new expenses (or editing saved drafts), click **Save** so they become server drafts.
- You can keep typing after Save; Save again when you have more rows.
- Open **Drafts** and submit for Verify when the day is ready — Save alone does not submit.

## What “Draft restored” / Recovered means

- If the app closed before Save finished (or while offline), local recovery brings those rows back.
- Recovered rows are **not** double-counted against rows already on the server — matching rows are skipped.
- Click **Save** after recovery so anything still local is stored on the server.

## Offline / slow network

- If you appear offline, Save stores work **on this computer** and shows “Saved locally — will sync.”
- When the connection returns, pending rows sync automatically. Sky/red tint means waiting or failed sync.
- Do **not** re-type the same expenses “just in case” — retries are safe and will not create duplicates.

## Duplicate check (“Save anyway”)

- The soft duplicate warning compares similar past expenses (advisory).
- Prefer fixing a mistaken re-entry over clicking **Save anyway**.
- Use Save anyway only when you are sure the new row is a real separate expense.
"""


def _help_markdown() -> str:
    candidates = [
        Path(__file__).resolve().parents[3] / "docs" / "cashier_register_save_recovery.md",
        Path.cwd() / "docs" / "cashier_register_save_recovery.md",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return _FALLBACK


def show_register_save_help(parent: QWidget | None = None) -> None:
    """Modal help dialog for Save / Recovered / offline behaviour."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Register Save & Recovery")
    dlg.resize(520, 460)
    root = QVBoxLayout(dlg)
    browser = QTextBrowser(dlg)
    browser.setOpenExternalLinks(False)
    browser.setMarkdown(_help_markdown())
    root.addWidget(browser)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    root.addWidget(buttons)
    dlg.exec()
