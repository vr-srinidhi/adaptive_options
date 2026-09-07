"""
Short Straddle — Adjustment executor  (straddle_adjustment_v1)

Strategy:
  Enter as Short Straddle (SELL ATM CE + SELL ATM PE).
  Supports two mid-session lock triggers:
    - Profit lock (`lock_trigger`): when net_mtm >= lock_trigger, buy OTM wings
      to convert to Iron Condor and protect the gain.
    - Loss lock (`loss_lock_trigger`): when net_mtm <= -loss_lock_trigger, buy OTM
      wings to cap further downside — emergency defensive hedge.
  Only one lock can fire per session (whichever threshold is crossed first).
  Trailing stop, stop-loss, and time exit apply throughout.

Backward-compatible: if loss_lock_trigger is 0 or absent, behaviour is identical
to the original profit-lock-only strategy.

validate_run is reused from generic_executor (validates the 2 straddle legs).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import date as date_type, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy_run import (
    StrategyLegMtm,
    StrategyRun,
    StrategyRunEvent,
    StrategyRunLeg,
    StrategyRunMtm,
)
from app.services.charges_service import (
    compute_leg_entry_charges,
    compute_leg_exit_charges_estimate,
    compute_leg_total_charges,
)
from app.services.contract_spec_service import (
    get_contract_spec,
    resolve_atm_strike,
    resolve_expiry,
    resolve_leg_strikes,
)
from app.services.delta_hedge import (
    wing_lots_after_hedges,
    hedge_lots_for_delta,
    is_hedgeable_delta,
    parse_delta_hedge_settings,
    signed_position_delta,
    unit_delta_for_option,
)
from app.services.entry_rule_registry import get_entry_rule
from app.services.generic_executor import (
    ExecutionResult,
    ValidationResult,
    _get_price,
    _parse_time,
    validate_run,
)
from app.services.historical_market_data import (
    load_option_candles_for_strikes,
    load_spot_candles,
    load_vix_candles,
    vix_at_time,
)

log = logging.getLogger(__name__)

_SESSION_START    = time(9, 15)
_SESSION_END      = time(15, 30)
_MAX_STALE_MINUTES = 1


# Delta hedging is scoped to this strategy only; the executor is shared.
_DELTA_HEDGE_STRATEGY_ID = "short_straddle_dual_lock"


def _hedge_lots(hedge: Dict[str, Any], default_lots: int) -> int:
    """Lots for one hedge leg. Legacy rows without a size are full-size."""
    return int(hedge.get("lots") or default_lots)


def _wing_lots(wing_lots, idx: int, default_lots: int) -> int:
    """Lots for one wing leg; full size when no per-wing count was recorded."""
    if not wing_lots or idx >= len(wing_lots) or wing_lots[idx] is None:
        return default_lots
    return int(wing_lots[idx])


def _wing_exit_charges(
    wing_legs, wing_cur, wing_lots, lot_size: int, default_lots: int
) -> float:
    """Exit-charge estimate for the lock wings, each at its own netted size.

    Summed per leg because netting can leave the two wings at different sizes,
    or one at zero. Kept in one place deliberately: this was previously written
    out at each call site, and a post-hedge refresh that recomputed both wings
    at the full position size silently undid the netting for that tick.
    """
    total = 0.0
    for i, leg in enumerate(wing_legs):
        lots_i = _wing_lots(wing_lots, i, default_lots)
        price = wing_cur[i] if i < len(wing_cur) else None
        if lots_i <= 0 or price is None:
            continue
        total += compute_leg_exit_charges_estimate(lots_i, lot_size, [leg], [price])
    return total


def _hedge_gross(hedges: List[Dict[str, Any]], lot_size: int, default_lots: int) -> float:
    """Mark-to-market of the long hedge legs, each at its own size."""
    return sum(
        (h["last_price"] - h["entry_price"]) * lot_size * _hedge_lots(h, default_lots)
        for h in hedges
        if h.get("entry_price") is not None and h.get("last_price") is not None
    )


def _hedge_exit_charges(hedges: List[Dict[str, Any]], lot_size: int, default_lots: int) -> float:
    """Exit-charge estimate for hedge legs, summed per leg so each uses its own
    lot count. The charge formula is additive across legs, so this equals a
    single combined call whenever the sizes match."""
    total = 0.0
    for h in hedges:
        price = h.get("last_price")
        if price is None:
            continue
        total += compute_leg_exit_charges_estimate(
            _hedge_lots(h, default_lots),
            lot_size,
            [(h["side"], h["option_type"], h["strike"])],
            [price],
        )
    return total


async def execute_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    strategy: Dict[str, Any],
    config: Dict[str, Any],
    validation: ValidationResult,
    user_id: Optional[uuid.UUID] = None,
) -> ExecutionResult:
    instrument    = validation.instrument
    trade_date    = date_type.fromisoformat(validation.trade_date)
    entry_time    = _parse_time(validation.entry_time)
    expiry        = date_type.fromisoformat(validation.resolved_expiry)
    atm_strike    = validation.atm_strike
    lot_size      = validation.lot_size
    approved_lots = validation.approved_lots
    warnings: List[str] = list(validation.warnings)

    exit_rule         = strategy.get("exit_rule", {})
    stop_capital_pct  = float(config.get("stop_capital_pct")   or exit_rule.get("stop_capital_pct")   or 0.015)
    trail_trigger     = float(config.get("trail_trigger")       or exit_rule.get("trail_trigger")      or 0)
    trail_pct         = float(config.get("trail_pct")           or exit_rule.get("trail_pct")          or 0)
    lock_trigger      = float(config.get("lock_trigger")        or exit_rule.get("lock_trigger")       or 20_000)
    loss_lock_trigger = float(config.get("loss_lock_trigger")   or exit_rule.get("loss_lock_trigger")  or 0)
    wing_steps        = int(config.get("wing_width_steps")      or exit_rule.get("wing_width_steps")   or 2)
    sq_time           = _parse_time(exit_rule.get("time_exit", "15:25"), default=time(15, 25))
    capital_amount    = float(config.get("capital", 0))
    delta_settings    = parse_delta_hedge_settings(config)

    # Delta hedging is only offered on short_straddle_dual_lock. This executor is
    # shared with short_straddle_profit_lock, which never exposes the feature, so
    # pin it off there rather than relying on config discipline.
    if strategy.get("id") != _DELTA_HEDGE_STRATEGY_ID and delta_settings.enabled:
        delta_settings = replace(delta_settings, enabled=False)
        # Surface it the way live does, so a misconfigured replay doesn't read
        # as a normal unhedged result.
        warnings.append(
            f"Delta hedge requested but only supported on {_DELTA_HEDGE_STRATEGY_ID}; disabled for this run"
        )
        log.warning("Delta hedge requested for %s but only supported on %s — disabled",
                    strategy.get("id"), _DELTA_HEDGE_STRATEGY_ID)

    entry_rule = get_entry_rule(strategy.get("entry_rule_id", "timed_entry"))

    spec         = await get_contract_spec(db, instrument, trade_date)
    strike_step  = spec.strike_step
    straddle_legs: List[Tuple[str, str, int]] = [
        ("SELL", "CE", atm_strike),
        ("SELL", "PE", atm_strike),
    ]
    wing_ce_strike = atm_strike + wing_steps * strike_step
    wing_pe_strike = atm_strike - wing_steps * strike_step
    wing_legs: List[Tuple[str, str, int]] = [
        ("BUY", "CE", wing_ce_strike),
        ("BUY", "PE", wing_pe_strike),
    ]

    # Pre-load all 4 strikes so wing prices are available when the lock fires
    all_strike_keys = {
        (atm_strike, "CE"), (atm_strike, "PE"),
        (wing_ce_strike, "CE"), (wing_pe_strike, "PE"),
    }
    option_index, _ = await load_option_candles_for_strikes(
        db, instrument, trade_date, expiry, all_strike_keys,
        option_price_source="close",
    )

    spot_candles = await load_spot_candles(db, instrument, trade_date)
    vix_candles  = await load_vix_candles(db, trade_date)

    if not spot_candles:
        return ExecutionResult(run_id=str(run_id), status="no_trade", exit_reason="NO_SPOT_DATA", realized_net_pnl=None)

    session_start_dt = datetime.combine(trade_date, _SESSION_START)

    # Straddle leg state (2 legs, indices 0/1)
    trade_open            = False
    straddle_entry_prices: List[Optional[float]] = [None, None]
    straddle_last_prices:  List[Optional[float]] = [None, None]
    straddle_stale:        List[int]             = [0, 0]

    # Wing state (added mid-session on either lock)
    wings_locked         = False
    wing_lots            = None
    lock_reason:          Optional[str]         = None   # "profit" | "loss"
    wing_entry_prices:    List[Optional[float]] = [None, None]
    wing_last_prices:     List[Optional[float]] = [None, None]
    wing_stale:           List[int]             = [0, 0]
    wing_lock_ts:         Optional[datetime]    = None
    wing_entry_charges    = 0.0
    delta_hedges:         List[Dict[str, Any]]  = []
    delta_hedge_charges   = 0.0
    delta_hedge_status    = "off" if not delta_settings.enabled else "monitoring"
    delta_hedge_count     = 0
    delta_reentry_armed   = True
    undersized_logged     = False   # gap < 1 lot
    low_delta_logged      = False   # wing has ~no delta
    wing_unavailable_logged = False
    last_delta_hedge_ts:  Optional[datetime] = None
    last_net_delta:       Optional[float] = None

    entry_credit_per_unit = 0.0
    entry_credit_total    = 0.0
    entry_charges         = 0.0
    actual_entry_ts:      Optional[datetime] = None
    exit_reason:          Optional[str]      = None
    exit_ts:              Optional[datetime] = None

    # Trailing stop
    trail_active        = False
    trail_peak          = 0.0
    trail_stop_at_exit: Optional[float] = None

    mtm_rows:     List[Dict] = []
    leg_mtm_rows: List[Dict] = []
    event_rows:   List[Dict] = []

    straddle_leg_ids = [uuid.uuid4(), uuid.uuid4()]
    wing_leg_ids     = [uuid.uuid4(), uuid.uuid4()]

    def _compute_net_delta(
        ts: datetime,
        spot_close: Optional[float],
        vix_close: Optional[float],
        straddle_cur: List[Optional[float]],
        wing_cur: List[Optional[float]],
    ) -> Optional[float]:
        if not delta_settings.enabled:
            return None

        total = 0.0
        qty = lot_size * approved_lots
        # (side, option_type, strike, price, quantity) — hedge legs carry their
        # own size, which may be smaller than the straddle.
        legs_for_delta = [
            ("SELL", "CE", atm_strike, straddle_cur[0], qty),
            ("SELL", "PE", atm_strike, straddle_cur[1], qty),
        ]
        if wings_locked:
            # Netted wings carry their own size; using the full straddle
            # quantity would model protection that was never bought and skew
            # every subsequent hedge decision.
            #
            # A fully netted wing is skipped rather than passed at zero:
            # signed_position_delta() returns None for quantity <= 0 and the
            # loop below bails on the first None, which would silence net delta
            # for the rest of the session and stop all later hedging.
            for _i, (_ot, _strike) in enumerate(
                (("CE", wing_ce_strike), ("PE", wing_pe_strike))
            ):
                _wl = _wing_lots(wing_lots, _i, approved_lots)
                if _wl > 0:
                    legs_for_delta.append(
                        ("BUY", _ot, _strike, wing_cur[_i], lot_size * _wl)
                    )
        legs_for_delta.extend(
            (h["side"], h["option_type"], h["strike"], h["last_price"],
             lot_size * _hedge_lots(h, approved_lots))
            for h in delta_hedges
        )

        for side, opt_type, strike, price, leg_qty in legs_for_delta:
            leg_delta = signed_position_delta(
                side=side,
                option_type=opt_type,
                price=price,
                spot=spot_close,
                strike=strike,
                quantity=leg_qty,
                timestamp=ts,
                expiry_date=expiry,
                vix=vix_close,
                default_iv=delta_settings.default_iv,
            )
            if leg_delta is None:
                return None
            total += leg_delta
        return round(total, 4)

    for candle in spot_candles:
        ts: datetime = candle["date"]
        t = ts.time().replace(second=0, microsecond=0)
        if t < _SESSION_START or t >= _SESSION_END:
            continue

        minute_idx = int((ts - session_start_dt).total_seconds() / 60)
        spot_close = candle["close"]
        vix_close  = vix_at_time(vix_candles, ts)

        # Fetch current prices for all 4 strikes
        s_ce_price, straddle_stale[0] = _get_price(option_index, (atm_strike, "CE"),    minute_idx, straddle_last_prices[0], straddle_stale[0])
        s_pe_price, straddle_stale[1] = _get_price(option_index, (atm_strike, "PE"),    minute_idx, straddle_last_prices[1], straddle_stale[1])
        w_ce_price, wing_stale[0]     = _get_price(option_index, (wing_ce_strike, "CE"), minute_idx, wing_last_prices[0],     wing_stale[0])
        w_pe_price, wing_stale[1]     = _get_price(option_index, (wing_pe_strike, "PE"), minute_idx, wing_last_prices[1],     wing_stale[1])

        if s_ce_price is not None: straddle_last_prices[0] = s_ce_price
        if s_pe_price is not None: straddle_last_prices[1] = s_pe_price
        if w_ce_price is not None: wing_last_prices[0] = w_ce_price
        if w_pe_price is not None: wing_last_prices[1] = w_pe_price
        for hedge in delta_hedges:
            hedge_price = w_ce_price if hedge["option_type"] == "CE" else w_pe_price
            if hedge_price is not None:
                hedge["last_price"] = hedge_price
                hedge["stale"] = 0
            else:
                hedge["stale"] += 1

        straddle_cur = [s_ce_price, s_pe_price]

        # ── Entry ─────────────────────────────────────────────────────────────
        if not trade_open:
            signal = entry_rule.evaluate(ts, config, trade_open=False)
            if signal.action == "ENTER":
                if any(p is None for p in straddle_cur):
                    event_rows.append({"run_id": run_id, "timestamp": ts, "event_type": "HOLD", "reason_code": "MISSING_LEG_PRICE"})
                    continue
                trade_open = True
                actual_entry_ts = ts
                straddle_entry_prices = list(straddle_cur)
                entry_credit_per_unit = (straddle_entry_prices[0] or 0) + (straddle_entry_prices[1] or 0)
                entry_credit_total    = entry_credit_per_unit * lot_size * approved_lots
                entry_charges         = compute_leg_entry_charges(approved_lots, lot_size, straddle_legs, straddle_entry_prices)
                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "ENTRY", "reason_code": "ENTRY_SCHEDULED",
                    "reason_text": f"Straddle entered at {ts.strftime('%H:%M')}",
                    "payload_json": {"spot": spot_close, "legs": [
                        {"side": "SELL", "option_type": "CE", "strike": atm_strike, "price": straddle_entry_prices[0]},
                        {"side": "SELL", "option_type": "PE", "strike": atm_strike, "price": straddle_entry_prices[1]},
                    ]},
                })
            else:
                event_rows.append({"run_id": run_id, "timestamp": ts, "event_type": "HOLD", "reason_code": signal.reason_code})
            continue

        # ── Trade open: data gap check ────────────────────────────────────────
        data_gap = (
            any(straddle_stale[i] > _MAX_STALE_MINUTES for i in range(2))
            or any(p is None for p in straddle_cur)
        )
        if data_gap and exit_rule.get("data_gap_exit", True):
            exit_reason = "DATA_GAP_EXIT"
            exit_ts = ts
            event_rows.append({"run_id": run_id, "timestamp": ts, "event_type": "DATA_GAP_EXIT", "reason_code": "DATA_GAP_EXIT"})
            break

        # ── MTM calculation ───────────────────────────────────────────────────
        straddle_gross = sum(
            (ep - cp)
            for ep, cp in zip(straddle_entry_prices, straddle_cur)
            if ep is not None and cp is not None
        ) * lot_size * approved_lots

        wing_gross = 0.0
        wing_cur = [w_ce_price, w_pe_price]
        if wings_locked:
            wing_gross = sum(
                # BUY leg: profit = current - entry
                (cp - ep) * lot_size * _wing_lots(wing_lots, i, approved_lots)
                for i, (ep, cp) in enumerate(zip(wing_entry_prices, wing_cur))
                if ep is not None and cp is not None
            )

        delta_hedge_gross = _hedge_gross(delta_hedges, lot_size, approved_lots)

        gross_mtm_total = straddle_gross + wing_gross + delta_hedge_gross

        # Estimate exit charges for currently active legs. Hedge legs are priced
        # separately because they carry their own lot count.
        est_exit_charges = (
            compute_leg_exit_charges_estimate(
                approved_lots, lot_size, list(straddle_legs), list(straddle_cur)
            )
            + _hedge_exit_charges(delta_hedges, lot_size, approved_lots)
        )
        if wings_locked:
            est_exit_charges += _wing_exit_charges(
                wing_legs, [w_ce_price, w_pe_price], wing_lots, lot_size, approved_lots
            )

        net_mtm = gross_mtm_total - entry_charges - wing_entry_charges - delta_hedge_charges - est_exit_charges

        net_delta = _compute_net_delta(ts, spot_close, vix_close, straddle_cur, wing_cur)
        last_net_delta = net_delta

        # ── Hard stop is evaluated BEFORE the hedge so we never buy protection
        # on a minute the position is already stopping out. Mirrors the live
        # engine's `fired is None` guard; the authoritative exit decision is
        # still made further down, from this same threshold. ─────────────────
        stop_threshold = -(capital_amount * stop_capital_pct) if stop_capital_pct > 0 else -(entry_credit_total * 1.5)
        stop_already_fired = net_mtm <= stop_threshold

        # ── Delta hedge: opt-in and isolated from existing lock rules ────────
        if not stop_already_fired and delta_settings.enabled and net_delta is not None:
            threshold = delta_settings.delta_threshold
            safe_level = max(0.0, threshold - delta_settings.reentry_buffer)
            abs_delta = abs(net_delta)

            if abs_delta <= safe_level:
                # A new episode starts here. These resets must NOT sit inside the
                # `not delta_reentry_armed` branch — a skip deliberately leaves the
                # hedge armed, so that branch never opens after a skip-only episode
                # and the once-per-episode notes degrade to once-per-session.
                undersized_logged = False
                low_delta_logged = False
                wing_unavailable_logged = False
            if not delta_reentry_armed and abs_delta <= safe_level:
                delta_reentry_armed = True
                delta_hedge_status = "monitoring"
                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "DELTA_HEDGE", "reason_code": "DELTA_NEUTRAL_RESTORED",
                    "reason_text": f"Net delta normalized to {net_delta:.2f}",
                    "payload_json": {"net_delta": net_delta, "threshold": threshold, "safe_level": safe_level},
                })

            if delta_hedge_count >= delta_settings.max_hedge_triggers and delta_hedge_status != "exhausted":
                delta_hedge_status = "exhausted"
                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "DELTA_HEDGE", "reason_code": "DELTA_HEDGE_EXHAUSTED",
                    "reason_text": "Delta hedge max trigger count reached; existing loss controls remain active.",
                    "payload_json": {"net_delta": net_delta, "max_triggers": delta_settings.max_hedge_triggers},
                })

            in_delta_window = time(9, 20) <= t <= time(15, 20)
            cooldown_ok = (
                last_delta_hedge_ts is None
                or ts - last_delta_hedge_ts >= timedelta(minutes=delta_settings.cooldown_minutes)
            )
            can_trigger = (
                delta_hedge_status != "exhausted"
                and delta_reentry_armed
                and in_delta_window
                and cooldown_ok
                and delta_hedge_count < delta_settings.max_hedge_triggers
                and abs_delta > threshold
                and delta_settings.hedge_action == "BUY_WING"
            )
            if can_trigger:
                tested_type = "CE" if net_delta < 0 else "PE"
                hedge_strike = wing_ce_strike if tested_type == "CE" else wing_pe_strike
                hedge_price = w_ce_price if tested_type == "CE" else w_pe_price
                hedge_unit_delta = unit_delta_for_option(
                    option_type=tested_type,
                    price=hedge_price,
                    spot=spot_close,
                    strike=hedge_strike,
                    timestamp=ts,
                    expiry_date=expiry,
                    vix=vix_close,
                    default_iv=delta_settings.default_iv,
                )
                hedge_lots = hedge_lots_for_delta(
                    net_delta=net_delta,
                    option_delta=hedge_unit_delta,
                    lot_size=lot_size,
                    max_lots=approved_lots,
                    mode=delta_settings.hedge_qty_mode,
                )

                # None of the no-op paths below consume a trigger, disarm, or set
                # a cooldown, so the condition can persist for the rest of the
                # session. Each is recorded once per episode, not once per candle.
                if hedge_price is None or hedge_strike is None:
                    if not wing_unavailable_logged:
                        wing_unavailable_logged = True
                        event_rows.append({
                            "run_id": run_id, "timestamp": ts,
                            "event_type": "DELTA_HEDGE", "reason_code": "DELTA_HEDGE_FAILED",
                            "reason_text": f"Delta hedge skipped; {tested_type} wing price unavailable.",
                            "payload_json": {"net_delta": net_delta, "strike": hedge_strike,
                                             "option_type": tested_type, "reason": "WING_PRICE_UNAVAILABLE"},
                        })
                        warnings.append(
                            f"Delta hedge trigger reached at {ts.strftime('%H:%M')} but {tested_type} wing price unavailable — skipping hedge"
                        )
                    can_trigger = False
                elif hedge_lots <= 0:
                    # Two very different causes; the wrong label sends whoever reads
                    # this event in exactly the wrong direction.
                    # The two causes are opposites and each gets its own throttle:
                    # one must not suppress the other inside the same episode.
                    hedgeable = is_hedgeable_delta(hedge_unit_delta)
                    already = undersized_logged if hedgeable else low_delta_logged
                    if not already:
                        if hedgeable:
                            undersized_logged = True
                        else:
                            low_delta_logged = True
                        event_rows.append({
                            "run_id": run_id, "timestamp": ts,
                            "event_type": "DELTA_HEDGE",
                            "reason_code": ("DELTA_HEDGE_SKIPPED_UNDERSIZED" if hedgeable
                                            else "DELTA_HEDGE_SKIPPED_LOW_DELTA"),
                            "reason_text": (
                                f"Net delta {net_delta:.2f} needs less than one lot of {tested_type} "
                                f"{hedge_strike}; hedging would overshoot."
                                if hedgeable else
                                f"{tested_type} {hedge_strike} has near-zero delta "
                                f"({hedge_unit_delta}); hedging it could not close a "
                                f"{net_delta:.2f} gap."
                            ),
                            "payload_json": {
                                "net_delta": net_delta,
                                "strike": hedge_strike,
                                "option_type": tested_type,
                                "unit_delta": hedge_unit_delta,
                            },
                        })
                    can_trigger = False

            if can_trigger:
                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "DELTA_HEDGE", "reason_code": "DELTA_HEDGE_TRIGGERED",
                    "reason_text": f"Net delta {net_delta:.2f} breached {threshold:.2f}; testing {tested_type} wing.",
                    "payload_json": {
                        "net_delta": net_delta,
                        "threshold": threshold,
                        "hedge_action": delta_settings.hedge_action,
                        "tested_side": tested_type,
                    },
                })

                hedge = {
                    "id": uuid.uuid4(),
                    "side": "BUY",
                    "option_type": tested_type,
                    "strike": hedge_strike,
                    "entry_price": hedge_price,
                    "last_price": hedge_price,
                    "entry_ts": ts,
                    "stale": 0,
                    "lots": hedge_lots,
                }
                delta_hedges.append(hedge)
                entry_charge = compute_leg_entry_charges(
                    hedge_lots,
                    lot_size,
                    [("BUY", tested_type, hedge_strike)],
                    [hedge_price],
                )
                delta_hedge_charges += entry_charge
                delta_hedge_count += 1
                delta_reentry_armed = False
                delta_hedge_status = "hedged"
                last_delta_hedge_ts = ts
                # `or` would treat an exactly-neutral 0.0 as missing and report
                # the old breached delta instead. Only None is missing.
                _post = _compute_net_delta(ts, spot_close, vix_close, straddle_cur, wing_cur)
                if _post is not None:
                    net_delta = _post
                last_net_delta = net_delta
                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "DELTA_HEDGE", "reason_code": "DELTA_HEDGE_EXECUTED",
                    "reason_text": (
                        f"Bought {hedge_lots} lot(s) {tested_type} delta hedge wing {hedge_strike} @ {hedge_price} "
                        f"(trigger {delta_hedge_count}/{delta_settings.max_hedge_triggers})"
                    ),
                    "payload_json": {
                        "net_delta": net_delta,
                        "strike": hedge_strike,
                        "option_type": tested_type,
                        "price": hedge_price,
                        "lots": hedge_lots,
                        "quantity": lot_size * hedge_lots,
                        "entry_charges": entry_charge,
                        "trigger_count": delta_hedge_count,
                        "unit_delta": hedge_unit_delta,
                        "max_lots": approved_lots,
                    },
                })

        # Refresh active-leg charges after any delta hedge inserted at this minute.
        delta_hedge_gross = _hedge_gross(delta_hedges, lot_size, approved_lots)
        gross_mtm_total = straddle_gross + wing_gross + delta_hedge_gross
        est_exit_charges = (
            compute_leg_exit_charges_estimate(
                approved_lots, lot_size, list(straddle_legs), list(straddle_cur)
            )
            + _hedge_exit_charges(delta_hedges, lot_size, approved_lots)
        )
        if wings_locked:
            # Same helper as above. This refresh previously recomputed with
            # approved_lots for both wings, silently discarding the netted
            # sizes and inflating the charges stop and trail decisions read.
            est_exit_charges += _wing_exit_charges(
                wing_legs, wing_cur, wing_lots, lot_size, approved_lots
            )
        net_mtm = gross_mtm_total - entry_charges - wing_entry_charges - delta_hedge_charges - est_exit_charges

        # ── Lock check: profit lock (up) or loss lock (down) ─────────────────
        if not wings_locked:
            profit_lock_hit = lock_trigger > 0 and net_mtm >= lock_trigger
            loss_lock_hit   = loss_lock_trigger > 0 and net_mtm <= -loss_lock_trigger
            if profit_lock_hit or loss_lock_hit:
                if w_ce_price is not None and w_pe_price is not None:
                    wings_locked       = True
                    lock_reason        = "profit" if profit_lock_hit else "loss"
                    wing_entry_prices  = [w_ce_price, w_pe_price]
                    wing_lock_ts       = ts
                    # Net each wing against protection the delta hedge already
                    # holds on the same contract; both react to the same move.
                    wing_strikes = [wing_ce_strike, wing_pe_strike]
                    wing_lots = [
                        wing_lots_after_hedges(
                            wing_strike=wing_strikes[i],
                            option_type=wing_legs[i][1],
                            approved_lots=approved_lots,
                            delta_hedges=delta_hedges,
                        )
                        for i in range(len(wing_legs))
                    ]
                    wing_entry_charges = sum(
                        compute_leg_entry_charges(
                            wing_lots[i], lot_size, [wing_legs[i]], [wing_entry_prices[i]]
                        )
                        for i in range(len(wing_legs))
                        if wing_lots[i] > 0 and wing_entry_prices[i] is not None
                    )
                    net_mtm           -= wing_entry_charges
                    label = "Profit lock" if profit_lock_hit else "Loss lock (defensive hedge)"
                    threshold = lock_trigger if profit_lock_hit else -loss_lock_trigger
                    event_rows.append({
                        "run_id": run_id, "timestamp": ts,
                        "event_type": "HOLD", "reason_code": "WINGS_LOCKED",
                        "reason_text": (
                            f"{label} triggered at {ts.strftime('%H:%M')} "
                            f"(net_mtm ₹{round(net_mtm+wing_entry_charges):,} crossed ₹{threshold:,.0f}) — "
                            f"bought wings CE {wing_ce_strike} @ {w_ce_price}, PE {wing_pe_strike} @ {w_pe_price}"
                        ),
                        "payload_json": {
                            "lock_reason": lock_reason,
                            "threshold": threshold,
                            "net_mtm_at_lock": round(net_mtm, 2),
                            "approved_lots": approved_lots,
                            "wing_ce": {"strike": wing_ce_strike, "price": w_ce_price,
                                        "lots": wing_lots[0]},
                            "wing_pe": {"strike": wing_pe_strike, "price": w_pe_price,
                                        "lots": wing_lots[1]},
                            "netted_against_hedges": [
                                approved_lots - wing_lots[0], approved_lots - wing_lots[1]
                            ],
                        },
                    })
                else:
                    label = "Profit lock" if profit_lock_hit else "Loss lock"
                    warnings.append(f"{label} trigger reached at {ts.strftime('%H:%M')} but wing prices unavailable — skipping lock")

        # ── Exit conditions ───────────────────────────────────────────────────
        # stop_threshold was computed above, before the delta-hedge block.

        trail_stop_level: Optional[float] = None
        if trail_trigger > 0 and trail_pct > 0:
            if not trail_active and net_mtm >= trail_trigger:
                trail_active = True
                trail_peak   = net_mtm
            if trail_active:
                if net_mtm > trail_peak:
                    trail_peak = net_mtm
                trail_stop_level = round(trail_peak * trail_pct, 2)

        fired_event: Optional[str] = None
        if net_mtm <= stop_threshold:
            fired_event = "STOP_EXIT"
        elif trail_active and trail_stop_level is not None and net_mtm <= trail_stop_level:
            fired_event = "TRAIL_EXIT"
        elif t >= sq_time:
            fired_event = "TIME_EXIT"

        mtm_rows.append({
            "run_id": run_id, "timestamp": ts,
            "spot_close": spot_close, "vix_close": vix_close,
            "gross_mtm": round(gross_mtm_total, 2),
            "est_exit_charges": round(est_exit_charges, 2),
            "net_mtm": round(net_mtm, 2),
            "net_delta": round(net_delta, 4) if net_delta is not None else None,
            "trail_stop_level": trail_stop_level,
            "event_code": fired_event,
        })

        # Per-leg MTM rows (straddle)
        for i, (side, opt_type, strike) in enumerate(straddle_legs):
            cp = straddle_cur[i]
            ep = straddle_entry_prices[i]
            leg_mtm_rows.append({
                "run_id": run_id, "leg_id": straddle_leg_ids[i], "timestamp": ts,
                "price": cp,
                "gross_leg_pnl": round((ep - cp) * lot_size * approved_lots, 2) if ep and cp else None,
                "stale_minutes": straddle_stale[i],
            })
        # Per-leg MTM rows (wings, only once locked)
        if wings_locked:
            for i, (side, opt_type, strike) in enumerate(wing_legs):
                if _wing_lots(wing_lots, i, approved_lots) <= 0:
                    continue   # fully netted: no leg was persisted for it
                cp = [w_ce_price, w_pe_price][i]
                ep = wing_entry_prices[i]
                leg_mtm_rows.append({
                    "run_id": run_id, "leg_id": wing_leg_ids[i], "timestamp": ts,
                    "price": cp,
                    "gross_leg_pnl": (
                        round((cp - ep) * lot_size * _wing_lots(wing_lots, i, approved_lots), 2)
                        if ep and cp else None
                    ),
                    "stale_minutes": wing_stale[i],
                })
        for hedge in delta_hedges:
            cp = hedge.get("last_price")
            ep = hedge.get("entry_price")
            hedge_qty = lot_size * _hedge_lots(hedge, approved_lots)
            leg_mtm_rows.append({
                "run_id": run_id, "leg_id": hedge["id"], "timestamp": ts,
                "price": cp,
                "gross_leg_pnl": round((cp - ep) * hedge_qty, 2) if ep and cp else None,
                "stale_minutes": hedge.get("stale", 0),
            })

        if fired_event:
            exit_reason = fired_event
            exit_ts = ts
            if fired_event == "TRAIL_EXIT" and trail_stop_level is not None:
                trail_stop_at_exit = trail_stop_level
            event_rows.append({
                "run_id": run_id, "timestamp": ts,
                "event_type": fired_event, "reason_code": fired_event,
                "reason_text": f"Exit at {ts.strftime('%H:%M')}: {fired_event}",
                "payload_json": {"net_mtm": round(net_mtm, 2), "spot": spot_close, "wings_locked": wings_locked},
            })
            break

    # ── Final P&L ─────────────────────────────────────────────────────────────
    realized_net_pnl: Optional[float] = None
    gross_pnl: Optional[float] = None
    total_charges: Optional[float] = None

    if trade_open:
        exit_s_prices = list(straddle_last_prices)
        straddle_gross_pnl = sum(
            (ep - xp) * lot_size * approved_lots
            for ep, xp in zip(straddle_entry_prices, exit_s_prices)
            if ep and xp
        )
        wing_gross_pnl = 0.0
        if wings_locked:
            exit_w_prices = list(wing_last_prices)
            wing_gross_pnl = sum(
                (xp - ep) * lot_size * _wing_lots(wing_lots, i, approved_lots)
                for i, (ep, xp) in enumerate(zip(wing_entry_prices, exit_w_prices))
                if ep and xp
            )
        delta_hedge_gross_pnl = _hedge_gross(delta_hedges, lot_size, approved_lots)

        gross_pnl = straddle_gross_pnl + wing_gross_pnl + delta_hedge_gross_pnl

        # Hedge legs are charged separately so each uses its own lot count.
        # compute_leg_total_charges is a round trip, so the per-wing loop below
        # already includes wing entry. Adding wing_entry_charges as well
        # double-counted it (pre-existing).
        total_charges = compute_leg_total_charges(
            approved_lots, lot_size, list(straddle_legs),
            list(straddle_entry_prices), list(straddle_last_prices),
        )
        if wings_locked:
            # Per leg: each wing carries its own netted size.
            for i, leg in enumerate(wing_legs):
                lots_i = _wing_lots(wing_lots, i, approved_lots)
                if lots_i <= 0:
                    continue
                total_charges += compute_leg_total_charges(
                    lots_i, lot_size, [leg],
                    [wing_entry_prices[i]], [wing_last_prices[i]],
                )
        for h in delta_hedges:
            if h.get("entry_price") is None or h.get("last_price") is None:
                continue
            total_charges += compute_leg_total_charges(
                _hedge_lots(h, approved_lots),
                lot_size,
                [(h["side"], h["option_type"], h["strike"])],
                [h["entry_price"]],
                [h["last_price"]],
            )

        realized_net_pnl = round(gross_pnl - total_charges, 2)

        if exit_reason == "TRAIL_EXIT" and trail_stop_at_exit is not None:
            realized_net_pnl = round(trail_stop_at_exit, 2)
            gross_pnl        = round(trail_stop_at_exit + total_charges, 2)

    if not trade_open:
        exit_reason = exit_reason or "NO_TRADE"
        event_rows.append({
            "run_id": run_id,
            "timestamp": datetime.combine(trade_date, sq_time),
            "event_type": "NO_TRADE", "reason_code": exit_reason,
        })

    status = "no_trade" if not trade_open else "completed"

    # ── Persist ───────────────────────────────────────────────────────────────
    db.add(StrategyRun(
        id=run_id,
        user_id=user_id,
        strategy_id=strategy["id"],
        strategy_version=strategy.get("version", "v1"),
        run_type=config.get("run_type", "single_session_backtest"),
        executor=strategy.get("executor", "straddle_adjustment_v1"),
        instrument=instrument,
        trade_date=trade_date,
        entry_time=actual_entry_ts.strftime("%H:%M") if actual_entry_ts else None,
        exit_time=exit_ts.strftime("%H:%M") if exit_ts else None,
        status=status,
        exit_reason=exit_reason,
        capital=capital_amount,
        lot_size=lot_size,
        approved_lots=approved_lots,
        entry_credit_per_unit=round(entry_credit_per_unit, 2) if trade_open else None,
        entry_credit_total=round(entry_credit_total, 2) if trade_open else None,
        gross_pnl=round(gross_pnl, 2) if gross_pnl is not None else None,
        total_charges=round(total_charges, 2) if total_charges is not None else None,
        realized_net_pnl=realized_net_pnl,
        config_json={**config, "strategy_id": strategy["id"]},
        result_json={
            "warnings": warnings,
            "exit_reason": exit_reason,
            "wings_locked": wings_locked,
            "lock_reason": lock_reason,
            "wing_lock_ts": wing_lock_ts.isoformat() if wing_lock_ts else None,
            "delta_hedge_enabled": delta_settings.enabled,
            "delta_hedge_status": delta_hedge_status,
            "delta_hedge_count": delta_hedge_count,
            "last_net_delta": last_net_delta,
        },
    ))

    if trade_open:
        # Persist straddle legs
        straddle_ts = actual_entry_ts.replace(tzinfo=None) if actual_entry_ts else None
        for i, ((side, opt_type, strike), leg_id) in enumerate(zip(straddle_legs, straddle_leg_ids)):
            ep = straddle_entry_prices[i]
            xp = straddle_last_prices[i]
            leg_gross = round((ep - xp) * lot_size * approved_lots, 2) if ep and xp else None
            db.add(StrategyRunLeg(
                id=leg_id, run_id=run_id, leg_index=i,
                side=side, option_type=opt_type, strike=strike, expiry_date=expiry,
                quantity=lot_size * approved_lots,
                entry_price=ep, exit_price=xp, gross_leg_pnl=leg_gross,
                entry_timestamp=straddle_ts,
            ))
        # Persist wing legs (if locked)
        if wings_locked:
            wing_ts = wing_lock_ts.replace(tzinfo=None) if wing_lock_ts else None
            for i, ((side, opt_type, strike), leg_id) in enumerate(zip(wing_legs, wing_leg_ids)):
                ep = wing_entry_prices[i]
                xp = wing_last_prices[i]
                lots_i = _wing_lots(wing_lots, i, approved_lots)
                if lots_i <= 0:
                    continue   # wing fully covered by hedges; nothing was bought
                leg_gross = round((xp - ep) * lot_size * lots_i, 2) if ep and xp else None
                db.add(StrategyRunLeg(
                    id=leg_id, run_id=run_id, leg_index=i + 2,
                    side=side, option_type=opt_type, strike=strike, expiry_date=expiry,
                    quantity=lot_size * lots_i,
                    entry_price=ep, exit_price=xp, gross_leg_pnl=leg_gross,
                    entry_timestamp=wing_ts,
                ))
        # Leg slots 2/3 are reserved for the lock wings whether or not they fired,
        # so hedge legs always start at 4 — matching the live engine's indexing.
        delta_start_index = 4
        for i, hedge in enumerate(delta_hedges):
            ep = hedge.get("entry_price")
            xp = hedge.get("last_price")
            hedge_qty = lot_size * _hedge_lots(hedge, approved_lots)
            leg_gross = round((xp - ep) * hedge_qty, 2) if ep and xp else None
            entry_ts = hedge["entry_ts"].replace(tzinfo=None) if hedge.get("entry_ts") else None
            db.add(StrategyRunLeg(
                id=hedge["id"], run_id=run_id, leg_index=delta_start_index + i,
                side=hedge["side"], option_type=hedge["option_type"], strike=hedge["strike"],
                expiry_date=expiry,
                quantity=hedge_qty,
                entry_price=ep, exit_price=xp, gross_leg_pnl=leg_gross,
                entry_timestamp=entry_ts,
            ))

    for row in mtm_rows:
        db.add(StrategyRunMtm(**row))
    for row in leg_mtm_rows:
        db.add(StrategyLegMtm(**row))
    for row in event_rows:
        db.add(StrategyRunEvent(**row))

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return ExecutionResult(
        run_id=str(run_id),
        status=status,
        exit_reason=exit_reason,
        realized_net_pnl=realized_net_pnl,
        warnings=warnings,
    )
