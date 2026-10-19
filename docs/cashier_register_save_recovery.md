# Daily Register — Save, Recovered, and Offline

Short guide for cashiers.

## When to click Save

- After typing new expenses (or editing saved drafts), click **Save**.
- Save stores work **on this computer first**, then syncs to the server in the background — you can keep typing (sky tint = waiting to sync).
- Open **Drafts** and submit for Verify when the day is ready — Save alone does not submit.

## What “Draft restored” / “Sync pending” means

- If the app closed before sync finished (or while offline), local recovery brings those rows back.
- **Sync pending** means some rows were still queued for the server (sky tint) — they retry automatically when online, or click Save.
- **Draft restored** means ordinary unsaved typing was recovered — click Save to store it.
- Recovered rows are **not** double-counted against rows already on the server — matching rows are skipped.

## Offline / slow network

- If you appear offline, Save stores work **on this computer** and shows “Saved locally — will sync.”
- When the connection returns, pending rows sync automatically. Sky/red tint means waiting or failed sync.
- Background sync does **not** lock the table — keep working while it runs.
- Large Saves show progress in the status bar (`Syncing 1–100 of 360…`) and add finished rows without freezing the day.
- Press **Esc** while syncing to cancel the rest — rows already synced stay saved; the rest stay pending (sky tint) for a later Save.
- Do **not** re-type the same expenses “just in case” — retries are safe and will not create duplicates.

## Possible duplicates

- Soft duplicate warning compares similar past expenses (advisory).
- **Save checked** saves only the rows you tick; **Keep for later** leaves them on the table for a later Save.
- Use Save anyway / checked only when you are sure the new row is a separate expense.

## Parked trucks

- Incorrect trucks can be **Parked** for follow-up (**Park all** / **Park remaining**).
- Parking persists **only** those parked rows and skips Off-date / duplicate save prompts so other unsaved entries stay on My.
- The app finishes sync before opening the Parked tab so rows are not lost mid-sync.

## Tips

- Prefer **My** view for new entry; Merged is for reviewing everyone.
- Set **Reconciled Date** before Save.
- Every draft row needs at least a **Description** (and truck when required).
