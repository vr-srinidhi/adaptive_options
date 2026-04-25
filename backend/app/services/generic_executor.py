"""
Generic strategy executor.

Powers every strategy registered in the workbench catalog (Short Straddle,
Buy Call, Iron Condor, …).  Strategy-specific logic lives entirely in the
catalog definition (leg_template, entry_rule_id, exit_rule) — this file
contains no per-strategy code.

Public API
----------
validate_run(db, strategy, config)          → ValidationResult
execute_run(db, run_id, strategy, config, validation) → ExecutionResult

Flow
----
  validate_run
    ├── check trading_day readiness
    ├── get contract spec (lot size, strike step)
    ├── get spot at entry → resolve ATM strike
    ├── resolve expiry
    ├── check CE + PE exist at entry
    ├── check VIX guardrail (if enabled)
    └── compute approved_lots

  execute_run
    ├── load spot, vix, option candles from warehouse
    ├── entry at entry_time
    ├── minute loop:
    │     evaluate entry rule (if no trade open)
    │     on ENTER → record leg prices, persist entry event
    │     on HOLD  → check exit conditions (if trade open)
    │       TARGET_EXIT, STOP_EXIT, TIME_EXIT, DATA_GAP_EXIT
    ├── persist strategy_runs header
    ├── persist strategy_run_legs
    ├── persist strategy_run_mtm (one row per minute)
    ├── persist strategy_leg_mtm (one row per leg per minute)
    └── persist strategy_run_events
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.historical import OptionsCandle, TradingDay
from app.models.strategy_run import (
    StrategyLegMtm,
    StrategyRun,
    StrategyRunEvent,
    StrategyRunLeg,
    StrategyRunMtm,
)
from app.services.charges_service import (
    compute_entry_charges,
    compute_exit_charges_estimate,
    compute_total_charges,
)
from app.services.contract_spec_service import (
    ContractSpec,
    ExpiryResult,
    get_contract_spec,
    get_spot_at_entry,
    get_vix_at_entry,
    resolve_atm_strike,
    resolve_expiry,
    resolve_leg_strikes,
)
from app.services.entry_rule_registry import get_entry_rule
from app.services.historical_market_data import (
    load_option_candles_for_strikes,
    load_spot_candles,
    load_vix_candles,
    vix_at_time,
)

log = logging.getLogger(__name__)

_SESSION_START = time(9, 15)
_SESSION_END   = time(15, 30)
_MAX_STALE_MINUTES = 1          # allow at most 1 stale minute before DATA_GAP_EXIT


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    validated: bool
    instrument: str
    trade_date: str
    entry_time: str
    resolved_expiry: Optional[str] = None
    spot_at_entry: Optional[float] = None
    atm_strike: Optional[int] = None
    contracts: List[Dict] = field(default_factory=list)
    lot_size: int = 0
    approved_lots: int = 0
    estimated_margin: float = 0.0
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ExecutionResult:
    run_id: str
    status: str
    exit_reason: Optional[str]
    realized_net_pnl: Optional[float]
    warnings: List[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_time(hhmm: str, default: time = time(9, 50)) -> time:
    try:
        h, m = map(int, hhmm.split(":"))
        return time(h, m)
    except Exception:
        return default


def _exit_time(config: Dict) -> time:
    return _parse_time(
        config.get("exit_rule", {}).get("time_exit", "15:25"),
        default=time(15, 25),
    )


def _get_price(
    option_index: Dict[Tuple[int, str], Dict[int, Dict]],
    key: Tuple[int, str],
    minute_idx: int,
    last_price: Optional[float],
    stale_count: int,
) -> Tuple[Optional[float], int]:
    """Return (price, updated_stale_count) with bounded freshness fallback."""
    tick = option_index.get(key, {}).get(minute_idx)
    if tick is not None:
        return tick["price"], 0
    # Allow carrying last known price for up to _MAX_STALE_MINUTES
    if last_price is not None and stale_count < _MAX_STALE_MINUTES:
        return last_price, stale_count + 1
    return None, stale_count + 1


# ── validate_run ──────────────────────────────────────────────────────────────

async def validate_run(
    db: AsyncSession,
    strategy: Dict[str, Any],
    config: Dict[str, Any],
) -> ValidationResult:
    instrument  = config.get("instrument", "NIFTY").upper()
    trade_date_str = config.get("trade_date", "")
    entry_time_str = config.get("entry_time", "09:50")
    warnings: List[str] = []

    try:
        trade_date = date_type.fromisoformat(trade_date_str)
    except (ValueError, TypeError):
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error="Invalid trade_date format. Use YYYY-MM-DD.",
        )

    # 1. Trading day must exist and be backtest_ready
    td_row = (await db.execute(
        select(TradingDay).where(TradingDay.trade_date == trade_date)
    )).scalar_one_or_none()

    if td_row is None:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error=f"No trading day record for {trade_date}. Ingest data first.",
        )
    if not td_row.backtest_ready:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error=f"{trade_date} is not marked backtest_ready (ingestion may be incomplete).",
        )

    # 2. Contract spec
    spec: ContractSpec = await get_contract_spec(db, instrument, trade_date)

    # 3. Spot at entry → ATM strike
    spot = await get_spot_at_entry(db, instrument, trade_date, entry_time_str)
    if spot is None:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error=f"No spot candle for {instrument} at {entry_time_str} on {trade_date}.",
        )
    atm_strike = resolve_atm_strike(spot, spec.strike_step)

    # 4. Expiry resolution
    try:
        expiry_result: ExpiryResult = await resolve_expiry(
            db, instrument, trade_date, entry_time_str
        )
        warnings.extend(expiry_result.warnings)
    except ValueError as e:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error=str(e),
        )

    # 5. VIX guardrail (optional)
    if config.get("vix_guardrail_enabled"):
        vix = await get_vix_at_entry(db, trade_date, entry_time_str)
        if vix is None:
            return ValidationResult(
                validated=False, instrument=instrument,
                trade_date=trade_date_str, entry_time=entry_time_str,
                error=f"VIX guardrail enabled but no VIX data at {entry_time_str} on {trade_date}.",
            )
        vix_min = float(config.get("vix_min", 0))
        vix_max = float(config.get("vix_max", 999))
        if not (vix_min <= vix <= vix_max):
            return ValidationResult(
                validated=False, instrument=instrument,
                trade_date=trade_date_str, entry_time=entry_time_str,
                error=f"VIX {vix:.2f} outside guardrail [{vix_min}, {vix_max}].",
            )

    # 6. Resolve concrete leg strikes from template
    leg_template = strategy.get("leg_template", [])
    resolved_legs = resolve_leg_strikes(leg_template, atm_strike, spec.strike_step)

    # 7. Confirm each resolved leg strike has a price within the entry grace window
    from datetime import timedelta as _td
    _GRACE = 5   # minutes — must match _ENTRY_GRACE_MINUTES in entry_rule_registry
    entry_dt = datetime.combine(trade_date, _parse_time(entry_time_str))
    grace_end_dt = entry_dt + _td(minutes=_GRACE)
    contracts = []
    exact_missing: List[str] = []
    fully_missing: List[str] = []
    for side, opt_type, strike in resolved_legs:
        # Check exact minute first
        exact_row = (await db.execute(
            select(OptionsCandle)
            .where(
                OptionsCandle.symbol == instrument,
                OptionsCandle.trade_date == trade_date,
                OptionsCandle.expiry_date == expiry_result.expiry,
                OptionsCandle.strike == strike,
                OptionsCandle.option_type == opt_type,
                OptionsCandle.timestamp == entry_dt,
            )
            .limit(1)
        )).scalar_one_or_none()
        if exact_row is None:
            exact_missing.append(f"{opt_type} {strike}")
            # Check whether any minute in the grace window has data
            grace_row = (await db.execute(
                select(OptionsCandle)
                .where(
                    OptionsCandle.symbol == instrument,
                    OptionsCandle.trade_date == trade_date,
                    OptionsCandle.expiry_date == expiry_result.expiry,
                    OptionsCandle.strike == strike,
                    OptionsCandle.option_type == opt_type,
                    OptionsCandle.timestamp > entry_dt,
                    OptionsCandle.timestamp <= grace_end_dt,
                )
                .limit(1)
            )).scalar_one_or_none()
            if grace_row is None:
                fully_missing.append(f"{opt_type} {strike}")
        contracts.append({"side": side, "option_type": opt_type, "strike": strike})

    if fully_missing:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error=(
                f"No price data within {_GRACE} minutes of {entry_time_str} "
                f"for: {', '.join(fully_missing)}. "
                "These strikes are not tradable on this date."
            ),
        )
    if exact_missing:
        warnings.append(
            f"No price at exactly {entry_time_str} for {', '.join(exact_missing)}. "
            f"Entry will be attempted within the next {_GRACE} minutes."
        )

    # 8. Position sizing
    capital = float(config.get("capital", 0))
    if capital <= 0:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error="Capital must be positive.",
        )
    approved_lots = max(0, int(capital // spec.estimated_margin_per_lot))
    if approved_lots < 1:
        return ValidationResult(
            validated=False, instrument=instrument,
            trade_date=trade_date_str, entry_time=entry_time_str,
            error=(
                f"CAPITAL_INSUFFICIENT: capital ₹{capital:,.0f} < "
                f"est. margin ₹{spec.estimated_margin_per_lot:,.0f}/lot."
            ),
        )

    return ValidationResult(
        validated=True,
        instrument=instrument,
        trade_date=trade_date_str,
        entry_time=entry_time_str,
        resolved_expiry=expiry_result.expiry.isoformat(),
        spot_at_entry=round(spot, 2),
        atm_strike=atm_strike,
        contracts=contracts,
        lot_size=spec.lot_size,
        approved_lots=approved_lots,
        estimated_margin=round(approved_lots * spec.estimated_margin_per_lot, 2),
        warnings=warnings,
    )


# ── execute_run ───────────────────────────────────────────────────────────────

async def execute_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    strategy: Dict[str, Any],
    config: Dict[str, Any],
    validation: ValidationResult,
    user_id: Optional[uuid.UUID] = None,
) -> ExecutionResult:
    """
    Execute the strategy for one session and persist all results.
    Called only after validate_run returns validated=True.
    """
    instrument   = validation.instrument
    trade_date   = date_type.fromisoformat(validation.trade_date)
    entry_time   = _parse_time(validation.entry_time)
    expiry       = date_type.fromisoformat(validation.resolved_expiry)
    atm_strike   = validation.atm_strike
    lot_size     = validation.lot_size
    approved_lots = validation.approved_lots
    warnings: List[str] = list(validation.warnings)

    exit_rule = strategy.get("exit_rule", {})
    target_pct   = float(exit_rule.get("target_pct", 0.30))
    stop_multiple = float(exit_rule.get("stop_multiple", 1.5))
    sq_time      = _exit_time(config)

    entry_rule = get_entry_rule(strategy.get("entry_rule_id", "timed_entry"))

    # Resolved legs: (side, option_type, strike, leg_uuid)
    leg_template = strategy.get("leg_template", [])
    spec = await get_contract_spec(db, instrument, trade_date)
    resolved = resolve_leg_strikes(leg_template, atm_strike, spec.strike_step)
    legs_to_fetch = {(strike, opt_type) for _, opt_type, strike in resolved}
    leg_ids = [uuid.uuid4() for _ in resolved]

    # ── Load market data ──────────────────────────────────────────────────────
    spot_candles = await load_spot_candles(db, instrument, trade_date)
    vix_candles  = await load_vix_candles(db, trade_date)
    option_index, _ = await load_option_candles_for_strikes(
        db, instrument, trade_date, expiry, legs_to_fetch,
        option_price_source="close",
    )

    if not spot_candles:
        await _persist_no_trade(db, run_id, strategy, config, validation, user_id, "NO_SPOT_DATA")
        return ExecutionResult(run_id=str(run_id), status="no_trade", exit_reason="NO_SPOT_DATA", realized_net_pnl=None)

    session_start_dt = datetime.combine(trade_date, _SESSION_START)

    # ── State ─────────────────────────────────────────────────────────────────
    trade_open      = False
    entry_prices: List[Optional[float]] = [None] * len(resolved)
    last_prices: List[Optional[float]] = [None] * len(resolved)
    stale_counts: List[int] = [0] * len(resolved)
    entry_credit_per_unit = 0.0
    entry_credit_total    = 0.0
    entry_charges         = 0.0
    exit_reason: Optional[str] = None
    exit_ts: Optional[datetime] = None

    mtm_rows: List[Dict]      = []
    leg_mtm_rows: List[Dict]  = []
    event_rows: List[Dict]    = []

    for candle in spot_candles:
        ts: datetime = candle["date"]
        t = ts.time().replace(second=0, microsecond=0)

        if t < _SESSION_START or t >= _SESSION_END:
            continue

        minute_idx = int((ts - session_start_dt).total_seconds() / 60)
        spot_close = candle["close"]
        vix_close  = vix_at_time(vix_candles, ts)

        # Current leg prices
        cur_prices: List[Optional[float]] = []
        for i, (side, opt_type, strike) in enumerate(resolved):
            price, stale_counts[i] = _get_price(
                option_index, (strike, opt_type), minute_idx,
                last_prices[i], stale_counts[i],
            )
            cur_prices.append(price)
            if price is not None:
                last_prices[i] = price

        # ── No trade open: evaluate entry rule ────────────────────────────────
        if not trade_open:
            signal = entry_rule.evaluate(ts, config, trade_open=False)
            if signal.action == "ENTER":
                # Validate all leg prices are available at entry
                if any(p is None for p in cur_prices):
                    # Log as HOLD so the grace-window retry can fire next minute
                    event_rows.append({
                        "run_id": run_id, "timestamp": ts,
                        "event_type": "HOLD", "reason_code": "MISSING_LEG_PRICE",
                        "reason_text": "Leg prices unavailable at entry — retrying next minute",
                    })
                    continue

                trade_open = True
                entry_prices = list(cur_prices)

                # Only SELL legs contribute to entry credit for short strategies
                sell_prices = [
                    p for p, (side, _, _) in zip(entry_prices, resolved)
                    if side == "SELL"
                ]
                entry_credit_per_unit = sum(sell_prices)
                entry_credit_total = entry_credit_per_unit * lot_size * approved_lots
                entry_charges = compute_entry_charges(approved_lots, lot_size, sell_prices)

                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "ENTRY", "reason_code": "ENTRY_SCHEDULED",
                    "reason_text": f"Entered short straddle at {ts.strftime('%H:%M')}",
                    "payload_json": {
                        "spot": spot_close,
                        "legs": [
                            {"side": s, "option_type": o, "strike": k, "price": p}
                            for (s, o, k), p in zip(resolved, entry_prices)
                        ],
                    },
                })
            else:
                event_rows.append({
                    "run_id": run_id, "timestamp": ts,
                    "event_type": "HOLD", "reason_code": signal.reason_code,
                })
            continue

        # ── Trade open: compute MTM and check exits ───────────────────────────
        # Handle stale / missing prices
        data_gap = any(
            stale_counts[i] > _MAX_STALE_MINUTES for i in range(len(resolved))
        )

        valid_prices = all(p is not None for p in cur_prices)
        if not valid_prices:
            data_gap = True

        if data_gap and config.get("exit_rule", {}).get("data_gap_exit", True):
            exit_reason = "DATA_GAP_EXIT"
            exit_ts = ts
            event_rows.append({
                "run_id": run_id, "timestamp": ts,
                "event_type": "DATA_GAP_EXIT", "reason_code": "DATA_GAP_EXIT",
                "reason_text": f"Price freshness exceeded at {ts.strftime('%H:%M')}",
            })
            break

        # Gross MTM: for SELL legs profit = entry_price - current_price
        gross_mtm_per_unit = sum(
            (ep - cp if side == "SELL" else cp - ep)
            for (side, _, _), ep, cp in zip(resolved, entry_prices, cur_prices)
            if ep is not None and cp is not None
        )
        gross_mtm_total = gross_mtm_per_unit * lot_size * approved_lots

        sell_current = [
            p for p, (side, _, _) in zip(cur_prices, resolved)
            if side == "SELL" and p is not None
        ]
        est_exit_charges = compute_exit_charges_estimate(approved_lots, lot_size, sell_current or [0.0])
        net_mtm = gross_mtm_total - entry_charges - est_exit_charges

        # Exit conditions
        target_threshold = entry_credit_total * target_pct
        stop_threshold   = -(entry_credit_total * stop_multiple)

        fired_event: Optional[str] = None
        if net_mtm >= target_threshold:
            fired_event = "TARGET_EXIT"
        elif net_mtm <= stop_threshold:
            fired_event = "STOP_EXIT"
        elif t >= sq_time:
            fired_event = "TIME_EXIT"

        mtm_rows.append({
            "run_id": run_id, "timestamp": ts,
            "spot_close": spot_close,
            "vix_close": vix_close,
            "gross_mtm": round(gross_mtm_total, 2),
            "est_exit_charges": round(est_exit_charges, 2),
            "net_mtm": round(net_mtm, 2),
            "event_code": fired_event,
        })

        for i, ((side, opt_type, strike), leg_id) in enumerate(zip(resolved, leg_ids)):
            leg_mtm_rows.append({
                "run_id": run_id, "leg_id": leg_id, "timestamp": ts,
                "price": cur_prices[i],
                "gross_leg_pnl": round(
                    ((entry_prices[i] - cur_prices[i]) if side == "SELL"
                     else (cur_prices[i] - entry_prices[i])) * lot_size * approved_lots, 2
                ) if cur_prices[i] is not None else None,
                "stale_minutes": stale_counts[i],
            })

        if fired_event:
            exit_reason = fired_event
            exit_ts = ts
            event_rows.append({
                "run_id": run_id, "timestamp": ts,
                "event_type": fired_event, "reason_code": fired_event,
                "reason_text": f"Exit triggered at {ts.strftime('%H:%M')}: {fired_event}",
                "payload_json": {"net_mtm": round(net_mtm, 2), "spot": spot_close},
            })
            break

    # ── Compute final P&L ─────────────────────────────────────────────────────
    realized_net_pnl: Optional[float] = None
    gross_pnl: Optional[float] = None
    total_charges: Optional[float] = None

    if trade_open:
        # Find exit prices (last prices from MTM loop or final cur_prices)
        exit_prices = list(last_prices)
        sell_entry  = [p for p, (s, _, _) in zip(entry_prices, resolved) if s == "SELL" and p is not None]
        sell_exit   = [p for p, (s, _, _) in zip(exit_prices,  resolved) if s == "SELL" and p is not None]
        buy_entry   = [p for p, (s, _, _) in zip(entry_prices, resolved) if s == "BUY"  and p is not None]
        buy_exit    = [p for p, (s, _, _) in zip(exit_prices,  resolved) if s == "BUY"  and p is not None]

        gross_pnl = sum(
            (ep - xp if side == "SELL" else xp - ep) * lot_size * approved_lots
            for (side, _, _), ep, xp in zip(resolved, entry_prices, exit_prices)
            if ep is not None and xp is not None
        )
        total_charges = compute_total_charges(
            approved_lots, lot_size,
            sell_entry, sell_exit if sell_exit else [0.0],
        )
        realized_net_pnl = round(gross_pnl - total_charges, 2)

    if not trade_open:
        # Session ended without an entry
        if not exit_reason:
            exit_reason = "NO_TRADE"
        event_rows.append({
            "run_id": run_id, "timestamp": datetime.combine(trade_date, sq_time),
            "event_type": "NO_TRADE", "reason_code": exit_reason or "SESSION_END",
            "reason_text": "Session ended without trade entry.",
        })

    # ── Persist ───────────────────────────────────────────────────────────────
    status = (
        "no_trade" if not trade_open
        else "completed" if exit_reason
        else "completed"
    )

    run_row = StrategyRun(
        id=run_id,
        user_id=user_id,
        strategy_id=strategy["id"],
        strategy_version=strategy.get("version", "v1"),
        run_type=config.get("run_type", "single_session_backtest"),
        executor=strategy.get("executor", "generic_v1"),
        instrument=instrument,
        trade_date=trade_date,
        entry_time=validation.entry_time if trade_open else None,
        exit_time=exit_ts.strftime("%H:%M") if exit_ts else None,
        status=status,
        exit_reason=exit_reason,
        capital=validation.capital if hasattr(validation, "capital") else float(config.get("capital", 0)),
        lot_size=lot_size,
        approved_lots=approved_lots,
        entry_credit_per_unit=round(entry_credit_per_unit, 2) if trade_open else None,
        entry_credit_total=round(entry_credit_total, 2) if trade_open else None,
        gross_pnl=round(gross_pnl, 2) if gross_pnl is not None else None,
        total_charges=round(total_charges, 2) if total_charges is not None else None,
        realized_net_pnl=realized_net_pnl,
        config_json={**config, "strategy_id": strategy["id"]},
        result_json={"warnings": warnings, "exit_reason": exit_reason},
    )
    db.add(run_row)

    if trade_open:
        for i, ((side, opt_type, strike), leg_id) in enumerate(zip(resolved, leg_ids)):
            ep = entry_prices[i]
            xp = last_prices[i]
            if ep is not None and xp is not None:
                raw_pnl = (ep - xp) if side == "SELL" else (xp - ep)
                leg_gross_pnl = round(raw_pnl * lot_size * approved_lots, 2)
            else:
                leg_gross_pnl = None
            db.add(StrategyRunLeg(
                id=leg_id,
                run_id=run_id,
                leg_index=i,
                side=side,
                option_type=opt_type,
                strike=strike,
                expiry_date=expiry,
                quantity=lot_size * approved_lots,
                entry_price=ep,
                exit_price=xp,
                gross_leg_pnl=leg_gross_pnl,
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


async def _persist_no_trade(
    db: AsyncSession,
    run_id: uuid.UUID,
    strategy: Dict,
    config: Dict,
    validation: ValidationResult,
    user_id: Optional[uuid.UUID],
    reason: str,
) -> None:
    trade_date = date_type.fromisoformat(validation.trade_date)
    db.add(StrategyRun(
        id=run_id,
        user_id=user_id,
        strategy_id=strategy["id"],
        run_type=config.get("run_type", "single_session_backtest"),
        executor=strategy.get("executor", "generic_v1"),
        instrument=validation.instrument,
        trade_date=trade_date,
        status="no_trade",
        exit_reason=reason,
        capital=float(config.get("capital", 0)),
        config_json=config,
    ))
    db.add(StrategyRunEvent(
        run_id=run_id,
        timestamp=datetime.combine(trade_date, _SESSION_START),
        event_type="NO_TRADE",
        reason_code=reason,
    ))
    await db.commit()
