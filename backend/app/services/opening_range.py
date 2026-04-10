"""
Opening Range (OR) computation for the ORB paper-trading strategy.

The Opening Range = High and Low of the first OR_WINDOW_MINUTES candles
(09:15 – 09:29 for a standard NSE session).

Breakout confirmation requires the close to be FOLLOW_THROUGH_PCT% beyond
the range boundary, preventing false signals from single-pip crossings.
"""
import math
from typing import Dict, List, Tuple

# ── Config ────────────────────────────────────────────────────────────────────
OR_WINDOW_MINUTES = 15       # 09:15 (idx 0) → 09:29 (idx 14)
FOLLOW_THROUGH_PCT = 0.001   # 0.1% beyond OR level required
STRIKE_STEP = 50             # NIFTY strike granularity (pts)


# ── OR computation ────────────────────────────────────────────────────────────

def compute_opening_range(candles: List[Dict]) -> Tuple[float, float]:
    """
    Return (or_high, or_low) from the first OR_WINDOW_MINUTES candles.
    Raises ValueError if fewer candles are provided than the window.
    """
    if len(candles) < OR_WINDOW_MINUTES:
        raise ValueError(
            f"Need at least {OR_WINDOW_MINUTES} candles to compute OR; "
            f"got {len(candles)}."
        )
    window = candles[:OR_WINDOW_MINUTES]
    or_high = max(float(c["high"]) for c in window)
    or_low = min(float(c["low"]) for c in window)
    return or_high, or_low


# ── Breakout tests ────────────────────────────────────────────────────────────

def is_bullish_breakout(candle_close: float, or_high: float) -> bool:
    """True when close is FOLLOW_THROUGH_PCT% above OR high."""
    return candle_close > or_high * (1.0 + FOLLOW_THROUGH_PCT)


def is_bearish_breakout(candle_close: float, or_low: float) -> bool:
    """True when close is FOLLOW_THROUGH_PCT% below OR low."""
    return candle_close < or_low * (1.0 - FOLLOW_THROUGH_PCT)


# ── Strike selection ──────────────────────────────────────────────────────────

def select_bullish_strikes(or_high: float, step: int = STRIKE_STEP) -> Tuple[int, int]:
    """
    Bull Call Spread strikes:
      long_strike  = nearest STRIKE_STEP multiple at or above OR high
      short_strike = long_strike + step

    Example: OR high = 22893.40 → long = 22900, short = 22950
    """
    long_strike = int(math.ceil(or_high / step)) * step
    short_strike = long_strike + step
    return long_strike, short_strike


def select_bearish_strikes(or_low: float, step: int = STRIKE_STEP) -> Tuple[int, int]:
    """
    Bear Put Spread strikes:
      long_strike  = nearest STRIKE_STEP multiple at or below OR low
      short_strike = long_strike - step

    Example: OR low = 22719.30 → long = 22700, short = 22650
    """
    long_strike = int(math.floor(or_low / step)) * step
    short_strike = long_strike - step
    return long_strike, short_strike
