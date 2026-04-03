"""
Zerodha Kite Connect wrapper.

Credentials are read from environment variables:
  ZERODHA_API_KEY    — your Kite API key
  ZERODHA_API_SECRET — your Kite API secret
  ZERODHA_ACCESS_TOKEN (optional) — pre-set access token; updated at runtime
                                    via POST /auth/zerodha/session

Access token lifecycle:
  Kite tokens expire daily at 6 AM IST.  To refresh, the user visits the
  login URL (GET /auth/zerodha/login-url), completes the Zerodha login, and
  POSTs the returned request_token to POST /auth/zerodha/session.
"""
import logging
import os
from datetime import date, datetime, time
from typing import Dict, List, Optional

from kiteconnect import KiteConnect

log = logging.getLogger(__name__)

API_KEY = os.getenv("ZERODHA_API_KEY", "")
API_SECRET = os.getenv("ZERODHA_API_SECRET", "")

# Module-level singleton
_kite: Optional[KiteConnect] = None


def _get_kite() -> KiteConnect:
    global _kite
    if _kite is None:
        if not API_KEY:
            raise RuntimeError("ZERODHA_API_KEY environment variable is not set.")
        _kite = KiteConnect(api_key=API_KEY)
        # Apply pre-set access token if available (e.g. injected at deploy time)
        preset = os.getenv("ZERODHA_ACCESS_TOKEN", "")
        if preset:
            _kite.set_access_token(preset)
    return _kite


def get_login_url() -> str:
    """Return the Zerodha OAuth login URL."""
    return _get_kite().login_url()


def generate_session(request_token: str) -> Dict:
    """
    Exchange a request_token for an access_token.
    Activates the token on the singleton and returns the full session dict.
    """
    if not API_SECRET:
        raise RuntimeError("ZERODHA_API_SECRET environment variable is not set.")
    kite = _get_kite()
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    kite.set_access_token(data["access_token"])
    log.info("Zerodha session established for user: %s", data.get("user_id"))
    return data


def get_access_token() -> Optional[str]:
    """Return the currently active access token, or None."""
    try:
        kite = _get_kite()
        return getattr(kite, "access_token", None) or None
    except RuntimeError:
        return None


def get_profile() -> Dict:
    """Return the user profile; raises if not authenticated."""
    return _get_kite().profile()


def is_authenticated() -> bool:
    """Quick check — tries profile() and catches any auth error."""
    try:
        get_profile()
        return True
    except Exception:
        return False


# ── Historical candle fetching ────────────────────────────────────────────────

def fetch_candles(
    instrument_token: int,
    trade_date: date,
    interval: str = "minute",
) -> List[Dict]:
    """
    Fetch 1-minute OHLCV candles for *instrument_token* on *trade_date*.

    Returns a list of dicts:
      { "date": datetime, "open": float, "high": float,
        "low": float, "close": float, "volume": int }

    Raises:
      RuntimeError  — if not authenticated
      DataUnavailableError — if Zerodha returns no candles for this date
    """
    kite = _get_kite()
    if not getattr(kite, "access_token", None):
        raise RuntimeError(
            "Zerodha not authenticated. POST /auth/zerodha/session first."
        )

    from_dt = datetime.combine(trade_date, time(9, 0))
    to_dt = datetime.combine(trade_date, time(15, 35))

    try:
        records = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_dt,
            to_date=to_dt,
            interval=interval,
            continuous=False,
            oi=False,
        )
    except Exception as exc:
        log.warning(
            "Zerodha historical_data failed — token=%s date=%s: %s",
            instrument_token, trade_date, exc,
        )
        raise DataUnavailableError(
            f"Zerodha API error for token {instrument_token} on {trade_date}: {exc}"
        ) from exc

    if not records:
        raise DataUnavailableError(
            f"No candle data returned by Zerodha for token={instrument_token} "
            f"on {trade_date}. Possible holiday or instrument not traded."
        )

    return records


# ── Instrument master ─────────────────────────────────────────────────────────

_instruments_cache: Optional[List[Dict]] = None


def get_instruments(segment: str = "NFO") -> List[Dict]:
    """
    Fetch and in-process-cache the instrument master for *segment*.
    Cache is valid for the lifetime of the process; call
    invalidate_instruments_cache() to force a refresh.
    """
    global _instruments_cache
    if _instruments_cache is not None:
        return _instruments_cache
    kite = _get_kite()
    if not getattr(kite, "access_token", None):
        raise RuntimeError("Zerodha not authenticated.")
    _instruments_cache = kite.instruments(segment)
    log.info("Loaded %d NFO instruments from Zerodha.", len(_instruments_cache))
    return _instruments_cache


def invalidate_instruments_cache() -> None:
    global _instruments_cache
    _instruments_cache = None


# ── Custom exceptions ─────────────────────────────────────────────────────────

class DataUnavailableError(Exception):
    """Raised when Zerodha returns no data for a requested date/instrument."""
