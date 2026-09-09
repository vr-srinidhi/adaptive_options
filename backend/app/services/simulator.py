"""
Simulation engine per PRD Section 7.
Deterministic candle generation, indicator computation, and option pricing.
"""
import hashlib
import math
from datetime import date, time
from typing import Dict, List, Optional, Tuple

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

CANDLES_PER_DAY   = 375   # 09:15 → 15:29 inclusive
ENTRY_CANDLE_IDX  = 15    # 09:30 — legacy default entry (kept for tests)
EOD_CANDLE_IDX    = 360   # 15:15 — hard end-of-day
FORCE_EXIT_IDX    = 365   # 15:20 — skill rule: no open positions after 15:20
SIGNAL_SCAN_START = 5     # 09:20 — skip first 5 min (opening auction)
SIGNAL_SCAN_END   = 330   # 14:45 — no new positions after 14:45
SESSION_START_H, SESSION_START_M = 9, 15


# ── Seeding helpers ───────────────────────────────────────────────────────────
def _seed(trade_date: date, instrument: str) -> int:
    key = f"{trade_date.strftime('%Y%m%d')}_{instrument}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


# ── Zerodha real candle fetch ─────────────────────────────────────────────────

def _candles_from_zerodha(
    trade_date: date,
    instrument: str,
    access_token: Optional[str] = None,
) -> Tuple[np.ndarray, float]:
    """
    Fetch real underlying 1-min candles from Zerodha for *instrument* on *trade_date*.
    Returns (candles shape (375,4), daily_vol).
    Raises DataUnavailableError or RuntimeError if unavailable/unauthenticated.
    """
    from app.services.zerodha_client import fetch_candles_with_token
    from app.services.option_resolver import UNDERLYING_TOKENS
    from datetime import time as dtime

    if not access_token:
        raise RuntimeError("No per-user Zerodha access token available.")

    token = UNDERLYING_TOKENS[instrument]
    records = fetch_candles_with_token(token, trade_date, access_token)

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
    trade_date: date,
    instrument: str,
    access_token: Optional[str] = None,
) -> Tuple[np.ndarray, float, str]:
    """Try Zerodha first; fall back to synthetic. Returns (candles, daily_vol, data_source)."""
    try:
        candles, daily_vol = _candles_from_zerodha(trade_date, instrument, access_token)
        return candles, daily_vol, "ZERODHA"
    except Exception:
        candles, daily_vol = generate_candles(trade_date, instrument)
        return candles, daily_vol, "SYNTHETIC"


def _iv_rank_from_vol(daily_vol: float) -> int:
    """Map realized daily vol to IV rank 0–100 using typical NIFTY/BANKNIFTY vol range."""
    lo, hi = 0.007, 0.020
    return int(max(0, min(100, round((daily_vol - lo) / (hi - lo) * 100.0))))


def _fetch_option_price_map(
    token: int,
    trade_date: date,
    access_token: str,
) -> Dict[str, float]:
    """Return {HH:MM → close_price} for the option contract on trade_date."""
    from app.services.zerodha_client import fetch_candles_with_token

    records = fetch_candles_with_token(token, trade_date, access_token)
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


def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                period: int = 14) -> np.ndarray:
    """True Range → smoothed ATR."""
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i]  - close[i - 1]))
    atr = np.empty(n)
    atr[:period] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_indicators_df(candles: np.ndarray) -> "pd.DataFrame":
    """
    Build a full indicator DataFrame from OHLC candle array (shape N×4 or N×5).
    Columns added: ema9, ema21, ema50, rsi, atr14, ema_cross, ema_cross_change,
                   vol_ma20, time.
    """
    import pandas as pd

    cols = ["open", "high", "low", "close"]
    df = pd.DataFrame(candles[:, :4], columns=cols)
    df["time"] = [_idx_to_time(i) for i in range(len(df))]

    if candles.shape[1] >= 5:
        df["volume"] = candles[:, 4].astype(float)
    else:
        df["volume"] = 0.0

    # EMAs (pandas ewm for speed)
    df["ema9"]  = df["close"].ewm(span=9,  adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    # RSI 14
    delta = df["close"].diff()
    gain  = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    df["rsi"] = 100.0 - (100.0 / (1.0 + gain / (loss + 1e-10)))

    # ATR 14
    df["atr14"] = compute_atr(
        df["high"].values, df["low"].values, df["close"].values, 14
    )

    # EMA crossover
    df["ema_cross"]        = np.where(df["ema9"] > df["ema21"], 1, -1)
    df["ema_cross_change"] = df["ema_cross"].diff().fillna(0)

    # Volume MA20
    df["vol_ma20"] = df["volume"].rolling(20).mean().fillna(0)

    return df


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

def run_day_simulation(
    trade_date: date,
    instrument: str,
    capital: float,
    *,
    access_token: Optional[str] = None,
) -> Dict:
    """
    Simulate one trading day using the skill-enhanced engine:
      - 10-regime detection + signal scoring (EMA9/21, ATR14, RSI14)
      - Dynamic entry: first valid signal in 09:20–14:45 window
      - Strategy catalog: IRON_CONDOR, BULL/BEAR spreads, LONG_CE/PE, debit spreads
      - R-multiple trailing stop for directional trades
      - Zerodha real data when authenticated, synthetic fallback otherwise
    """
    import pandas as pd
    from app.services.strategy import (
        select_strategy_v2, build_legs, regime_to_simple,
        detect_regime, MIN_SIGNAL_SCORE,
    )
    from app.services.position_sizer import size_position, size_long_position, size_debit_spread

    cfg      = INSTRUMENT_CONFIG[instrument]
    tick_size = cfg["tick_size"]
    lot_size  = cfg["lot_size"]

    candles, daily_vol, data_source = _get_candles_and_source(
        trade_date,
        instrument,
        access_token,
    )
    closes = candles[:, 3]

    df = compute_indicators_df(candles)

    iv_rank = (
        _iv_rank_from_vol(daily_vol)
        if data_source == "ZERODHA"
        else get_iv_rank(trade_date, instrument)
    )

    # ── Signal scan: find first tradeable entry in the valid window ───────────
    entry_idx    = None
    regime_detail = "NEUTRAL"
    strategy     = "NO_TRADE"
    signal_type  = "NO_SIGNAL"
    signal_score = 0

    for scan_idx in range(SIGNAL_SCAN_START, SIGNAL_SCAN_END + 1):
        if pd.isna(df.at[scan_idx, "rsi"]):
            continue
        rd, strat, sig, score = select_strategy_v2(df, scan_idx, iv_rank)
        if strat != "NO_TRADE" and score >= MIN_SIGNAL_SCORE:
            entry_idx    = scan_idx
            regime_detail = rd
            strategy     = strat
            signal_type  = sig
            signal_score = score
            break

    # Convenience values at entry (or fallback candle 15 for NO_TRADE rows)
    ref_idx  = entry_idx if entry_idx is not None else ENTRY_CANDLE_IDX
    ref_row  = df.iloc[ref_idx]
    spot_in  = float(closes[ref_idx])
    e9       = float(ref_row["ema9"])
    e21      = float(ref_row["ema21"])
    rsi_val  = float(ref_row["rsi"]) if not pd.isna(ref_row["rsi"]) else 50.0
    atr_val  = float(ref_row["atr14"]) if not pd.isna(ref_row["atr14"]) else 0.0

    if entry_idx is None:
        regime_detail = detect_regime(df, ENTRY_CANDLE_IDX)
        regime_simple = regime_to_simple(regime_detail)
        return _no_trade_result(
            trade_date, instrument, capital,
            regime_simple, iv_rank, spot_in, e9, e21, rsi_val,
            no_trade_reason="NO_SIGNAL", data_source=data_source,
            regime_detail=regime_detail, atr14=atr_val,
        )

    regime_simple = regime_to_simple(regime_detail)

    # ── Build legs ────────────────────────────────────────────────────────────
    remaining_at_entry = CANDLES_PER_DAY - entry_idx
    legs = [dict(leg) for leg in build_legs(
        spot_in, instrument, strategy, daily_vol, remaining_at_entry
    )]

    IS_LONG      = strategy in ("LONG_CE", "LONG_PE")
    IS_DEBIT_SPR = strategy in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD")

    # ── Position sizing ───────────────────────────────────────────────────────
    if IS_LONG:
        entry_px      = legs[0]["ep"]
        sl_px         = max(0.50, entry_px * 0.50)      # 50% SL on premium
        risk_unit     = entry_px - sl_px
        lots          = size_long_position(entry_px, sl_px, capital, lot_size)
        max_loss      = risk_unit * lots * lot_size
        max_profit    = risk_unit * 2.5 * lots * lot_size  # 2.5R target

    elif IS_DEBIT_SPR:
        buy_leg  = next(l for l in legs if l["act"] == "BUY")
        sell_leg = next(l for l in legs if l["act"] == "SELL")
        net_debit = buy_leg["ep"] - sell_leg["ep"]
        lots       = size_debit_spread(net_debit, capital, lot_size)
        max_loss   = net_debit * lots * lot_size
        max_profit = (tick_size * 2 - net_debit) * lots * lot_size  # spread width - debit

    else:  # credit spreads
        lots, max_profit_per_lot, max_loss_per_lot = size_position(
            capital, legs, lot_size, tick_size
        )
        max_profit = max_profit_per_lot * lots
        max_loss   = max_loss_per_lot   * lots

    # ── Resolve real option prices from Zerodha ───────────────────────────────
    expiry_date: Optional[date] = None
    option_price_maps: Dict[int, Dict[str, float]] = {}

    if data_source == "ZERODHA":
        try:
            from app.services import option_resolver
            from app.services.zerodha_client import get_instruments_with_token

            if not access_token:
                raise RuntimeError("No per-user Zerodha access token available.")

            master   = get_instruments_with_token(access_token, "NFO")
            resolved, expiry_date = option_resolver.resolve_all_legs(
                instrument, trade_date, legs, master
            )
            entry_ts = _idx_to_time(entry_idx)
            for li, (token, _exp) in enumerate(resolved):
                if token is not None:
                    try:
                        pmap = _fetch_option_price_map(token, trade_date, access_token)
                        option_price_maps[li] = pmap
                        if entry_ts in pmap:
                            legs[li]["ep"] = pmap[entry_ts]
                    except Exception:
                        pass
        except Exception:
            pass

    # ── P&L loop ──────────────────────────────────────────────────────────────
    if IS_LONG:
        # R-multiple trailing stop
        profit_target_px = legs[0]["ep"] + 2.5 * risk_unit
        trailing_sl      = sl_px
    else:
        profit_mult   = 0.45 if strategy == "IRON_CONDOR" else 0.55
        profit_target = max_profit * profit_mult
        hard_stop     = -max_loss * 0.75

    min_data:    List[Dict] = []
    exit_idx    = entry_idx
    exit_reason = "END_OF_DAY"

    for i in range(entry_idx, min(FORCE_EXIT_IDX + 1, CANDLES_PER_DAY)):
        spot      = float(closes[i])
        remaining = CANDLES_PER_DAY - i
        ts        = _idx_to_time(i)

        if IS_LONG:
            leg = legs[0]
            opt_px = (option_price_maps[0][ts]
                      if 0 in option_price_maps and ts in option_price_maps[0]
                      else price_option(spot, leg["strike"], daily_vol, remaining, leg["typ"]))

            # Update trailing stop
            r_now = (opt_px - leg["ep"]) / risk_unit if risk_unit > 0 else 0.0
            if r_now >= 2.0:
                trailing_sl = max(trailing_sl, leg["ep"] + risk_unit)   # lock 1R
            elif r_now >= 1.0:
                trailing_sl = max(trailing_sl, leg["ep"])               # lock BE

            current_pnl = (opt_px - leg["ep"]) * lots * lot_size

            if i > entry_idx and opt_px <= trailing_sl:
                exit_reason = "HARD_EXIT"; break
            if opt_px >= profit_target_px:
                exit_reason = "PROFIT_TARGET"; break

        else:
            current_pnl = 0.0
            for li, leg in enumerate(legs):
                px = (option_price_maps[li][ts]
                      if li in option_price_maps and ts in option_price_maps[li]
                      else price_option(spot, leg["strike"], daily_vol, remaining, leg["typ"]))
                current_pnl += ((leg["ep"] - px) if leg["act"] == "SELL"
                                else (px - leg["ep"])) * lots * lot_size

            if current_pnl >= profit_target:
                exit_reason = "PROFIT_TARGET"; break
            if current_pnl <= hard_stop:
                exit_reason = "HARD_EXIT"; break

        min_data.append({"time": ts, "spot": round(spot, 2), "pnl": round(current_pnl, 2)})
        exit_idx = i

    # ── Final leg detail with exit prices ─────────────────────────────────────
    spot_out          = float(closes[exit_idx])
    remaining_at_exit = CANDLES_PER_DAY - exit_idx
    exit_ts           = _idx_to_time(exit_idx)

    final_legs: List[Dict] = []
    total_pnl = 0.0

    for li, leg in enumerate(legs):
        exit_px = (option_price_maps[li][exit_ts]
                   if li in option_price_maps and exit_ts in option_price_maps[li]
                   else price_option(spot_out, leg["strike"], daily_vol, remaining_at_exit, leg["typ"]))
        leg_pnl = ((leg["ep"] - exit_px) if leg["act"] == "SELL"
                   else (exit_px - leg["ep"])) * lots * lot_size
        total_pnl += leg_pnl
        final_legs.append({
            "id":     li + 1,
            "act":    leg["act"],
            "typ":    leg["typ"],
            "strike": leg["strike"],
            "delta":  leg["delta"],
            "ep":     round(leg["ep"], 2),
            "ep2":    round(exit_px, 2),
            "legPnl": round(leg_pnl, 2),
            "lots":   lots,
        })

    pnl_pct  = total_pnl / float(capital) * 100.0
    wl       = "WIN" if total_pnl > 0 else ("LOSS" if total_pnl < 0 else "BREAK_EVEN")
    r_mult   = (total_pnl / max_loss) if max_loss > 0 else 0.0

    return {
        "instrument":    instrument,
        "session_date":  trade_date,
        "capital":       capital,
        "regime":        regime_simple,          # BULLISH/BEARISH/NEUTRAL (backward compat)
        "regime_detail": regime_detail,          # 10-regime label
        "iv_rank":       iv_rank,
        "strategy":      strategy,
        "signal_type":   signal_type,
        "signal_score":  signal_score,
        "entry_time":    _idx_to_time_obj(entry_idx),
        "exit_time":     _idx_to_time_obj(exit_idx),
        "exit_reason":   exit_reason,
        "spot_in":       round(spot_in, 2),
        "spot_out":      round(spot_out, 2),
        "lots":          lots,
        "max_profit":    round(max_profit, 2),
        "max_loss":      round(max_loss, 2),
        "pnl":           round(total_pnl, 2),
        "pnl_pct":       round(pnl_pct, 4),
        "wl":            wl,
        "ema5":          round(e9,  2),          # ema9  (kept as ema5 for compat)
        "ema20":         round(e21, 2),          # ema21 (kept as ema20 for compat)
        "rsi14":         round(rsi_val, 2),
        "atr14":         round(atr_val, 2),
        "r_multiple":    round(r_mult, 2),
        "legs":          final_legs,
        "min_data":      min_data,
        "no_trade_reason": None,
        "expiry_date":   expiry_date,
        "data_source":   data_source,
    }


def _no_trade_result(
    trade_date, instrument, capital,
    regime, iv_rank, spot, e9, e21, rsi_val,
    no_trade_reason: str = "NO_SIGNAL",
    data_source: str = "SYNTHETIC",
    regime_detail: str = "NEUTRAL",
    atr14: float = 0.0,
) -> Dict:
    return {
        "instrument":    instrument,
        "session_date":  trade_date,
        "capital":       capital,
        "regime":        regime,
        "regime_detail": regime_detail,
        "iv_rank":       iv_rank,
        "strategy":      "NO_TRADE",
        "signal_type":   "NO_SIGNAL",
        "signal_score":  0,
        "entry_time":    None,
        "exit_time":     None,
        "exit_reason":   no_trade_reason,
        "spot_in":       round(spot, 2) if spot else None,
        "spot_out":      round(spot, 2) if spot else None,
        "lots":          0,
        "max_profit":    0.0,
        "max_loss":      0.0,
        "pnl":           0.0,
        "pnl_pct":       0.0,
        "wl":            "NO_TRADE",
        "ema5":          round(e9,  2) if e9  else None,
        "ema20":         round(e21, 2) if e21 else None,
        "rsi14":         round(rsi_val, 2) if rsi_val else None,
        "atr14":         round(atr14, 2),
        "r_multiple":    0.0,
        "legs":          [],
        "min_data":      [],
        "no_trade_reason": no_trade_reason,
        "expiry_date":   None,
        "data_source":   data_source,
    }
