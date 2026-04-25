"""
Strategy replay serializer.

Reads persisted strategy_runs + related tables and shapes them into the
PRD §13 replay payload consumed by the frontend ReplayAnalyzer.

Public API
----------
strategy_run_library_item(run)                          → dict  (for Runs Library list)
strategy_run_replay_payload(run, legs, mtm, leg_mtm, events) → dict  (for Replay Analyzer)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.strategy_run import (
    StrategyLegMtm,
    StrategyRun,
    StrategyRunEvent,
    StrategyRunLeg,
    StrategyRunMtm,
)


def strategy_run_library_item(run: StrategyRun) -> Dict[str, Any]:
    """Compact row for the Runs Library list."""
    pnl = float(run.realized_net_pnl) if run.realized_net_pnl is not None else None
    trade_date = run.trade_date.isoformat() if run.trade_date else None
    return {
        "id":               str(run.id),
        "kind":             "strategy_run",
        "strategy_id":      run.strategy_id,
        "strategy_name":    run.strategy_id,
        "instrument":       run.instrument,
        "trade_date":       trade_date,
        "date_label":       trade_date,
        "entry_time":       run.entry_time,
        "status":           run.status,
        "exit_reason":      run.exit_reason,
        "lots":             run.approved_lots,
        "lot_size":         run.lot_size,
        "realized_net_pnl": pnl,
        "pnl":              pnl,
        "created_at":       run.created_at.isoformat() if run.created_at else None,
        "route":            f"/workbench/replay/strategy_run/{run.id}",
    }


def strategy_run_replay_payload(
    run: StrategyRun,
    legs: List[StrategyRunLeg],
    mtm_rows: List[StrategyRunMtm],
    leg_mtm_rows: List[StrategyLegMtm],
    events: List[StrategyRunEvent],
    spot_candles_full: Optional[List] = None,
) -> Dict[str, Any]:
    """Full replay payload per PRD §13."""

    serialized_legs = [
        {
            "leg_index":    leg.leg_index,
            "side":         leg.side,
            "option_type":  leg.option_type,
            "strike":       leg.strike,
            "expiry_date":  leg.expiry_date.isoformat() if leg.expiry_date else None,
            "quantity":     leg.quantity,
            "entry_price":  float(leg.entry_price) if leg.entry_price is not None else None,
            "exit_price":   float(leg.exit_price)  if leg.exit_price  is not None else None,
            "gross_leg_pnl":float(leg.gross_leg_pnl) if leg.gross_leg_pnl is not None else None,
        }
        for leg in sorted(legs, key=lambda l: l.leg_index)
    ]

    # Spot series from MTM rows
    spot_series = [
        {
            "timestamp": row.timestamp.isoformat(),
            "close":     float(row.spot_close) if row.spot_close is not None else None,
        }
        for row in mtm_rows
    ]

    # MTM series
    mtm_series = [
        {
            "timestamp":        row.timestamp.isoformat(),
            "gross_mtm":        float(row.gross_mtm)          if row.gross_mtm          is not None else None,
            "est_exit_charges": float(row.est_exit_charges)   if row.est_exit_charges   is not None else None,
            "net_mtm":          float(row.net_mtm)            if row.net_mtm            is not None else None,
            "trail_stop_level": float(row.trail_stop_level)   if row.trail_stop_level   is not None else None,
            "event_code":       row.event_code,
        }
        for row in mtm_rows
    ]

    # Minute table (flat join: spot + all legs)
    leg_mtm_by_ts: Dict[str, Dict[str, Any]] = {}
    for lm in leg_mtm_rows:
        ts_key = lm.timestamp.isoformat()
        if ts_key not in leg_mtm_by_ts:
            leg_mtm_by_ts[ts_key] = {}
        leg_mtm_by_ts[ts_key][str(lm.leg_id)] = {
            "price":       float(lm.price) if lm.price is not None else None,
            "stale":       lm.stale_minutes,
        }

    leg_id_to_info = {str(l.id): l for l in legs}

    minute_table = []
    for row in mtm_rows:
        ts_key = row.timestamp.isoformat()
        leg_cols: Dict[str, Any] = {}
        for leg_id_str, ldata in leg_mtm_by_ts.get(ts_key, {}).items():
            leg = leg_id_to_info.get(leg_id_str)
            if leg:
                col = f"{leg.side.lower()}_{leg.option_type.lower()}_{leg.strike}"
                leg_cols[col] = ldata["price"]
                leg_cols[f"{col}_stale"] = ldata["stale"]

        minute_table.append({
            "timestamp":        ts_key,
            "spot_close":       float(row.spot_close) if row.spot_close is not None else None,
            "vix_close":        float(row.vix_close)  if row.vix_close  is not None else None,
            "gross_mtm":        float(row.gross_mtm)  if row.gross_mtm  is not None else None,
            "est_exit_charges": float(row.est_exit_charges) if row.est_exit_charges is not None else None,
            "net_mtm":          float(row.net_mtm)    if row.net_mtm    is not None else None,
            **leg_cols,
        })

    serialized_events = [
        {
            "timestamp":   ev.timestamp.isoformat(),
            "event_type":  ev.event_type,
            "reason_code": ev.reason_code,
            "reason_text": ev.reason_text,
            "payload":     ev.payload_json,
        }
        for ev in sorted(events, key=lambda e: e.timestamp)
    ]

    # Full-day spot series (09:15–15:30 for context charts)
    spot_series_full = []
    if spot_candles_full:
        spot_series_full = [
            {
                "timestamp": candle.timestamp.isoformat(),
                "close": float(candle.close) if candle.close is not None else None,
            }
            for candle in spot_candles_full
        ]

    # Entry credit total
    entry_credit_total = (
        float(run.entry_credit_total) if run.entry_credit_total is not None else None
    )

    return {
        "run": {
            "id":                  str(run.id),
            "strategy_id":         run.strategy_id,
            "instrument":          run.instrument,
            "trade_date":          run.trade_date.isoformat() if run.trade_date else None,
            "entry_time":          run.entry_time,
            "exit_time":           run.exit_time,
            "status":              run.status,
            "exit_reason":         run.exit_reason,
            "capital":             float(run.capital) if run.capital else None,
            "lots":                run.approved_lots,
            "lot_size":            run.lot_size,
            "entry_credit_per_unit": float(run.entry_credit_per_unit) if run.entry_credit_per_unit is not None else None,
            "entry_credit_total":  entry_credit_total,
            "gross_pnl":           float(run.gross_pnl) if run.gross_pnl is not None else None,
            "total_charges":       float(run.total_charges) if run.total_charges is not None else None,
            "realized_net_pnl":    float(run.realized_net_pnl) if run.realized_net_pnl is not None else None,
            "warnings":            (run.result_json or {}).get("warnings", []),
        },
        "legs":             serialized_legs,
        "spot_series":      spot_series,
        "spot_series_full": spot_series_full,
        "mtm_series":       mtm_series,
        "events":           serialized_events,
        "minute_table":     minute_table,
    }
