"""Shared Submit-day gates for Drafts / Saved inbox tabs."""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtWidgets import QMessageBox, QWidget

from tahmeed.services.cashier_service import submit_day_for_verify


async def submit_draft_day_with_gates(parent: QWidget, day: date) -> Optional[int]:
    """Submit all drafts for *day* to Verify, after Day Issues gates.

    Returns the updated count, or ``None`` when blocked / cancelled / failed
    (caller should not refresh on ``None`` when the user cancelled; failed
    paths already show a dialog).
    """
    from tahmeed.services.day_issues_service import collect_day_issues
    from tahmeed.services.settings_service import get_setting

    try:
        snap = await collect_day_issues(day)
    except Exception:
        snap = None
    if snap is not None and snap.total > 0:
        allow = False
        try:
            allow = bool(await get_setting("allow_submit_with_day_issues"))
        except Exception:
            allow = False
        label = day.strftime("%d %b %Y")
        summary = (
            f"{snap.total} Day Issue(s) on {label}:\n"
            f"  · {snap.unmapped_count} unmapped\n"
            f"  · {snap.bad_map_count} bad map\n\n"
        )
        if not allow:
            QMessageBox.warning(
                parent,
                "Day Issues block submit",
                summary + "Open Day Issues and map descriptions before submitting.",
            )
            return None
        if QMessageBox.question(
            parent,
            "Submit with Day Issues?",
            summary + "Submit anyway?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) != QMessageBox.Yes:
            return None
    try:
        return await submit_day_for_verify(day)
    except Exception as exc:
        QMessageBox.critical(parent, "Submit Failed", str(exc))
        return None
