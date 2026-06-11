"""Global application state — shared mutable state accessible across all views."""
from datetime import datetime


class _AppState:
    """Singleton holding cross-view UI state (selected fiscal year, etc.)."""
    fiscal_year: int = datetime.now().year


app_state = _AppState()
