"""
Simulation engine per PRD Section 7.
Deterministic candle generation, indicator computation, and option pricing.
"""
import hashlib
import math
from datetime import date, time
from typing import Dict, List, Tuple

import numpy as np

# ── Instrument configuration ─────────────────────────────────────────────────
INSTRUMENT_CONFIG = {
    "NIFTY": {
        "lot_size": 50,
        "tick_size": 50,
        "base_price": 22000.0,
        "yf_ticker": "^NSEI",
    },
    "BANKNIFTY": {
        "lot_size": 25,
        "tick_size": 100,
        "base_price": 48000.0,
        "yf_ticker": "^NSEBANK",
    },
}

CANDLES_PER_DAY = 375          # 09:15 → 15:29 inclusive
ENTRY_CANDLE_IDX = 15          # 09:30 (15 min after open)
EOD_CANDLE_IDX = 360           # 15:15 (hard end-of-day)
SESSION_START_H, SESSION_START_M = 9, 15


# ── Seeding helpers ───────────────────────────────────────────────────────────
def _seed(trade_date: date, instrument: str) -> int:
    key = f"{trade_date.strftime('%Y%m%d')}_{instrument}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


# ── Zerodha real candle fetch ─────────────────────────────────────────────────

def _candles_from_zerodha(trade_date: date, instrument: str) -> Tuple[np.ndarray, float]:
    """
    Fetch real underlying 1-min candles from Zerodha for *instrument* on *trade_date*.
    Returns (candles shape (375,4), daily_vol).
    Raises DataUnavailableError or RuntimeError if unavailable/unauthenticated.
    """
    from app.services import zerodha_client
    from app.services.option_resolver import UNDERLYING_TOKENS
    from datetime import time as dtime

    token = UNDERLYING_TOKENS[instrument]
    records = zerodha_client.fetch_candles(token, trade_date)

    session_start = dtime(9, 15)
    session_end = dtime(15, 29)
    filtered = [r for r in records if session_start <= r["date"].time() <= session_end]

    if len(filtered) < int(CANDLES_PER_DAY * 0.8):
        raise zerodha_client.DataUnavailableError(
            f"Insufficient candles for {instrument} on {trade_date}: got {len(filtered)}"
        )

    candles = np.full((CANDLES_PER_DAY, 4), np.nan)
    for r in filtered:
        t = r["date"].time()
        idx = (t.hour - 9) * 60 + t.minute - 15
        if 0 <= idx < CANDLES_PER_DAY:
            candles[idx] = [r["open"], r["high"], r["low"], r["close"]]

    # Forward-fill any NaN gaps
    if not np.isnan(candles[0, 3]):
        for i in range(1, CANDLES_PER_DAY):
            if np.isnan(candles[i, 3]):
                candles[i] = candles[i - 1]

    closes = candles[:, 3]
    valid = closes[~np.isnan(closes) & (closes > 0)]
    if len(valid) > 10:
        log_ret = np.diff(np.log(valid))
        daily_vol = float(np.clip(np.std(log_ret) * math.sqrt(CANDLES_PER_DAY), 0.007, 0.020))
    else:
        daily_vol = 0.013

    return candles, daily_vol


def _get_candles_and_source(
    trade_date: date, instrument: str
) -> Tuple[np.ndarray, float, str]:
    """Try Zerodha first; fall back to synthetic. Returns (candles, daily_vol, data_source)."""
    try:
        candles, daily_vol = _candles_from_zerodha(trade_date, instrument)
        return candles, daily_vol, "ZERODHA"
    except Exception:
        candles, daily_vol = generate_candles(trade_date, instrument)
        return candles, daily_vol, "SYNTHETIC"


def _iv_rank_from_vol(daily_vol: float) -> int:
    """Map realized daily vol to IV rank 0–100 using typical NIFTY/BANKNIFTY vol range."""
    lo, hi = 0.007, 0.020
    return int(max(0, min(100, round((daily_vol - lo) / (hi - lo) * 100.0))))


def _fetch_option_price_map(token: int, trade_date: date) -> Dict[str, float]:
    """Return {HH:MM → close_price} for the option contract on trade_date."""
    from app.services import zerodha_client
    records = zerodha_client.fetch_candles(token, trade_date)
    return {
        f"{r['date'].hour:02d}:{r['date'].minute:02d}": float(r["close"])
        for r in records
    }


# ── Base price (tries yfinance, falls back to synthetic) ─────────────────────
def _base_price(trade_date: date, instrument: str) -> float:
    try:
        import yfinance as yf
        from datetime import timedelta

        ticker = INSTRUMENT_CONFIG[instrument]["yf_ticker"]
        start = (trade_date - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (trade_date + timedelta(days=1)).strftime("%Y-%m-%d")
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if not df.empty:
            # Normalise index to plain date strings
            df.index = df.index.normalize()
            target = str(trade_date)
            matches = df[df.index.astype(str) == target]
            if not matches.empty:
                close_val = matches["Close"].iloc[0]
                # Handle case where it might be a Series
                if hasattr(close_val, '__len__'):
                    close_val = float(close_val.iloc[0])
                else:
                    close_val = float(close_val)
                if close_val > 0:
                    return close_val
            # Nearest previous day
            val = df["Close"].iloc[-1]
            if hasattr(val, '__len__'):
                val = float(val.iloc[0])
            else:
                val = float(val)
            if val > 0:
                return val
    except Exception:
        pass

    # Synthetic fallback: base × seeded drift
    rng = np.random.RandomState(_seed(trade_date, instrument))
    base = INSTRUMENT_CONFIG[instrument]["base_price"]
    drift = rng.normal(0.0, 0.12)
    return base * (1.0 + drift)


# ── Candle generation (PRD §7.1) ─────────────────────────────────────────────
def generate_candles(trade_date: date, instrument: str) -> Tuple[np.ndarray, float]:
    """
    Returns (candles, daily_vol) where candles has shape (375, 4) → [open, high, low, close].
    daily_vol is the log-normal daily volatility used for option pricing.
    """
    seed = _seed(trade_date, instrument)
    rng = np.random.RandomState(seed)

    spot = _base_price(trade_date, instrument)

    # Daily vol: 0.7% – 2.0%, log-normally distributed
    daily_vol = float(np.clip(np.exp(rng.normal(np.log(0.013), 0.35)), 0.007, 0.020))
    minute_vol = daily_vol / math.sqrt(CANDLES_PER_DAY)

    closes = np.empty(CANDLES_PER_DAY)
    closes[0] = spot

    for i in range(1, CANDLES_PER_DAY):
        if i < 30:
            vm = 1.6
        elif i >= 360:
            vm = 1.4
        else:
            vm = 0.65
        closes[i] = closes[i - 1] * math.exp(rng.normal(0.0, minute_vol * vm))

    # Build OHLC
    candles = np.empty((CANDLES_PER_DAY, 4))
    for i in range(CANDLES_PER_DAY):
        c = closes[i]
        p = closes[i - 1] if i > 0 else c
        half = abs(rng.normal(0.0, minute_vol * 0.4))
        o = p * math.exp(rng.normal(0.0, minute_vol * 0.15))
        high = max(o, c) * (1.0 + half)
        low = min(o, c) * max(1.0 - half, 0.001)
        candles[i] = [o, high, low, c]

    return candles, daily_vol


# ── Technical indicators (PRD §7.2) ──────────────────────────────────────────
def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    ema = np.empty(len(prices))
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = prices[i] * k + ema[i - 1] * (1.0 - k)
    return ema


def compute_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(prices)
    rsi = np.zeros(n)
    if n < period + 1:
        return rsi

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    def _rs_to_rsi(ag, al):
        if al == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    rsi[period] = _rs_to_rsi(avg_gain, avg_loss)

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        rsi[i] = _rs_to_rsi(avg_gain, avg_loss)

    return rsi


def get_iv_rank(trade_date: date, instrument: str) -> int:
    rng = np.random.RandomState(_seed(trade_date, instrument) + 999)
    return int(rng.randint(15, 86))


# ── Option pricing — simplified BS approximation (PRD §7.5) ──────────────────
def price_option(spot: float, strike: float, daily_vol: float,
                 remaining_minutes: int, opt_type: str) -> float:
    """
    Simplified BS approximation:
      intrinsic  = max(spot-strike, 0) for CE; max(strike-spot, 0) for PE
      time_value = spot × annual_vol × sqrt(T) × 0.45 × exp(-d / (annual_vol × 0.25))
        where T = remaining / (375 × 252),  d = |spot - strike| / spot
      price = max(intrinsic + time_value, 0.50)
    """
    if opt_type.upper() == "CE":
        intrinsic = max(spot - strike, 0.0)
    else:
        intrinsic = max(strike - spot, 0.0)

    if remaining_minutes <= 0:
        return max(intrinsic, 0.50)

    T = remaining_minutes / (CANDLES_PER_DAY * 252.0)
    annual_vol = daily_vol * math.sqrt(252.0)
    d = abs(spot - strike) / spot

    denom = annual_vol * 0.25
    decay = math.exp(-d / denom) if denom > 0 else 0.0
    time_value = spot * annual_vol * math.sqrt(T) * 0.45 * decay

    return max(intrinsic + time_value, 0.50)


# ── Candle index → wall-clock time string ─────────────────────────────────────
def _idx_to_time(idx: int) -> str:
    total_min = SESSION_START_M + idx
    h = SESSION_START_H + total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


def _idx_to_time_obj(idx: int) -> time:
    total_min = SESSION_START_M + idx
    h = SESSION_START_H + total_min // 60
    m = total_min % 60
    return time(h, m)


# ── Main day simulation ───────────────────────────────────────────────────────
def run_day_simulation(trade_date: date, instrument: str, capital: float) -> Dict:
    from app.services.strategy import select_strategy, build_legs
    from app.services.position_sizer import size_position

    cfg = INSTRUMENT_CONFIG[instrument]
    tick_size = cfg["tick_size"]
    lot_size = cfg["lot_size"]

    candles, daily_vol, data_source = _get_candles_and_source(trade_date, instrument)
    closes = candles[:, 3]

    ema5 = compute_ema(closes, 5)
    ema20 = compute_ema(closes, 20)
    rsi = compute_rsi(closes, 14)

    iv_rank = (
        _iv_rank_from_vol(daily_vol)
        if data_source == "ZERODHA"
        else get_iv_rank(trade_date, instrument)
    )

    # Regime assessed at candle index 15 (09:30) — PRD §7.2
    e5 = float(ema5[ENTRY_CANDLE_IDX])
    e20 = float(ema20[ENTRY_CANDLE_IDX])
    rsi_val = float(rsi[ENTRY_CANDLE_IDX])
    spot_in = float(closes[ENTRY_CANDLE_IDX])

    regime, strategy = select_strategy(e5, e20, rsi_val, iv_rank)

    if strategy == "NO_TRADE":
        return _no_trade_result(
            trade_date, instrument, capital,
            regime, iv_rank, spot_in, e5, e20, rsi_val,
            no_trade_reason="NO_SIGNAL", data_source=data_source,
        )

    remaining_at_entry = CANDLES_PER_DAY - ENTRY_CANDLE_IDX
    legs = build_legs(spot_in, instrument, strategy, daily_vol, remaining_at_entry)
    # Make legs mutable for possible entry price updates
    legs = [dict(leg) for leg in legs]

    lots, max_profit_per_lot, max_loss_per_lot = size_position(
        capital, legs, lot_size, tick_size
    )

    max_profit = max_profit_per_lot * lots
    max_loss = max_loss_per_lot * lots

    profit_mult = 0.45 if strategy == "IRON_CONDOR" else 0.55
    profit_target = max_profit * profit_mult
    hard_stop = -max_loss * 0.75

    # ── Resolve real option prices if Zerodha is available ────────────────────
    expiry_date = None
    option_price_maps: Dict[int, Dict[str, float]] = {}  # leg_idx → {HH:MM: price}

    if data_source == "ZERODHA":
        try:
            from app.services import zerodha_client
            from app.services import option_resolver
            instruments_master = zerodha_client.get_instruments("NFO")
            resolved, expiry_date = option_resolver.resolve_all_legs(
                instrument, trade_date, legs, instruments_master
            )
            entry_time_str = _idx_to_time(ENTRY_CANDLE_IDX)
            for li, (token, _exp) in enumerate(resolved):
                if token is not None:
                    try:
                        pmap = _fetch_option_price_map(token, trade_date)
                        option_price_maps[li] = pmap
                        # Update entry price with real market price
                        if entry_time_str in pmap:
                            legs[li]["ep"] = pmap[entry_time_str]
                    except Exception:
                        pass  # fall back to BS price for this leg
        except Exception:
            pass  # fall back entirely to BS pricing

    # ── Candle-by-candle P&L loop ─────────────────────────────────────────────
    min_data: List[Dict] = []
    exit_idx = ENTRY_CANDLE_IDX
    exit_reason = "END_OF_DAY"
    current_pnl = 0.0

    for i in range(ENTRY_CANDLE_IDX, min(EOD_CANDLE_IDX + 1, CANDLES_PER_DAY)):
        spot = float(closes[i])
        remaining = CANDLES_PER_DAY - i
        time_str = _idx_to_time(i)

        current_pnl = 0.0
        for li, leg in enumerate(legs):
            if li in option_price_maps and time_str in option_price_maps[li]:
                px = option_price_maps[li][time_str]
            else:
                px = price_option(spot, leg["strike"], daily_vol, remaining, leg["typ"])
            if leg["act"] == "SELL":
                current_pnl += (leg["ep"] - px) * lots * lot_size
            else:
                current_pnl += (px - leg["ep"]) * lots * lot_size

        min_data.append({"time": time_str, "spot": round(spot, 2), "pnl": round(current_pnl, 2)})
        exit_idx = i

        if current_pnl >= profit_target:
            exit_reason = "PROFIT_TARGET"
            break
        if current_pnl <= hard_stop:
            exit_reason = "HARD_EXIT"
            break

    # ── Build final leg detail with exit prices ───────────────────────────────
    spot_out = float(closes[exit_idx])
    remaining_at_exit = CANDLES_PER_DAY - exit_idx
    exit_time_str = _idx_to_time(exit_idx)

    final_legs = []
    total_pnl = 0.0
    for li, leg in enumerate(legs):
        if li in option_price_maps and exit_time_str in option_price_maps[li]:
            exit_px = option_price_maps[li][exit_time_str]
        else:
            exit_px = price_option(spot_out, leg["strike"], daily_vol, remaining_at_exit, leg["typ"])
        if leg["act"] == "SELL":
            leg_pnl = (leg["ep"] - exit_px) * lots * lot_size
        else:
            leg_pnl = (exit_px - leg["ep"]) * lots * lot_size
        total_pnl += leg_pnl
        final_legs.append({
            "id": li + 1,
            "act": leg["act"],
            "typ": leg["typ"],
            "strike": leg["strike"],
            "delta": leg["delta"],
            "ep": round(leg["ep"], 2),
            "ep2": round(exit_px, 2),
            "legPnl": round(leg_pnl, 2),
            "lots": lots,
        })

    pnl_pct = total_pnl / float(capital) * 100.0
    wl = "WIN" if total_pnl > 0 else ("LOSS" if total_pnl < 0 else "BREAK_EVEN")

    return {
        "instrument": instrument,
        "session_date": trade_date,
        "capital": capital,
        "regime": regime,
        "iv_rank": iv_rank,
        "strategy": strategy,
        "entry_time": _idx_to_time_obj(ENTRY_CANDLE_IDX),
        "exit_time": _idx_to_time_obj(exit_idx),
        "exit_reason": exit_reason,
        "spot_in": round(spot_in, 2),
        "spot_out": round(spot_out, 2),
        "lots": lots,
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "pnl": round(total_pnl, 2),
        "pnl_pct": round(pnl_pct, 4),
        "wl": wl,
        "ema5": round(e5, 2),
        "ema20": round(e20, 2),
        "rsi14": round(rsi_val, 2),
        "legs": final_legs,
        "min_data": min_data,
        "no_trade_reason": None,
        "expiry_date": expiry_date,
        "data_source": data_source,
    }


def _no_trade_result(
    trade_date, instrument, capital,
    regime, iv_rank, spot, e5, e20, rsi_val,
    no_trade_reason: str = "NO_SIGNAL",
    data_source: str = "SYNTHETIC",
) -> Dict:
    return {
        "instrument": instrument,
        "session_date": trade_date,
        "capital": capital,
        "regime": regime,
        "iv_rank": iv_rank,
        "strategy": "NO_TRADE",
        "entry_time": None,
        "exit_time": None,
        "exit_reason": no_trade_reason,
        "spot_in": round(spot, 2) if spot else None,
        "spot_out": round(spot, 2) if spot else None,
        "lots": 0,
        "max_profit": 0.0,
        "max_loss": 0.0,
        "pnl": 0.0,
        "pnl_pct": 0.0,
        "wl": "NO_TRADE",
        "ema5": round(e5, 2) if e5 else None,
        "ema20": round(e20, 2) if e20 else None,
        "rsi14": round(rsi_val, 2) if rsi_val else None,
        "legs": [],
        "min_data": [],
        "no_trade_reason": no_trade_reason,
        "expiry_date": None,
        "data_source": data_source,
    }
