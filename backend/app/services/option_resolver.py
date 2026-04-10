"""
NSE option contract resolution.

Given a symbol, trade date, strike and option type (CE/PE) this module:
  1. Determines the nearest weekly expiry after trade_date
  2. Looks up the exact Zerodha instrument token from the NFO instrument master
  3. Returns the token so historical candle data can be fetched

Expiry convention (NSE weekly options):
  NIFTY      — expires on Thursday
  BANKNIFTY  — expires on Wednesday
"""
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Zerodha NSE index tokens (NSE segment — used for underlying spot data)
UNDERLYING_TOKENS: Dict[str, int] = {
    "NIFTY":     256265,
    "BANKNIFTY": 260105,
}

# Weekly expiry weekday (Python convention: Mon=0 … Sun=6)
EXPIRY_WEEKDAY: Dict[str, int] = {
    "NIFTY":     3,   # Thursday
    "BANKNIFTY": 2,   # Wednesday
}

# NFO instrument name prefix as it appears in Zerodha's instrument master
NFO_NAME: Dict[str, str] = {
    "NIFTY":     "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
}


# ── Expiry helpers ────────────────────────────────────────────────────────────

def nearest_weekly_expiry(symbol: str, trade_date: date) -> date:
    """
    Return the nearest weekly expiry strictly after trade_date.

    If trade_date itself is the expiry day, the contract has already
    expired at settlement; we return the *next* week's expiry.

    Examples (NIFTY, Thursday expiry):
      trade_date=Mon 2024-01-08 → expiry=Thu 2024-01-11
      trade_date=Thu 2024-01-11 → expiry=Thu 2024-01-18  (same day → next)
      trade_date=Fri 2024-01-12 → expiry=Thu 2024-01-18
    """
    target_wd = EXPIRY_WEEKDAY[symbol]
    delta = (target_wd - trade_date.weekday()) % 7
    candidate = trade_date + timedelta(days=delta)
    # If the candidate falls on trade_date, push to next occurrence
    if candidate == trade_date:
        candidate += timedelta(days=7)
    return candidate


# ── Instrument token lookup ───────────────────────────────────────────────────

def resolve_instrument_token(
    symbol: str,
    expiry: date,
    strike: int,
    opt_type: str,
    instruments: List[Dict],
) -> Optional[int]:
    """
    Search *instruments* (pre-fetched NFO master) for the option contract
    matching symbol / expiry / strike / opt_type.

    Returns the instrument_token or None if not found.

    Zerodha instrument master field names:
      name, instrument_type, strike, expiry (datetime.date), instrument_token
    """
    nfo_name = NFO_NAME[symbol]
    for inst in instruments:
        if (
            inst.get("name") == nfo_name
            and inst.get("instrument_type") == opt_type.upper()
            and int(inst.get("strike", -1)) == int(strike)
            and inst.get("expiry") == expiry
        ):
            return int(inst["instrument_token"])
    return None


def resolve_option(
    symbol: str,
    trade_date: date,
    strike: int,
    opt_type: str,
    instruments: List[Dict],
) -> Tuple[Optional[int], date]:
    """
    High-level helper: resolve expiry + instrument token for one option leg.

    Returns:
      (instrument_token, expiry_date)
      instrument_token is None when the contract is not found in the master.
    """
    expiry = nearest_weekly_expiry(symbol, trade_date)
    token = resolve_instrument_token(symbol, expiry, strike, opt_type, instruments)
    if token is None:
        log.warning(
            "Instrument not found: %s %s %s %s expiry=%s",
            symbol, opt_type, strike, trade_date, expiry,
        )
    return token, expiry


def resolve_all_legs(
    symbol: str,
    trade_date: date,
    legs: List[Dict],
    instruments: List[Dict],
) -> Tuple[List[Tuple[Optional[int], date]], date]:
    """
    Resolve tokens for all legs of a multi-leg strategy.

    Returns:
      ([(token, expiry), ...], common_expiry)
    All legs share the same expiry (nearest weekly).
    """
    expiry = nearest_weekly_expiry(symbol, trade_date)
    resolved = []
    for leg in legs:
        token = resolve_instrument_token(
            symbol, expiry, leg["strike"], leg["typ"], instruments
        )
        resolved.append((token, expiry))
    return resolved, expiry
