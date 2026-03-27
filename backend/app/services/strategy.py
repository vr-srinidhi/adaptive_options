"""
Strategy selection and leg builder per PRD Section 7.3 & 7.4.
"""
from typing import Dict, List, Tuple


# ── Regime / Strategy selection (PRD §7.3) ────────────────────────────────────
def select_strategy(ema5: float, ema20: float, rsi: float, iv_rank: int) -> Tuple[str, str]:
    """
    Returns (regime, strategy_code).
    regime  : BULLISH | BEARISH | NEUTRAL
    strategy: IRON_CONDOR | BULL_PUT_SPREAD | BEAR_CALL_SPREAD | NO_TRADE
    """
    # Classify regime direction
    ema_diff_pct = (ema5 - ema20) / ema20 * 100.0 if ema20 != 0 else 0.0

    if ema5 > ema20 and ema_diff_pct >= 0.15:
        regime = "BULLISH"
    elif ema5 < ema20 and abs(ema_diff_pct) >= 0.15:
        regime = "BEARISH"
    else:
        regime = "NEUTRAL"

    # Overbought / oversold → no trade regardless
    if rsi > 70 or rsi < 30:
        return regime, "NO_TRADE"

    if regime == "BULLISH":
        if 40 <= rsi <= 70:
            strategy = "IRON_CONDOR" if iv_rank >= 30 else "BULL_PUT_SPREAD"
        else:
            strategy = "NO_TRADE"

    elif regime == "BEARISH":
        if 30 <= rsi <= 60:
            strategy = "IRON_CONDOR" if iv_rank >= 30 else "BEAR_CALL_SPREAD"
        else:
            strategy = "NO_TRADE"

    else:  # NEUTRAL
        if 40 <= rsi <= 60:
            strategy = "IRON_CONDOR" if iv_rank >= 30 else "NO_TRADE"
        else:
            strategy = "NO_TRADE"

    return regime, strategy


# ── Leg builder (PRD §7.4) ────────────────────────────────────────────────────
def build_legs(spot: float, instrument: str, strategy: str,
               daily_vol: float, remaining_minutes: int) -> List[Dict]:
    """
    Build option legs for the given strategy.
    Returns list of dicts: {act, typ, strike, delta, ep}
    """
    from app.services.simulator import price_option, INSTRUMENT_CONFIG

    tick = INSTRUMENT_CONFIG[instrument]["tick_size"]
    atm = round(spot / tick) * tick

    def _leg(act, typ, strike, delta):
        return {
            "act": act,
            "typ": typ,
            "strike": int(strike),
            "delta": delta,
            "ep": price_option(spot, strike, daily_vol, remaining_minutes, typ),
        }

    if strategy == "IRON_CONDOR":
        # Short call ATM+3, Long call ATM+5
        # Short put  ATM-3, Long put  ATM-5
        return [
            _leg("SELL", "CE", atm + 3 * tick,  0.17),
            _leg("BUY",  "CE", atm + 5 * tick,  0.08),
            _leg("SELL", "PE", atm - 3 * tick, -0.17),
            _leg("BUY",  "PE", atm - 5 * tick, -0.08),
        ]

    if strategy == "BULL_PUT_SPREAD":
        # Short put ATM-2, Long put ATM-4
        return [
            _leg("SELL", "PE", atm - 2 * tick, -0.28),
            _leg("BUY",  "PE", atm - 4 * tick, -0.12),
        ]

    if strategy == "BEAR_CALL_SPREAD":
        # Short call ATM+2, Long call ATM+4
        return [
            _leg("SELL", "CE", atm + 2 * tick, 0.28),
            _leg("BUY",  "CE", atm + 4 * tick, 0.12),
        ]

    return []
