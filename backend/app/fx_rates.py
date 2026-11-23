"""Public FX rates → TZS for Overview Sources (cached, best-effort)."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

logger = logging.getLogger("tahmeed.fx")

# open.er-api.com — free, no key; includes TZS and ZMW.
_FX_URL = "https://open.er-api.com/v6/latest/USD"
_CACHE_TTL_SEC = 6 * 60 * 60
_TIMEOUT_SEC = 8.0

# Conservative fallbacks if the network call fails (USD→unit).
_FALLBACK_USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "TZS": 2600.0,
    "ZMW": 20.0,
}

_lock = threading.Lock()
_cache: dict[str, Any] = {"rates": None, "fetched_at": 0.0, "source": "none"}


def _normalize_code(code: str | None) -> str:
    cur = (code or "").strip().upper()
    if cur in {"TSH", "TZ"}:
        return "TZS"
    if cur in {"ZMB", "ZK"}:
        return "ZMW"
    return cur


def _rates_to_tzs_from_usd_table(usd_rates: dict[str, float]) -> dict[str, float]:
    tzs_per_usd = float(usd_rates.get("TZS") or 0.0)
    if tzs_per_usd <= 0:
        tzs_per_usd = float(_FALLBACK_USD_RATES["TZS"])
    out: dict[str, float] = {"TZS": 1.0, "USD": tzs_per_usd}
    zmw_per_usd = float(usd_rates.get("ZMW") or 0.0)
    if zmw_per_usd > 0:
        out["ZMW"] = tzs_per_usd / zmw_per_usd
    else:
        out["ZMW"] = tzs_per_usd / float(_FALLBACK_USD_RATES["ZMW"])
    return out


def _fetch_usd_rates() -> dict[str, float]:
    req = urlrequest.Request(
        _FX_URL,
        headers={"User-Agent": "tahmeed-expense/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urlrequest.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if str(payload.get("result") or "").lower() != "success":
        raise RuntimeError(f"FX provider result={payload.get('result')!r}")
    rates = payload.get("rates") or {}
    out: dict[str, float] = {}
    for key, value in rates.items():
        try:
            out[str(key).upper()] = float(value)
        except (TypeError, ValueError):
            continue
    if "USD" not in out:
        out["USD"] = 1.0
    if float(out.get("TZS") or 0) <= 0:
        raise RuntimeError("FX provider missing TZS")
    return out


def get_rates_to_tzs(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return multipliers: amount_in_currency * rate = amount_in_TZS."""
    now = time.time()
    with _lock:
        cached = _cache.get("rates")
        age = now - float(_cache.get("fetched_at") or 0.0)
        if cached and not force_refresh and age < _CACHE_TTL_SEC:
            return {
                "rates": dict(cached),
                "source": str(_cache.get("source") or "cache"),
                "as_of": float(_cache.get("fetched_at") or 0.0),
            }

    source = "live"
    try:
        usd_table = _fetch_usd_rates()
        rates = _rates_to_tzs_from_usd_table(usd_table)
    except (urlerror.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        logger.warning("FX fetch failed (%s); using fallback rates", exc)
        rates = _rates_to_tzs_from_usd_table(_FALLBACK_USD_RATES)
        source = "fallback"

    with _lock:
        _cache["rates"] = dict(rates)
        _cache["fetched_at"] = now
        _cache["source"] = source
    return {"rates": rates, "source": source, "as_of": now}


def convert_to_tzs(amount: float, currency: str | None, rates: dict[str, float]) -> float:
    cur = _normalize_code(currency)
    if cur == "TZS" or not cur:
        return float(amount or 0.0)
    rate = float(rates.get(cur) or 0.0)
    if rate <= 0:
        return 0.0
    return float(amount or 0.0) * rate


def clear_fx_cache() -> None:
    with _lock:
        _cache["rates"] = None
        _cache["fetched_at"] = 0.0
        _cache["source"] = "none"
