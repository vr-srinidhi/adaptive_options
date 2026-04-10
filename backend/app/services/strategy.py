"""
Strategy selection, regime detection, and leg builder.

v2 (skill-enhanced):
  - 10-regime detector (PANIC_SELL, BOTTOMING, CONSOLIDATION, BREAKOUT_UP/DOWN,
    TRENDING_UP/DOWN, OVERBOUGHT_REVERSAL, OVERSOLD_REVERSAL, CHOPPY, NEUTRAL)
  - Signal engine with scoring (REVERSAL_LONG/SHORT, BREAKOUT_LONG/SHORT,
    TREND_CONTINUATION, PREMIUM_SELL_BULLISH/BEARISH, PREMIUM_SELL_RANGE)
  - Expanded strategy catalog: LONG_CE, LONG_PE, BULL_CALL_SPREAD, BEAR_PUT_SPREAD
    in addition to existing IRON_CONDOR, BULL_PUT_SPREAD, BEAR_CALL_SPREAD

Legacy API (select_strategy / build_legs) kept intact for backward compat.
"""
from typing import Dict, List, Tuple, Optional
import numpy as np


# ── Legacy regime / strategy selection (PRD §7.3 — unchanged) ─────────────────

def select_strategy(ema5: float, ema20: float, rsi: float, iv_rank: int) -> Tuple[str, str]:
    """
    Returns (regime, strategy_code).
    regime  : BULLISH | BEARISH | NEUTRAL
    strategy: IRON_CONDOR | BULL_PUT_SPREAD | BEAR_CALL_SPREAD | NO_TRADE
    """
    ema_diff_pct = (ema5 - ema20) / ema20 * 100.0 if ema20 != 0 else 0.0

    if ema5 > ema20 and ema_diff_pct >= 0.15:
        regime = "BULLISH"
    elif ema5 < ema20 and abs(ema_diff_pct) >= 0.15:
        regime = "BEARISH"
    else:
        regime = "NEUTRAL"

    if rsi > 70 or rsi < 30:
        return regime, "NO_TRADE"

    if regime == "BULLISH":
        strategy = "IRON_CONDOR" if (40 <= rsi <= 70 and iv_rank >= 30) else (
            "BULL_PUT_SPREAD" if 40 <= rsi <= 70 else "NO_TRADE"
        )
    elif regime == "BEARISH":
        strategy = "IRON_CONDOR" if (30 <= rsi <= 60 and iv_rank >= 30) else (
            "BEAR_CALL_SPREAD" if 30 <= rsi <= 60 else "NO_TRADE"
        )
    else:
        strategy = "IRON_CONDOR" if (40 <= rsi <= 60 and iv_rank >= 30) else "NO_TRADE"

    return regime, strategy


# ── v2: Regime detection (10 regimes) ─────────────────────────────────────────

def detect_regime(df: "pd.DataFrame", idx: int) -> str:
    """
    Full 10-regime detector operating on a 1-min indicator DataFrame.
    Requires columns: close, high, low, open, ema9, ema21, rsi, atr14,
                      ema_cross_change.
    Returns one of:
      PANIC_SELL | BOTTOMING | CONSOLIDATION | BREAKOUT_UP | BREAKOUT_DOWN |
      TRENDING_UP | TRENDING_DOWN | OVERBOUGHT_REVERSAL | OVERSOLD_REVERSAL |
      CHOPPY | NEUTRAL | INITIALIZING
    """
    import pandas as pd

    if idx < 30:
        return "INITIALIZING"

    row = df.iloc[idx]
    window30 = df.iloc[max(0, idx - 30): idx + 1]
    recent5  = df.iloc[max(0, idx - 5):  idx + 1]

    close = row["close"]
    ema9  = row.get("ema9", close)
    ema21 = row.get("ema21", close)
    atr   = row.get("atr14", 10.0)
    rsi   = row.get("rsi", 50.0)

    if pd.isna(rsi) or pd.isna(atr) or atr == 0:
        return "INITIALIZING"

    candle_range    = row["high"] - row["low"]
    crosses_30      = window30["ema_cross_change"].abs().sum() / 2
    range_20_vals   = window30.tail(20)
    range_20        = range_20_vals["high"].max() - range_20_vals["low"].min()
    ema_spread_pct  = abs(ema9 - ema21) / close
    recent_red      = (recent5["close"] < recent5["open"]).sum()

    # PANIC_SELL
    if (rsi < 30 and close < ema21
            and candle_range > 2 * atr and recent_red >= 3):
        return "PANIC_SELL"

    # OVERBOUGHT_REVERSAL
    if rsi > 75 and close > ema21 + 1.5 * atr:
        return "OVERBOUGHT_REVERSAL"

    # OVERSOLD_REVERSAL
    if rsi < 25 and close < ema21 - 1.5 * atr:
        return "OVERSOLD_REVERSAL"

    # CHOPPY
    if crosses_30 >= 3:
        return "CHOPPY"

    # BOTTOMING
    rsi_series = window30["rsi"].dropna()
    rsi_was_low = (rsi_series.tail(20).min() < 30) if len(rsi_series) >= 5 else False
    if rsi_was_low and 30 < rsi < 50 and candle_range < 0.5 * atr:
        return "BOTTOMING"

    # CONSOLIDATION
    if (ema_spread_pct < 0.0015 and 40 <= rsi <= 60
            and (range_20 / close) < 0.005):
        return "CONSOLIDATION"

    # BREAKOUT_UP
    range_high = range_20_vals["high"].max()
    if ema9 > ema21 and close >= range_high * 0.999 and rsi > 55:
        return "BREAKOUT_UP"

    # BREAKOUT_DOWN
    range_low = range_20_vals["low"].min()
    if ema9 < ema21 and close <= range_low * 1.001 and rsi < 45:
        return "BREAKOUT_DOWN"

    # TRENDING_UP
    older_ema9 = df.iloc[max(0, idx - 10)]["ema9"]
    if ema9 > ema21 and ema9 > older_ema9 and rsi > 50:
        return "TRENDING_UP"

    # TRENDING_DOWN
    if ema9 < ema21 and ema9 < older_ema9 and rsi < 50:
        return "TRENDING_DOWN"

    return "NEUTRAL"


def regime_to_simple(regime_detail: str) -> str:
    """Map 10-regime label → legacy BULLISH/BEARISH/NEUTRAL."""
    if regime_detail in ("TRENDING_UP", "BREAKOUT_UP", "BOTTOMING"):
        return "BULLISH"
    if regime_detail in ("TRENDING_DOWN", "BREAKOUT_DOWN", "PANIC_SELL"):
        return "BEARISH"
    return "NEUTRAL"


# ── v2: Double bottom / top detection ─────────────────────────────────────────

def _detect_double_bottom(df, idx, lookback=30, tol=0.003):
    window = df.iloc[max(0, idx - lookback): idx + 1]
    lows = []
    for i in range(2, len(window) - 1):
        if (window["low"].iloc[i] < window["low"].iloc[i - 1]
                and window["low"].iloc[i] <= window["low"].iloc[i + 1]):
            lows.append(window["low"].iloc[i])
    if len(lows) < 2:
        return False
    return abs(lows[-1] - lows[-2]) / lows[-2] < tol


def _detect_double_top(df, idx, lookback=30, tol=0.003):
    window = df.iloc[max(0, idx - lookback): idx + 1]
    highs = []
    for i in range(2, len(window) - 1):
        if (window["high"].iloc[i] > window["high"].iloc[i - 1]
                and window["high"].iloc[i] >= window["high"].iloc[i + 1]):
            highs.append(window["high"].iloc[i])
    if len(highs) < 2:
        return False
    return abs(highs[-1] - highs[-2]) / highs[-2] < tol


# ── v2: Signal engine ──────────────────────────────────────────────────────────

MIN_SIGNAL_SCORE = 60


def scan_signals(df: "pd.DataFrame", idx: int, regime: str) -> List[Dict]:
    """
    Scan for trade signals at candle *idx*.
    Returns list of {type, score, reasons, regime} sorted by score desc.
    Score ≥ MIN_SIGNAL_SCORE required to trade.
    """
    import pandas as pd

    signals = []
    row = df.iloc[idx]
    rsi  = row.get("rsi", 50.0)
    ema9 = row.get("ema9", row["close"])
    ema21= row.get("ema21", row["close"])
    atr  = row.get("atr14", 10.0)

    if pd.isna(rsi) or pd.isna(atr):
        return signals

    # ── REVERSAL_LONG (BOTTOMING / OVERSOLD_REVERSAL) ─────────────────────
    if regime in ("BOTTOMING", "OVERSOLD_REVERSAL"):
        score = 0
        reasons = []
        window20_rsi = df.iloc[max(0, idx - 20): idx + 1]["rsi"].dropna()
        if rsi > 30 and (len(window20_rsi) > 0 and window20_rsi.min() < 30):
            score += 25; reasons.append("RSI reversal from <30")
        if _detect_double_bottom(df, idx):
            score += 25; reasons.append("Double bottom pattern")
        if row.get("ema_cross_change", 0) > 0:
            score += 25; reasons.append("Bullish EMA9/21 crossover")
        body = abs(row["close"] - row["open"])
        wick_range = row["high"] - row["low"]
        if row["close"] > row["open"] and wick_range > 0 and body / wick_range > 0.6:
            score += 15; reasons.append("Bullish engulfing candle")
        if score >= MIN_SIGNAL_SCORE:
            signals.append({"type": "REVERSAL_LONG", "score": min(score, 100),
                            "reasons": reasons, "regime": regime})

    # ── REVERSAL_SHORT (OVERBOUGHT_REVERSAL) ──────────────────────────────
    if regime == "OVERBOUGHT_REVERSAL":
        score = 0
        reasons = []
        window20_rsi = df.iloc[max(0, idx - 20): idx + 1]["rsi"].dropna()
        if rsi < 70 and (len(window20_rsi) > 0 and window20_rsi.max() > 70):
            score += 25; reasons.append("RSI reversal from >70")
        if _detect_double_top(df, idx):
            score += 25; reasons.append("Double top pattern")
        if row.get("ema_cross_change", 0) < 0:
            score += 25; reasons.append("Bearish EMA9/21 crossover")
        if row["close"] < row["open"]:
            score += 15; reasons.append("Bearish candle at high")
        if score >= MIN_SIGNAL_SCORE:
            signals.append({"type": "REVERSAL_SHORT", "score": min(score, 100),
                            "reasons": reasons, "regime": regime})

    # ── BREAKOUT_LONG (BREAKOUT_UP) ───────────────────────────────────────
    if regime == "BREAKOUT_UP":
        score = 0
        reasons = []
        window20 = df.iloc[max(0, idx - 20): idx + 1]
        if ema9 > ema21:
            score += 25; reasons.append("EMA9 > EMA21")
        if rsi > 55:
            score += 25; reasons.append(f"RSI {rsi:.0f} > 55")
        if row["close"] >= window20["high"].max() * 0.999:
            score += 25; reasons.append("Breaking 20-candle high")
        cons_window = window20[abs(window20["ema9"] - window20["ema21"]) / window20["close"] < 0.002]
        if len(cons_window) > 15:
            score += 15; reasons.append(f"{len(cons_window)}-candle consolidation break")
        if score >= MIN_SIGNAL_SCORE:
            signals.append({"type": "BREAKOUT_LONG", "score": min(score, 100),
                            "reasons": reasons, "regime": regime})

    # ── BREAKOUT_SHORT (BREAKOUT_DOWN) ────────────────────────────────────
    if regime == "BREAKOUT_DOWN":
        score = 0
        reasons = []
        window20 = df.iloc[max(0, idx - 20): idx + 1]
        if ema9 < ema21:
            score += 25; reasons.append("EMA9 < EMA21")
        if rsi < 45:
            score += 25; reasons.append(f"RSI {rsi:.0f} < 45")
        if row["close"] <= window20["low"].min() * 1.001:
            score += 25; reasons.append("Breaking 20-candle low")
        if row["close"] < row["open"]:
            score += 15; reasons.append("Bearish close candle")
        if score >= MIN_SIGNAL_SCORE:
            signals.append({"type": "BREAKOUT_SHORT", "score": min(score, 100),
                            "reasons": reasons, "regime": regime})

    # ── TREND_CONTINUATION_LONG (TRENDING_UP) ─────────────────────────────
    if regime == "TRENDING_UP":
        score = 0
        reasons = []
        near_ema21 = abs(row["close"] - ema21) < atr * 0.5
        rsi_pullback = 45 <= rsi <= 58
        if near_ema21:
            score += 35; reasons.append("Price at EMA21 support")
        if rsi_pullback:
            score += 30; reasons.append(f"RSI pullback to {rsi:.0f}")
        if ema9 > ema21:
            score += 20; reasons.append("EMA9 > EMA21")
        if row["close"] > row["open"]:
            score += 15; reasons.append("Bullish bounce candle")
        if score >= MIN_SIGNAL_SCORE:
            signals.append({"type": "TREND_CONTINUATION_LONG", "score": min(score, 100),
                            "reasons": reasons, "regime": regime})

    # ── TREND_CONTINUATION_SHORT (TRENDING_DOWN) ──────────────────────────
    if regime == "TRENDING_DOWN":
        score = 0
        reasons = []
        near_ema21 = abs(row["close"] - ema21) < atr * 0.5
        rsi_bounce  = 42 <= rsi <= 55
        if near_ema21:
            score += 35; reasons.append("Price at EMA21 resistance")
        if rsi_bounce:
            score += 30; reasons.append(f"RSI bounce to {rsi:.0f}")
        if ema9 < ema21:
            score += 20; reasons.append("EMA9 < EMA21")
        if row["close"] < row["open"]:
            score += 15; reasons.append("Bearish rejection candle")
        if score >= MIN_SIGNAL_SCORE:
            signals.append({"type": "TREND_CONTINUATION_SHORT", "score": min(score, 100),
                            "reasons": reasons, "regime": regime})

    # ── PREMIUM_SELL_BULLISH (CONSOLIDATION / TRENDING_UP) ────────────────
    if regime in ("CONSOLIDATION", "TRENDING_UP") and ema9 >= ema21 and rsi > 45:
        signals.append({"type": "PREMIUM_SELL_BULLISH", "score": 65,
                        "reasons": ["Trend/consolidation with bullish EMA"], "regime": regime})

    # ── PREMIUM_SELL_BEARISH (CONSOLIDATION / TRENDING_DOWN) ──────────────
    if regime in ("CONSOLIDATION", "TRENDING_DOWN") and ema9 <= ema21 and rsi < 55:
        signals.append({"type": "PREMIUM_SELL_BEARISH", "score": 65,
                        "reasons": ["Trend/consolidation with bearish EMA"], "regime": regime})

    # ── PREMIUM_SELL_RANGE (CONSOLIDATION only, both sides) ───────────────
    if regime == "CONSOLIDATION" and 40 <= rsi <= 60:
        signals.append({"type": "PREMIUM_SELL_RANGE", "score": 70,
                        "reasons": ["Range-bound consolidation"], "regime": regime})

    return sorted(signals, key=lambda s: s["score"], reverse=True)


def _signal_to_strategy(signal_type: str, iv_rank: int) -> str:
    """Map signal type + IV rank to strategy code."""
    if signal_type == "REVERSAL_LONG":
        return "BULL_CALL_SPREAD" if iv_rank >= 50 else "LONG_CE"
    if signal_type == "REVERSAL_SHORT":
        return "BEAR_PUT_SPREAD" if iv_rank >= 50 else "LONG_PE"
    if signal_type == "BREAKOUT_LONG":
        return "LONG_CE"
    if signal_type == "BREAKOUT_SHORT":
        return "LONG_PE"
    if signal_type == "TREND_CONTINUATION_LONG":
        return "BULL_PUT_SPREAD" if iv_rank >= 30 else "LONG_CE"
    if signal_type == "TREND_CONTINUATION_SHORT":
        return "BEAR_CALL_SPREAD" if iv_rank >= 30 else "LONG_PE"
    if signal_type == "PREMIUM_SELL_BULLISH":
        return "IRON_CONDOR" if iv_rank >= 30 else "BULL_PUT_SPREAD"
    if signal_type == "PREMIUM_SELL_BEARISH":
        return "IRON_CONDOR" if iv_rank >= 30 else "BEAR_CALL_SPREAD"
    if signal_type == "PREMIUM_SELL_RANGE":
        return "IRON_CONDOR"
    return "NO_TRADE"


def select_strategy_v2(
    df: "pd.DataFrame",
    idx: int,
    iv_rank: int,
) -> Tuple[str, str, str, int]:
    """
    Enhanced strategy selection using the 10-regime detector and signal engine.

    Returns:
      (regime_detail, strategy, signal_type, signal_score)
      regime_detail — one of the 10 detailed regimes
      strategy      — strategy code (may be NO_TRADE)
      signal_type   — triggering signal, or NO_SIGNAL
      signal_score  — 0-100
    """
    regime = detect_regime(df, idx)

    # Non-tradeable regimes
    if regime in ("PANIC_SELL", "INITIALIZING", "NEUTRAL"):
        return regime, "NO_TRADE", "NO_SIGNAL", 0

    signals = scan_signals(df, idx, regime)
    if not signals:
        return regime, "NO_TRADE", "NO_SIGNAL", 0

    best = signals[0]  # already sorted by score desc
    if best["score"] < MIN_SIGNAL_SCORE:
        return regime, "NO_TRADE", "NO_SIGNAL", 0

    strategy = _signal_to_strategy(best["type"], iv_rank)
    return regime, strategy, best["type"], best["score"]


# ── Leg builder ────────────────────────────────────────────────────────────────

def build_legs(spot: float, instrument: str, strategy: str,
               daily_vol: float, remaining_minutes: int) -> List[Dict]:
    """
    Build option legs for *strategy*.
    Returns list of dicts: {act, typ, strike, delta, ep}

    Supports:
      IRON_CONDOR, BULL_PUT_SPREAD, BEAR_CALL_SPREAD  (credit spreads)
      LONG_CE, LONG_PE                                 (directional buys)
      BULL_CALL_SPREAD                                 (debit call spread)
      BEAR_PUT_SPREAD                                  (debit put spread)
    """
    from app.services.simulator import price_option, INSTRUMENT_CONFIG

    tick = INSTRUMENT_CONFIG[instrument]["tick_size"]
    atm  = round(spot / tick) * tick

    def _leg(act, typ, strike, delta):
        return {
            "act":    act,
            "typ":    typ,
            "strike": int(strike),
            "delta":  delta,
            "ep":     price_option(spot, strike, daily_vol, remaining_minutes, typ),
        }

    # ── Credit spreads (premium-selling) ──────────────────────────────────
    if strategy == "IRON_CONDOR":
        return [
            _leg("SELL", "CE", atm + 3 * tick,  0.17),
            _leg("BUY",  "CE", atm + 5 * tick,  0.08),
            _leg("SELL", "PE", atm - 3 * tick, -0.17),
            _leg("BUY",  "PE", atm - 5 * tick, -0.08),
        ]

    if strategy == "BULL_PUT_SPREAD":
        return [
            _leg("SELL", "PE", atm - 2 * tick, -0.28),
            _leg("BUY",  "PE", atm - 4 * tick, -0.12),
        ]

    if strategy == "BEAR_CALL_SPREAD":
        return [
            _leg("SELL", "CE", atm + 2 * tick, 0.28),
            _leg("BUY",  "CE", atm + 4 * tick, 0.12),
        ]

    # ── Directional buys ──────────────────────────────────────────────────
    if strategy == "LONG_CE":
        return [_leg("BUY", "CE", atm, 0.50)]

    if strategy == "LONG_PE":
        return [_leg("BUY", "PE", atm, -0.50)]

    # ── Debit spreads ──────────────────────────────────────────────────────
    if strategy == "BULL_CALL_SPREAD":
        # Long ATM CE + Short ATM+2 CE
        return [
            _leg("BUY",  "CE", atm,             0.50),
            _leg("SELL", "CE", atm + 2 * tick,  0.28),
        ]

    if strategy == "BEAR_PUT_SPREAD":
        # Long ATM PE + Short ATM-2 PE
        return [
            _leg("BUY",  "PE", atm,            -0.50),
            _leg("SELL", "PE", atm - 2 * tick, -0.28),
        ]

    return []
