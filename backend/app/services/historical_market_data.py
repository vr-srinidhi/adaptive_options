"""
Historical market data provider — DB-backed drop-in for Zerodha live data.

Loads spot + options candles from the warehouse tables and produces the
exact data structures expected by run_paper_engine_core().

Key types produced:
  spot_candles        : List[Dict]  — each has {"date": datetime, "open", "high", "low", "close", "volume"}
  option_market_index : Dict[(strike, opt_type), Dict[minute_idx, {price, volume, oi}]]
  option_candles_raw  : Dict[(strike, opt_type), List[Dict]]  — same schema as spot_candles
  expiry              : date
  lot_size            : int
  legs_to_fetch       : set of (strike, opt_type)
"""
import logging
from datetime import date as date_type, datetime, time
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.historical import OptionsCandle, SpotCandle
from app.services.opening_range import (
    generate_bearish_candidates,
    generate_bullish_candidates,
    compute_opening_range,
    OR_WINDOW_MINUTES,
)

log = logging.getLogger(__name__)

# NIFTY lot size is constant throughout the 2022-2026 data range.
_NIFTY_LOT_SIZE = 75

# Market session bounds (naive times)
_SESSION_START = time(9, 15)
_SESSION_END   = time(15, 30)


# ── Candle builders ───────────────────────────────────────────────────────────

def _spot_row_to_candle(row: SpotCandle) -> Dict[str, Any]:
    """Convert a SpotCandle ORM row to the paper-engine candle dict format."""
    return {
        "date":   row.timestamp,          # naive datetime
        "open":   float(row.open),
        "high":   float(row.high),
        "low":    float(row.low),
        "close":  float(row.close),
        "volume": int(row.volume or 0),
    }


def _option_row_to_candle(row: OptionsCandle) -> Dict[str, Any]:
    price = float(row.ltp if row.ltp is not None else row.close)
    return {
        "date":   row.timestamp,
        "open":   float(row.open) if row.open is not None else price,
        "high":   float(row.high) if row.high is not None else price,
        "low":    float(row.low)  if row.low  is not None else price,
        "close":  price,
        "volume": int(row.volume or 0),
        "oi":     int(row.open_interest or 0),
    }


# ── Expiry resolution ─────────────────────────────────────────────────────────

async def resolve_expiry_from_db(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
) -> Optional[date_type]:
    """
    Find the nearest weekly expiry >= trade_date available in options_candles.
    Prefers the smallest expiry >= trade_date (nearest weekly).
    """
    result = await db.execute(
        text(
            "SELECT DISTINCT expiry_date FROM options_candles "
            "WHERE symbol = :sym AND trade_date = :td AND expiry_date >= :td "
            "ORDER BY expiry_date ASC LIMIT 10"
        ),
        {"sym": instrument, "td": trade_date},
    )
    rows = result.fetchall()
    if not rows:
        return None
    return rows[0][0]


async def resolve_monthly_expiry_from_db(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
) -> Optional[date_type]:
    """Return the last expiry within trade_date's calendar month."""
    result = await db.execute(
        text(
            "SELECT DISTINCT expiry_date FROM options_candles "
            "WHERE symbol = :sym AND trade_date = :td "
            "  AND EXTRACT(YEAR FROM expiry_date)  = :yr "
            "  AND EXTRACT(MONTH FROM expiry_date) = :mo "
            "  AND expiry_date >= :td "
            "ORDER BY expiry_date DESC LIMIT 1"
        ),
        {"sym": instrument, "td": trade_date, "yr": trade_date.year, "mo": trade_date.month},
    )
    row = result.fetchone()
    return row[0] if row else None


# ── Spot candles ──────────────────────────────────────────────────────────────

async def load_spot_candles(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
) -> List[Dict[str, Any]]:
    """Load 1-min spot candles for the day, sorted by timestamp."""
    result = await db.execute(
        select(SpotCandle)
        .where(SpotCandle.symbol == instrument, SpotCandle.trade_date == trade_date)
        .order_by(SpotCandle.timestamp)
    )
    rows = result.scalars().all()
    candles = [_spot_row_to_candle(r) for r in rows]
    log.info("Loaded %d spot candles for %s %s", len(candles), instrument, trade_date)
    return candles


# ── Option candles ────────────────────────────────────────────────────────────

async def load_option_candles_for_strikes(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
    expiry: date_type,
    legs_to_fetch: Set[Tuple[int, str]],
) -> Tuple[
    Dict[Tuple[int, str], Dict[int, Dict[str, Any]]],   # option_market_index
    Dict[Tuple[int, str], List[Dict[str, Any]]],         # option_candles_raw
]:
    """
    Bulk-load all option candles for the given (strike, opt_type) set.

    Returns:
      option_market_index  — {(strike, opt_type): {minute_idx: {price, volume, oi}}}
      option_candles_raw   — {(strike, opt_type): [candle_dict, ...]}

    minute_idx is relative to 09:15, so index 0 = 09:15, 15 = 09:30, etc.
    """
    if not legs_to_fetch:
        return {}, {}

    strikes = list({s for s, _ in legs_to_fetch})
    opt_types = list({t for _, t in legs_to_fetch})

    result = await db.execute(
        select(OptionsCandle)
        .where(
            OptionsCandle.symbol == instrument,
            OptionsCandle.trade_date == trade_date,
            OptionsCandle.expiry_date == expiry,
            OptionsCandle.strike.in_(strikes),
            OptionsCandle.option_type.in_(opt_types),
        )
        .order_by(OptionsCandle.strike, OptionsCandle.option_type, OptionsCandle.timestamp)
    )
    rows = result.scalars().all()

    # Group by (strike, opt_type)
    raw: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
    for row in rows:
        key = (int(row.strike), row.option_type)
        if key not in legs_to_fetch:
            continue
        raw.setdefault(key, []).append(_option_row_to_candle(row))

    # Build minute index: map candle timestamp → minute offset from 09:15
    session_start_dt = datetime.combine(trade_date, _SESSION_START)

    option_market_index: Dict[Tuple[int, str], Dict[int, Dict[str, Any]]] = {}
    option_candles_out: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}

    for key, candles in raw.items():
        idx_map: Dict[int, Dict[str, Any]] = {}
        for c in candles:
            ts: datetime = c["date"]
            # Compute minute offset from session start
            delta_min = int((ts - session_start_dt).total_seconds() / 60)
            if delta_min < 0:
                continue  # pre-market
            idx_map[delta_min] = {
                "price":  c["close"],
                "volume": c["volume"],
                "oi":     c.get("oi", 0),
            }
        option_market_index[key] = idx_map
        option_candles_out[key] = candles

    loaded = len(raw)
    missing = legs_to_fetch - set(raw.keys())
    if missing:
        log.warning("Option candles missing for %d legs on %s: %s", len(missing), trade_date, missing)
    log.info("Loaded option candles for %d/%d legs on %s", loaded, len(legs_to_fetch), trade_date)

    return option_market_index, option_candles_out


# ── High-level loader (called by batch runner) ─────────────────────────────────

async def load_historical_session_data(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
) -> Optional[Dict[str, Any]]:
    """
    Load all data needed to call run_paper_engine_core() for one historical day.

    Returns None if the day has insufficient data (< OR_WINDOW_MINUTES spot candles
    or no expiry found in options_candles).

    Returns a dict with keys:
      spot_candles, option_market_index, option_candles_raw,
      expiry, monthly_expiry, lot_size, legs_to_fetch
    """
    # 1. Spot candles
    spot_candles = await load_spot_candles(db, instrument, trade_date)
    if len(spot_candles) < OR_WINDOW_MINUTES:
        log.warning(
            "Insufficient spot candles for %s %s: got %d, need ≥ %d",
            instrument, trade_date, len(spot_candles), OR_WINDOW_MINUTES,
        )
        return None

    # 2. OR + candidate generation
    or_high, or_low = compute_opening_range(spot_candles)

    bullish_candidates = generate_bullish_candidates(or_high)
    bearish_candidates = generate_bearish_candidates(or_low)

    legs_to_fetch: Set[Tuple[int, str]] = set()
    for long_s, short_s in bullish_candidates:
        legs_to_fetch.add((long_s, "CE"))
        legs_to_fetch.add((short_s, "CE"))
    for long_s, short_s in bearish_candidates:
        legs_to_fetch.add((long_s, "PE"))
        legs_to_fetch.add((short_s, "PE"))

    # 3. Expiry from options_candles
    expiry = await resolve_expiry_from_db(db, instrument, trade_date)
    if expiry is None:
        log.warning("No expiry found in options_candles for %s %s", instrument, trade_date)
        return None

    monthly_expiry = await resolve_monthly_expiry_from_db(db, instrument, trade_date)
    if monthly_expiry == expiry:
        monthly_expiry = None

    # 4. Option candles
    option_market_index, option_candles_raw = await load_option_candles_for_strikes(
        db, instrument, trade_date, expiry, legs_to_fetch
    )

    # 5. Lot size (hardcoded — constant for NIFTY throughout history)
    lot_size = _NIFTY_LOT_SIZE

    return {
        "spot_candles":         spot_candles,
        "option_market_index":  option_market_index,
        "option_candles_raw":   option_candles_raw,
        "expiry":               expiry,
        "monthly_expiry":       monthly_expiry,
        "lot_size":             lot_size,
        "legs_to_fetch":        legs_to_fetch,
    }
