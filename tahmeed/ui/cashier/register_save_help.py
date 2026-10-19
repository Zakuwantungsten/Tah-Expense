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

- After typing new expenses (or editing saved drafts), click **Save**.
- Save stores work on this computer first, then syncs in the background — you can keep typing.
- Open **Drafts** and submit for Verify when the day is ready — Save alone does not submit.

## What “Draft restored” / “Sync pending” means

- Local recovery brings rows back if the app closed mid-sync or while offline.
- **Sync pending** = rows still queued for the server (sky tint); they retry when online.
- **Draft restored** = ordinary unsaved typing; click Save to store it.
- Matching rows already on the server are skipped (no double-count).

## Offline / slow network

- Offline Save keeps work on this computer and syncs when the connection returns.
- Sky/red tint means waiting or failed sync. Background sync does not lock the table.
- Large syncs show `Syncing 1–100 of N…` in the status bar. Press **Esc** to cancel the rest (already-synced rows stay saved).
- Do not re-type the same expenses — retries are safe.

## Possible duplicates

- **Save checked** saves only ticked rows; **Keep for later** leaves them on the table.
- Soft-dup is advisory — only save duplicates when you are sure they are separate expenses.

## Parked trucks

- Parking incorrect trucks finishes sync before opening the Parked tab.
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
