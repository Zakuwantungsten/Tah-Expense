from typing import Any

from tahmeed.services.api_client import ApiError, api_client

# Keep in sync with backend.app.api.SETTINGS_DEFAULTS
DEFAULTS = {
    "default_currency": "TZS",
    "confidence_threshold": 75,  # percent — below this goes to review queue
    # When True the cashier register's Item column flags unknown entries
    # (text is kept; save still requires a known item). When False free text is allowed.
    "restrict_items": False,
    # When True cashiers may save table rows with description only (no item).
    # The accountant assigns items on verify; mappings are remembered.
    "defer_item_to_verify": False,
    # When True the cashier register's Truck No. column only accepts numbers that
    # exist in the accountant's fleet registries (trucks, trailers, motor vehicles).
    # Default On — always intended.
    "restrict_trucks": True,
    # How many days back to scan when checking for duplicate transactions.
    "duplicate_check_days": 5,
    # Free-text Truck No. place labels (YARD, GARAGE, …) accepted without fleet match.
    "allowed_truck_labels": ["YARD", "GARAGE"],
    # Item names whose verified transactions appear in Fuel Consumption → Diesel Cash.
    "diesel_cash_items": ["Diesel Cash"],
    # Export surfaces that honour per-item restrict_in_pdf / restrict_in_excel flags.
    "export_restrict_surfaces": [
        "cashier_register",
        "cashier_print",
        "master_expenses",
        "category_tables",
        "truck_overview",
        "diesel_cash",
    ],
}


async def get_setting(key: str) -> Any:
    try:
        payload = await api_client.request("GET", f"v1/settings/{key}")
    except ApiError as exc:
        if exc.status_code == 404:
            return DEFAULTS.get(key)
        raise
    return payload.get("value", DEFAULTS.get(key))


async def set_setting(key: str, value: Any) -> None:
    await api_client.request("PUT", f"v1/settings/{key}", json={"value": value})


async def get_all_settings() -> dict:
    payload = await api_client.request("GET", "v1/settings")
    result = dict(DEFAULTS)
    if isinstance(payload, dict):
        result.update(payload)
    return result
