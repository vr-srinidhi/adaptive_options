"""
Liquidity-aware spread selection for ORB paper trading.

Phase 2 keeps the ORB signal engine intact and upgrades only the spread
construction step after a confirmed bullish or bearish signal.
"""
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.opening_range import (
    STRIKE_STEP,
    generate_bearish_candidates,
    generate_bullish_candidates,
)
from app.services.strategy_config import STRATEGY_CONFIG as _CFG

SELECTION_METHOD = "ranked_candidate_selection_v1"
_MAX_PRICE_STALENESS = max(int(_CFG["max_price_staleness_min"]), 1)


@dataclass
class SpreadSelectionResult:
    selected_candidate: Optional[Dict[str, Any]]
    candidate_ranking_json: Dict[str, Any]
    best_invalid_candidate: Optional[Dict[str, Any]]
    reason_code: str
    reason_text: str
    selected_candidate_rank: Optional[int] = None
    selected_candidate_score: Optional[float] = None
    selected_candidate_score_breakdown: Optional[Dict[str, Any]] = None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _normalize(value: Optional[float], values: List[float], invert: bool = False) -> float:
    clean = [float(v) for v in values if v is not None]
    if value is None or not clean:
        return 0.0
    lo = min(clean)
    hi = max(clean)
    if math.isclose(lo, hi):
        return 1.0 if hi > 0 else 0.0
    score = (float(value) - lo) / (hi - lo)
    if invert:
        score = 1.0 - score
    return max(0.0, min(1.0, score))


def _direction_preference_score(bias: str, long_strike: int, spot_price: float, step: int) -> float:
    strike_steps_from_spot = int(round((long_strike - spot_price) / step))
    if bias == "BULLISH":
        if strike_steps_from_spot == -1:
            return 1.0
        if strike_steps_from_spot == 0:
            return 0.93
        if strike_steps_from_spot == 1:
            return 0.86
    else:
        if strike_steps_from_spot == 0:
            return 1.0
        if strike_steps_from_spot == 1:
            return 0.93
        if strike_steps_from_spot == -1:
            return 0.86
    penalty_steps = min(abs(strike_steps_from_spot), 4)
    return max(0.45, 0.78 - penalty_steps * 0.08)


def _rejection_priority(reason_code: Optional[str]) -> int:
    order = {
        "LOW_LIQUIDITY_REJECT": 0,
        "STALE_OPTION_PRICE": 1,
        "RISK_EXCEEDS_CAP": 2,
        "INSUFFICIENT_TARGET_COVERAGE": 3,
        "NO_HEDGE_AVAILABLE": 4,
    }
    return order.get(reason_code or "", 99)


def _gate_for_reason(reason_code: Optional[str]) -> Optional[str]:
    mapping = {
        "LOW_LIQUIDITY_REJECT": "G5",
        "NO_HEDGE_AVAILABLE": "G5",
        "STALE_OPTION_PRICE": "FRESHNESS",
        "RISK_EXCEEDS_CAP": "G6",
        "INSUFFICIENT_TARGET_COVERAGE": "G7",
    }
    return mapping.get(reason_code or "")


def _summarize_rejections(candidates: List[Dict[str, Any]]) -> Tuple[str, str]:
    rejected = [c for c in candidates if not c["valid"]]
    if not rejected:
        return "NO_VALID_CANDIDATE_AFTER_RANKING", "No valid spread candidate was found."

    priced_rejections = [c for c in rejected if c.get("has_usable_prices")]
    scope = priced_rejections or rejected
    reasons: Dict[str, int] = {}
    for candidate in scope:
        code = candidate.get("rejection_reason") or "NO_VALID_CANDIDATE_AFTER_RANKING"
        reasons[code] = reasons.get(code, 0) + 1

    if not priced_rejections:
        return (
            "NO_HEDGE_AVAILABLE",
            "No candidate spread had two tradable option legs with usable prices.",
        )

    if len(reasons) == 1:
        code = next(iter(reasons))
        texts = {
            "LOW_LIQUIDITY_REJECT": "All candidate spreads failed the liquidity guardrails.",
            "STALE_OPTION_PRICE": "All candidate spreads were rejected because option prices were stale.",
            "RISK_EXCEEDS_CAP": "All candidate spreads breached the session risk cap.",
            "INSUFFICIENT_TARGET_COVERAGE": "All candidate spreads failed target coverage.",
            "NO_HEDGE_AVAILABLE": "No candidate spread had two tradable option legs with usable prices.",
        }
        return code, texts.get(code, "No valid spread candidate was found.")

    summary = ", ".join(
        f"{code}={count}" for code, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
    )
    return (
        "NO_VALID_CANDIDATE_AFTER_RANKING",
        f"Spread selector evaluated candidates but none passed hard constraints ({summary}).",
    )


def _candidate_sort_key(candidate: Dict[str, Any]) -> Tuple:
    score = candidate.get("score")
    liquidity = candidate.get("liquidity_score")
    coverage = candidate.get("target_coverage_ratio")
    distance = candidate.get("absolute_distance_long")
    debit = candidate.get("spread_debit")
    long_strike = candidate.get("long_strike")
    return (
        -(score if score is not None else -1.0),
        -(liquidity if liquidity is not None else -1.0),
        -(coverage if coverage is not None else -1.0),
        distance if distance is not None else float("inf"),
        debit if debit is not None else float("inf"),
        long_strike if long_strike is not None else float("inf"),
    )


def _best_invalid_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    invalid = [c for c in candidates if not c["valid"]]
    if not invalid:
        return None
    ordered = sorted(
        invalid,
        key=lambda c: (
            _rejection_priority(c.get("rejection_reason")),
            c.get("absolute_distance_long") if c.get("absolute_distance_long") is not None else float("inf"),
            c.get("spread_debit") if c.get("spread_debit") is not None else float("inf"),
            c["candidate_universe_rank"],
        ),
    )
    best = dict(ordered[0])
    best["failing_gate"] = _gate_for_reason(best.get("rejection_reason"))
    best["candidate_rank"] = best.get("candidate_rank") or best["candidate_universe_rank"]
    return best


def select_spread_candidate(
    *,
    bias: str,
    reference_strike: int,
    spot_price: float,
    capital: float,
    lot_size: int,
    expiry: str,
    option_market: Dict[Tuple[int, str], Dict[str, Any]],
    step: int = STRIKE_STEP,
) -> SpreadSelectionResult:
    """
    Evaluate, rank, and choose the best debit spread candidate for a confirmed signal.
    """
    opt_type = "CE" if bias == "BULLISH" else "PE"
    risk_cap = capital * _CFG["max_risk_pct"]
    target_profit = capital * _CFG["target_profit_pct"]
    candidate_pairs = (
        generate_bullish_candidates(reference_strike, step=step)
        if bias == "BULLISH"
        else generate_bearish_candidates(reference_strike, step=step)
    )

    candidates: List[Dict[str, Any]] = []
    for universe_rank, (long_strike, short_strike) in enumerate(candidate_pairs, start=1):
        long_leg = option_market.get((long_strike, opt_type), {})
        short_leg = option_market.get((short_strike, opt_type), {})
        long_price = long_leg.get("price")
        short_price = short_leg.get("price")
        has_usable_prices = (
            long_price is not None
            and short_price is not None
            and float(long_price) > 0
            and float(short_price) > 0
        )

        long_volume = int(long_leg.get("volume", 0) or 0)
        short_volume = int(short_leg.get("volume", 0) or 0)
        long_oi = int(long_leg.get("oi", 0) or 0)
        short_oi = int(short_leg.get("oi", 0) or 0)
        long_age = int(long_leg.get("age_min", 0) or 0)
        short_age = int(short_leg.get("age_min", 0) or 0)
        is_backfilled = bool(long_leg.get("is_backfilled")) or bool(short_leg.get("is_backfilled"))

        spread_debit = None
        max_loss_per_lot = None
        approved_lots = 0
        total_max_loss = None
        max_gain_total = None
        target_coverage_ratio = None
        risk_reward_ratio = None
        debit_efficiency = None

        if has_usable_prices:
            spread_debit = float(long_price) - float(short_price)
            if spread_debit > 0:
                max_loss_per_lot = spread_debit * lot_size
                approved_lots = int(math.floor(risk_cap / max_loss_per_lot)) if max_loss_per_lot > 0 else 0
                total_max_loss = approved_lots * max_loss_per_lot if approved_lots > 0 else max_loss_per_lot
                max_gain_unit = abs(short_strike - long_strike) - spread_debit
                max_gain_total = max_gain_unit * lot_size * approved_lots if approved_lots > 0 else 0.0
                target_coverage_ratio = (
                    (max_gain_total / target_profit) if target_profit > 0 and max_gain_total is not None else None
                )
                risk_reward_ratio = (
                    (max_gain_total / total_max_loss)
                    if total_max_loss not in (None, 0)
                    else None
                )
                debit_efficiency = (
                    (max_gain_total / spread_debit) if spread_debit not in (None, 0) else None
                )

        distance_long_from_spot = long_strike - spot_price
        distance_short_from_spot = short_strike - spot_price
        absolute_distance_long = abs(distance_long_from_spot)
        absolute_distance_short = abs(distance_short_from_spot)
        combined_volume = long_volume + short_volume
        combined_oi = long_oi + short_oi
        freshness_penalty = min(1.0, max(long_age, short_age) / _MAX_PRICE_STALENESS)
        if is_backfilled:
            freshness_penalty = min(1.0, freshness_penalty + 0.25)

        rejection_reason = None
        rejection_text = None
        valid = True

        if not has_usable_prices or spread_debit is None or spread_debit <= 0:
            valid = False
            rejection_reason = "NO_HEDGE_AVAILABLE"
            rejection_text = "Candidate does not have two usable option prices with positive net debit."
        elif max(long_age, short_age) > _MAX_PRICE_STALENESS:
            valid = False
            rejection_reason = "STALE_OPTION_PRICE"
            rejection_text = (
                f"Candidate prices exceed freshness threshold "
                f"(long age={long_age}, short age={short_age}, max allowed={_MAX_PRICE_STALENESS})."
            )
        elif long_volume <= 0 or short_volume <= 0 or long_oi <= 0 or short_oi <= 0:
            valid = False
            rejection_reason = "LOW_LIQUIDITY_REJECT"
            rejection_text = "Candidate has zero or missing volume / open interest on at least one leg."
        elif approved_lots <= 0 or total_max_loss is None or total_max_loss > risk_cap:
            valid = False
            rejection_reason = "RISK_EXCEEDS_CAP"
            rejection_text = (
                f"Candidate max loss ₹{_round(total_max_loss, 2) or 0:.2f} exceeds risk cap ₹{risk_cap:.2f}."
            )
        elif max_gain_total is None or max_gain_total < target_profit:
            valid = False
            rejection_reason = "INSUFFICIENT_TARGET_COVERAGE"
            rejection_text = (
                f"Candidate max gain ₹{_round(max_gain_total, 2) or 0:.2f} is below target ₹{target_profit:.2f}."
            )

        candidate: Dict[str, Any] = {
            "direction": bias,
            "bias": bias,
            "option_type": opt_type,
            "opt_type": opt_type,
            "candidate_universe_rank": universe_rank,
            "candidate_rank": None,
            "rank": None,
            "long_strike": long_strike,
            "short_strike": short_strike,
            "spread_width": abs(short_strike - long_strike),
            "long_leg_price": _round(long_price, 2),
            "short_leg_price": _round(short_price, 2),
            "long_price": _round(long_price, 2),
            "short_price": _round(short_price, 2),
            "spread_debit": _round(spread_debit, 2),
            "max_loss_per_lot": _round(max_loss_per_lot, 2),
            "approved_lots": approved_lots,
            "lot_efficiency": approved_lots,
            "lot_size": lot_size,
            "total_max_loss": _round(total_max_loss, 2),
            "max_loss_total": _round(total_max_loss, 2),
            "max_gain_total": _round(max_gain_total, 2),
            "target_profit": _round(target_profit, 2),
            "target_coverage_ratio": _round(target_coverage_ratio, 4),
            "risk_reward_ratio": _round(risk_reward_ratio, 4),
            "debit_efficiency": _round(debit_efficiency, 4),
            "spot_price": _round(spot_price, 2),
            "distance_long_from_spot": _round(distance_long_from_spot, 2),
            "distance_short_from_spot": _round(distance_short_from_spot, 2),
            "absolute_distance_long": _round(absolute_distance_long, 2),
            "absolute_distance_short": _round(absolute_distance_short, 2),
            "long_leg_volume": long_volume,
            "short_leg_volume": short_volume,
            "long_leg_oi": long_oi,
            "short_leg_oi": short_oi,
            "combined_volume": combined_volume,
            "combined_oi": combined_oi,
            "long_leg_age_min": long_age,
            "short_leg_age_min": short_age,
            "is_backfilled": is_backfilled,
            "freshness_penalty": _round(freshness_penalty, 4),
            "execution_quality_score": None,
            "liquidity_score": None,
            "spot_distance_score": None,
            "freshness_score": None,
            "score": None,
            "score_breakdown": None,
            "expiry": expiry,
            "valid": valid,
            "status": "VALID" if valid else "REJECTED",
            "rejection_reason": rejection_reason,
            "rejection_text": rejection_text,
            "has_usable_prices": has_usable_prices,
            "selection_method": SELECTION_METHOD,
            "reference_strike": reference_strike,
        }
        candidates.append(candidate)

    volume_values = [c["combined_volume"] for c in candidates if c["combined_volume"] is not None]
    oi_values = [c["combined_oi"] for c in candidates if c["combined_oi"] is not None]
    distance_values = [c["absolute_distance_long"] for c in candidates if c["absolute_distance_long"] is not None]
    coverage_values = [c["target_coverage_ratio"] for c in candidates if c["target_coverage_ratio"] is not None]
    rr_values = [c["risk_reward_ratio"] for c in candidates if c["risk_reward_ratio"] is not None]
    lot_values = [c["approved_lots"] for c in candidates if c["approved_lots"] is not None]

    for candidate in candidates:
        volume_score = _normalize(candidate["combined_volume"], volume_values)
        oi_score = _normalize(candidate["combined_oi"], oi_values)
        volume_balance = (
            min(candidate["long_leg_volume"], candidate["short_leg_volume"])
            / max(candidate["long_leg_volume"], candidate["short_leg_volume"])
            if max(candidate["long_leg_volume"], candidate["short_leg_volume"]) > 0
            else 0.0
        )
        oi_balance = (
            min(candidate["long_leg_oi"], candidate["short_leg_oi"])
            / max(candidate["long_leg_oi"], candidate["short_leg_oi"])
            if max(candidate["long_leg_oi"], candidate["short_leg_oi"]) > 0
            else 0.0
        )
        balance_score = (volume_balance * 0.5) + (oi_balance * 0.5)
        liquidity_score = ((volume_score * 0.6) + (oi_score * 0.4)) * balance_score
        freshness_score = max(0.0, 1.0 - float(candidate["freshness_penalty"]))
        coverage_score = _normalize(candidate["target_coverage_ratio"], coverage_values)
        rr_score = _normalize(candidate["risk_reward_ratio"], rr_values)
        lot_score = _normalize(candidate["approved_lots"], lot_values)
        closeness_score = _normalize(candidate["absolute_distance_long"], distance_values, invert=True)
        direction_score = _direction_preference_score(
            bias,
            candidate["long_strike"],
            float(candidate["spot_price"]),
            step,
        )
        spot_distance_score = (closeness_score * 0.85) + (direction_score * 0.15)
        execution_quality_score = (liquidity_score * 0.7) + (freshness_score * 0.3)

        candidate["liquidity_score"] = _round(liquidity_score, 4)
        candidate["freshness_score"] = _round(freshness_score, 4)
        candidate["spot_distance_score"] = _round(spot_distance_score, 4)
        candidate["execution_quality_score"] = _round(execution_quality_score, 4)

        if candidate["valid"]:
            total_score = (
                (coverage_score * 0.30)
                + (liquidity_score * 0.20)
                + (rr_score * 0.20)
                + (lot_score * 0.10)
                + (spot_distance_score * 0.15)
                + (freshness_score * 0.05)
            )
            breakdown = {
                "target_coverage_score": _round(coverage_score, 4),
                "liquidity_score": _round(liquidity_score, 4),
                "risk_reward_score": _round(rr_score, 4),
                "lot_score": _round(lot_score, 4),
                "spot_distance_score": _round(spot_distance_score, 4),
                "freshness_score": _round(freshness_score, 4),
                "direction_preference_score": _round(direction_score, 4),
                "execution_quality_score": _round(execution_quality_score, 4),
                "weights": {
                    "target_coverage": 0.30,
                    "liquidity": 0.20,
                    "risk_reward": 0.20,
                    "lots": 0.10,
                    "spot_distance": 0.15,
                    "freshness": 0.05,
                },
            }
            candidate["score"] = _round(total_score, 4)
            candidate["score_breakdown"] = breakdown
        else:
            candidate["score_breakdown"] = {
                "liquidity_score": _round(liquidity_score, 4),
                "spot_distance_score": _round(spot_distance_score, 4),
                "freshness_score": _round(freshness_score, 4),
            }
            candidate["failing_gate"] = _gate_for_reason(candidate.get("rejection_reason"))

    valid_candidates = sorted([c for c in candidates if c["valid"]], key=_candidate_sort_key)
    for rank, candidate in enumerate(valid_candidates, start=1):
        candidate["rank"] = rank
        candidate["candidate_rank"] = rank
        candidate["status"] = "SELECTED" if rank == 1 else "REJECTED_VALID_CANDIDATE"

    selected_candidate = valid_candidates[0] if valid_candidates else None
    best_invalid = _best_invalid_candidate(candidates)

    reason_code, reason_text = _summarize_rejections(candidates) if not selected_candidate else (
        "ENTER_TRADE",
        (
            f"Ranked spread selection chose {selected_candidate['long_strike']}{opt_type}/"
            f"{selected_candidate['short_strike']}{opt_type} with score "
            f"{selected_candidate['score']:.4f} from {len(valid_candidates)} valid candidates."
        ),
    )

    ranking_json = {
        "selection_method": SELECTION_METHOD,
        "signal_direction": bias,
        "option_type": opt_type,
        "reference_strike": reference_strike,
        "spot_price": _round(spot_price, 2),
        "evaluated_candidates": len(candidates),
        "valid_candidates": len(valid_candidates),
        "selected_candidate_rank": selected_candidate["rank"] if selected_candidate else None,
        "selected_candidate_score": selected_candidate["score"] if selected_candidate else None,
        "selected_candidate_score_breakdown": (
            selected_candidate["score_breakdown"] if selected_candidate else None
        ),
        "candidates": sorted(
            [dict(candidate) for candidate in candidates],
            key=lambda c: (
                c["rank"] is None,
                c["rank"] if c["rank"] is not None else 999,
                c["candidate_universe_rank"],
            ),
        ),
    }

    return SpreadSelectionResult(
        selected_candidate=dict(selected_candidate) if selected_candidate else None,
        candidate_ranking_json=ranking_json,
        best_invalid_candidate=best_invalid,
        reason_code=reason_code,
        reason_text=reason_text,
        selected_candidate_rank=selected_candidate["rank"] if selected_candidate else None,
        selected_candidate_score=selected_candidate["score"] if selected_candidate else None,
        selected_candidate_score_breakdown=(
            dict(selected_candidate["score_breakdown"]) if selected_candidate else None
        ),
    )
