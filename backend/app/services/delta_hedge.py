from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

RISK_FREE_RATE = 0.065
DEFAULT_IV = 0.12
MIN_T_YEARS = 1 / (365 * 24 * 60)
MIN_SIGMA = 0.01
MAX_SIGMA = 3.0


@dataclass(frozen=True)
class DeltaHedgeSettings:
    enabled: bool = False
    delta_threshold: float = 150.0
    hedge_action: str = "BUY_WING"
    hedge_qty_mode: str = "PARTIAL"
    max_hedge_triggers: int = 3
    reentry_buffer: float = 50.0
    default_iv: float = DEFAULT_IV
    cooldown_minutes: int = 5


def parse_delta_hedge_settings(params: dict) -> DeltaHedgeSettings:
    nested = params.get("deltaHedge") or params.get("delta_hedge") or {}

    def get(key: str, nested_key: str, default):
        if key in params:
            return params[key]
        if nested_key in nested:
            return nested[nested_key]
        return default

    enabled = bool(get("delta_hedge_enabled", "enabled", False))
    return DeltaHedgeSettings(
        enabled=enabled,
        delta_threshold=float(get("delta_threshold", "deltaThreshold", 150) or 150),
        hedge_action=str(get("hedge_action", "hedgeAction", "BUY_WING") or "BUY_WING").upper(),
        hedge_qty_mode=str(get("hedge_qty_mode", "hedgeQtyMode", "PARTIAL") or "PARTIAL").upper(),
        max_hedge_triggers=max(0, int(get("max_hedge_triggers", "maxHedgeTriggers", 3) or 3)),
        reentry_buffer=max(0.0, float(get("reentry_buffer", "reentryBuffer", 50) or 50)),
        default_iv=_normalize_iv(float(get("default_iv", "defaultIV", DEFAULT_IV) or DEFAULT_IV)),
        cooldown_minutes=max(0, int(get("delta_hedge_cooldown_minutes", "cooldownMinutes", 5) or 5)),
    )


def _normalize_iv(iv: float) -> float:
    if iv > 3:
        iv = iv / 100
    return min(MAX_SIGMA, max(MIN_SIGMA, iv))


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def years_to_expiry(now: datetime, expiry_date: date, expiry_time: time = time(15, 30)) -> float:
    expiry_dt = datetime.combine(expiry_date, expiry_time)
    seconds = (expiry_dt - now.replace(tzinfo=None)).total_seconds()
    return max(MIN_T_YEARS, seconds / (365 * 24 * 60 * 60))


def black_scholes_price(
    spot: float,
    strike: float,
    years: float,
    sigma: float,
    option_type: str,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    years = max(MIN_T_YEARS, years)
    sigma = _normalize_iv(sigma)
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    discounted_strike = strike * math.exp(-risk_free_rate * years)
    if option_type.upper() == "CE":
        return spot * norm_cdf(d1) - discounted_strike * norm_cdf(d2)
    return discounted_strike * norm_cdf(-d2) - spot * norm_cdf(-d1)


def black_scholes_delta(
    spot: float,
    strike: float,
    years: float,
    sigma: float,
    option_type: str,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    years = max(MIN_T_YEARS, years)
    sigma = _normalize_iv(sigma)
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    if option_type.upper() == "CE":
        return norm_cdf(d1)
    return norm_cdf(d1) - 1.0


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    years: float,
    option_type: str,
    risk_free_rate: float = RISK_FREE_RATE,
    initial_sigma: float = DEFAULT_IV,
) -> Optional[float]:
    if price <= 0 or spot <= 0 or strike <= 0:
        return None

    intrinsic = max(spot - strike, 0.0) if option_type.upper() == "CE" else max(strike - spot, 0.0)
    if price < intrinsic * 0.98:
        return None

    sigma = _normalize_iv(initial_sigma)
    years = max(MIN_T_YEARS, years)
    sqrt_t = math.sqrt(years)
    for _ in range(20):
        model_price = black_scholes_price(spot, strike, years, sigma, option_type, risk_free_rate)
        d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
        vega = spot * norm_pdf(d1) * sqrt_t
        diff = model_price - price
        if abs(diff) < 0.01:
            return _normalize_iv(sigma)
        if vega < 1e-6:
            break
        sigma -= diff / vega
        if sigma < MIN_SIGMA or sigma > MAX_SIGMA:
            break
    return None


def volatility_for_delta(
    price: Optional[float],
    spot: Optional[float],
    strike: float,
    years: float,
    option_type: str,
    vix: Optional[float],
    default_iv: float = DEFAULT_IV,
) -> float:
    if price is not None and spot is not None:
        guess = _normalize_iv(vix / 100) if vix else default_iv
        iv = implied_volatility(price, spot, strike, years, option_type, initial_sigma=guess)
        if iv is not None:
            return iv
    if vix:
        return _normalize_iv(vix / 100)
    return _normalize_iv(default_iv)


def signed_position_delta(
    *,
    side: str,
    option_type: str,
    price: Optional[float],
    spot: Optional[float],
    strike: float,
    quantity: int,
    timestamp: datetime,
    expiry_date: date,
    vix: Optional[float],
    default_iv: float = DEFAULT_IV,
) -> Optional[float]:
    if price is None or spot is None or quantity <= 0:
        return None
    years = years_to_expiry(timestamp, expiry_date)
    sigma = volatility_for_delta(price, spot, strike, years, option_type, vix, default_iv)
    delta = black_scholes_delta(spot, strike, years, sigma, option_type)
    multiplier = -1 if side.upper() == "SELL" else 1
    return multiplier * delta * quantity


# Below this per-unit delta the hedge instrument barely moves the book, so the
# lots required explode. Treat as un-hedgeable rather than dividing by ~zero.
MIN_HEDGE_OPTION_DELTA = 0.01


def is_hedgeable_delta(option_delta: Optional[float]) -> bool:
    """Whether an instrument moves the book enough to hedge with at all.

    A zero-lot sizing result has two very different causes, and callers must be
    able to tell them apart: the wing is nearly delta-less (this returns False,
    and hedging would *under*shoot by a mile), or the imbalance genuinely fits
    in less than one lot (this returns True). Note the latter is unreachable
    whenever `delta_threshold >= lot_size`, since |option_delta| <= 1 means
    lots >= floor(threshold / lot_size) >= 1.
    """
    return option_delta is not None and abs(option_delta) >= MIN_HEDGE_OPTION_DELTA


def unit_delta_for_option(
    *,
    option_type: str,
    price: Optional[float],
    spot: Optional[float],
    strike: float,
    timestamp: datetime,
    expiry_date: date,
    vix: Optional[float],
    default_iv: float = DEFAULT_IV,
) -> Optional[float]:
    """Signed per-unit delta of a single option, independent of position size."""
    if price is None or spot is None:
        return None
    years = years_to_expiry(timestamp, expiry_date)
    sigma = volatility_for_delta(price, spot, strike, years, option_type, vix, default_iv)
    return black_scholes_delta(spot, strike, years, sigma, option_type)


def hedge_lots_for_delta(
    *,
    net_delta: float,
    option_delta: Optional[float],
    lot_size: int,
    max_lots: int,
    mode: str = "PARTIAL",
) -> int:
    """Lots of the hedge option needed to pull net delta toward zero.

    FULL preserves the historical behaviour of hedging with the whole position
    size. PARTIAL sizes to the actual imbalance and *rounds down*, so the
    residual delta always keeps the sign it started with — the hedge can never
    overshoot through zero and invert the book. Returns 0 when the gap is too
    small for even one lot; callers should then skip the hedge without
    consuming a trigger.
    """
    max_lots = max(0, int(max_lots))
    if max_lots == 0 or lot_size <= 0:
        return 0

    if str(mode).upper() == "FULL":
        return max_lots

    if not is_hedgeable_delta(option_delta):
        return 0

    delta_per_lot = abs(option_delta) * lot_size
    if delta_per_lot <= 0:
        return 0

    return max(0, min(max_lots, math.floor(abs(net_delta) / delta_per_lot)))
