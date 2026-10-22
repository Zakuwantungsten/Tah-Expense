"""Shared Submit-day gates for Drafts / Saved inbox tabs."""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtWidgets import QMessageBox, QWidget

from tahmeed.services.cashier_service import (
    get_transactions_by_date,
    submit_day_for_verify,
)


async def submit_draft_day_with_gates(
    parent: QWidget,
    day: date,
    *,
    cashier_id=None,
    user_id=None,
) -> Optional[int]:
    """Submit all drafts for *day* to Verify, after parked + Day Issues gates.

    Returns the updated count, or ``None`` when blocked / cancelled / failed
    (caller should not refresh on ``None`` when the user cancelled; failed
    paths already show a dialog).
    """
    from tahmeed.services.day_issues_service import collect_day_issues
    from tahmeed.services.register_draft_service import (
        draft_parked_rows,
        load_register_draft,
    )
    from tahmeed.services.settings_service import get_setting
    from tahmeed.ui.async_utils import pause_background_polls

    label = day.strftime("%d %b %Y")
    with pause_background_polls(parent):
        return await _submit_draft_day_body(
            parent,
            day,
            label=label,
            cashier_id=cashier_id,
            user_id=user_id,
        )


async def _submit_draft_day_body(
    parent: QWidget,
    day: date,
    *,
    label: str,
    cashier_id=None,
    user_id=None,
) -> Optional[int]:
    from tahmeed.services.day_issues_service import collect_day_issues
    from tahmeed.services.register_draft_service import (
        draft_parked_rows,
        load_register_draft,
    )
    from tahmeed.services.settings_service import get_setting

    # Parked truck follow-up must be resolved before Submit (Register + Drafts).
    parked_n = 0
    try:
        txs = await get_transactions_by_date(day, cashier_id=cashier_id)
        parked_n = sum(
            1
            for tx in txs
            if (getattr(tx, "register_status", "") or "") == "parked"
        )
    except Exception:
        parked_n = 0
    if user_id is not None:
        try:
            local = load_register_draft(user_id, day, merged=False)
            parked_n += len(draft_parked_rows(local))
        except Exception:
            pass
    if parked_n > 0:
        plural = "s" if parked_n != 1 else ""
        QMessageBox.warning(
            parent,
            "Parked trucks block submit",
            f"{parked_n} parked truck row{plural} on {label} still need a "
            "correct fleet number.\n\n"
            "Open the day in Register → Parked, fix the truck(s), then submit "
            "again.",
        )
        return None

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
