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
from datetime import date as date_type, datetime, time
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
    lock_reason:          Optional[str]         = None   # "profit" | "loss"
    wing_entry_prices:    List[Optional[float]] = [None, None]
    wing_last_prices:     List[Optional[float]] = [None, None]
    wing_stale:           List[int]             = [0, 0]
    wing_lock_ts:         Optional[datetime]    = None
    wing_entry_charges    = 0.0

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
        if wings_locked:
            wing_cur = [w_ce_price, w_pe_price]
            wing_gross = sum(
                (cp - ep)   # BUY leg: profit = current - entry
                for ep, cp in zip(wing_entry_prices, wing_cur)
                if ep is not None and cp is not None
            ) * lot_size * approved_lots

        gross_mtm_total = straddle_gross + wing_gross

        # Estimate exit charges for currently active legs
        if wings_locked:
            active_legs   = straddle_legs + wing_legs
            active_prices = [w_ce_price, w_pe_price] if wings_locked else []
            all_cur_prices = list(straddle_cur) + [w_ce_price, w_pe_price]
            est_exit_charges = compute_leg_exit_charges_estimate(approved_lots, lot_size, active_legs, all_cur_prices)
        else:
            est_exit_charges = compute_leg_exit_charges_estimate(approved_lots, lot_size, straddle_legs, straddle_cur)

        net_mtm = gross_mtm_total - entry_charges - wing_entry_charges - est_exit_charges

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
                    wing_entry_charges = compute_leg_entry_charges(approved_lots, lot_size, wing_legs, wing_entry_prices)
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
                            "wing_ce": {"strike": wing_ce_strike, "price": w_ce_price},
                            "wing_pe": {"strike": wing_pe_strike, "price": w_pe_price},
                        },
                    })
                else:
                    label = "Profit lock" if profit_lock_hit else "Loss lock"
                    warnings.append(f"{label} trigger reached at {ts.strftime('%H:%M')} but wing prices unavailable — skipping lock")

        # ── Exit conditions ───────────────────────────────────────────────────
        stop_threshold = -(capital_amount * stop_capital_pct) if stop_capital_pct > 0 else -(entry_credit_total * 1.5)

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
                cp = [w_ce_price, w_pe_price][i]
                ep = wing_entry_prices[i]
                leg_mtm_rows.append({
                    "run_id": run_id, "leg_id": wing_leg_ids[i], "timestamp": ts,
                    "price": cp,
                    "gross_leg_pnl": round((cp - ep) * lot_size * approved_lots, 2) if ep and cp else None,
                    "stale_minutes": wing_stale[i],
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
                (xp - ep) * lot_size * approved_lots
                for ep, xp in zip(wing_entry_prices, exit_w_prices)
                if ep and xp
            )

        gross_pnl = straddle_gross_pnl + wing_gross_pnl

        if wings_locked:
            all_exit_legs   = straddle_legs + wing_legs
            all_entry_prices = straddle_entry_prices + wing_entry_prices
            all_exit_prices  = list(straddle_last_prices) + list(wing_last_prices)
        else:
            all_exit_legs    = straddle_legs
            all_entry_prices = straddle_entry_prices
            all_exit_prices  = list(straddle_last_prices)

        total_charges = compute_leg_total_charges(
            approved_lots, lot_size, all_exit_legs, all_entry_prices, all_exit_prices
        ) + wing_entry_charges

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
        },
    ))

    if trade_open:
        # Persist straddle legs
        for i, ((side, opt_type, strike), leg_id) in enumerate(zip(straddle_legs, straddle_leg_ids)):
            ep = straddle_entry_prices[i]
            xp = straddle_last_prices[i]
            leg_gross = round((ep - xp) * lot_size * approved_lots, 2) if ep and xp else None
            db.add(StrategyRunLeg(
                id=leg_id, run_id=run_id, leg_index=i,
                side=side, option_type=opt_type, strike=strike, expiry_date=expiry,
                quantity=lot_size * approved_lots,
                entry_price=ep, exit_price=xp, gross_leg_pnl=leg_gross,
            ))
        # Persist wing legs (if locked)
        if wings_locked:
            for i, ((side, opt_type, strike), leg_id) in enumerate(zip(wing_legs, wing_leg_ids)):
                ep = wing_entry_prices[i]
                xp = wing_last_prices[i]
                leg_gross = round((xp - ep) * lot_size * approved_lots, 2) if ep and xp else None
                db.add(StrategyRunLeg(
                    id=leg_id, run_id=run_id, leg_index=i + 2,
                    side=side, option_type=opt_type, strike=strike, expiry_date=expiry,
                    quantity=lot_size * approved_lots,
                    entry_price=ep, exit_price=xp, gross_leg_pnl=leg_gross,
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
