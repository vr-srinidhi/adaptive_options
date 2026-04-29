"""
Strategy replay serializer.

Reads persisted strategy_runs + related tables and shapes them into the
PRD §13 replay payload consumed by the frontend ReplayAnalyzer.

Public API
----------
strategy_run_library_item(run)                                          → dict
strategy_run_replay_payload(run, legs, mtm, leg_mtm, events, ...)       → dict
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
    shadow_mtm_rows: Optional[List[Dict[str, Any]]] = None,
    vix_candles_full: Optional[List] = None,
    leg_candles: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Full replay payload per PRD §13."""

    sorted_legs = sorted(legs, key=lambda l: l.leg_index)

    serialized_legs = [
        {
            "leg_index":    leg.leg_index,
            "side":         leg.side,
            "option_type":  leg.option_type,
            "strike":       leg.strike,
            "expiry_date":  leg.expiry_date.isoformat() if leg.expiry_date else None,
            "quantity":     leg.quantity,
            "lots":         run.approved_lots,
            "lot_size":     run.lot_size,
            "entry_price":  float(leg.entry_price) if leg.entry_price is not None else None,
            "exit_price":   float(leg.exit_price)  if leg.exit_price  is not None else None,
            "gross_leg_pnl":float(leg.gross_leg_pnl) if leg.gross_leg_pnl is not None else None,
        }
        for leg in sorted_legs
    ]

    # ── CE/PE MTM grouping ──────────────────────────────────────────────────
    # Map leg_id → option_type so we can group leg_mtm_rows without extra queries.
    leg_id_to_type: Dict[str, str] = {str(l.id): l.option_type for l in legs}

    # {timestamp_iso → {"CE": sum_pnl, "PE": sum_pnl}}
    leg_pnl_by_ts: Dict[str, Dict[str, float]] = {}
    for lm in leg_mtm_rows:
        ts_key = lm.timestamp.isoformat()
        ot = leg_id_to_type.get(str(lm.leg_id))
        if ot and lm.gross_leg_pnl is not None:
            bucket = leg_pnl_by_ts.setdefault(ts_key, {})
            bucket[ot] = bucket.get(ot, 0.0) + float(lm.gross_leg_pnl)

    # Spot series from MTM rows (trade window)
    spot_series = [
        {
            "timestamp": row.timestamp.isoformat(),
            "close":     float(row.spot_close) if row.spot_close is not None else None,
        }
        for row in mtm_rows
    ]

    # MTM series — includes per-leg CE/PE split
    mtm_series = []
    for row in mtm_rows:
        ts_key = row.timestamp.isoformat()
        leg_bucket = leg_pnl_by_ts.get(ts_key, {})
        mtm_series.append({
            "timestamp":        ts_key,
            "gross_mtm":        float(row.gross_mtm)          if row.gross_mtm          is not None else None,
            "est_exit_charges": float(row.est_exit_charges)   if row.est_exit_charges   is not None else None,
            "net_mtm":          float(row.net_mtm)            if row.net_mtm            is not None else None,
            "trail_stop_level": float(row.trail_stop_level)   if row.trail_stop_level   is not None else None,
            "event_code":       row.event_code,
            "ce_mtm":           leg_bucket.get("CE"),
            "pe_mtm":           leg_bucket.get("PE"),
        })

    # Minute table (flat join: spot + all legs)
    leg_mtm_by_ts: Dict[str, Dict[str, Any]] = {}
    for lm in leg_mtm_rows:
        ts_key = lm.timestamp.isoformat()
        if ts_key not in leg_mtm_by_ts:
            leg_mtm_by_ts[ts_key] = {}
        leg_mtm_by_ts[ts_key][str(lm.leg_id)] = {
            "price":  float(lm.price) if lm.price is not None else None,
            "stale":  lm.stale_minutes,
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

    # ── Full-day spot series ────────────────────────────────────────────────
    spot_series_full = []
    if spot_candles_full:
        spot_series_full = [
            {
                "timestamp": candle.timestamp.isoformat(),
                "open":  float(candle.open)  if candle.open  is not None else None,
                "high":  float(candle.high)  if candle.high  is not None else None,
                "low":   float(candle.low)   if candle.low   is not None else None,
                "close": float(candle.close) if candle.close is not None else None,
            }
            for candle in spot_candles_full
        ]

    # ── Full-day VIX series ─────────────────────────────────────────────────
    # Forward-fill gaps and tag each row with vix_source.
    vix_series_full: List[Dict[str, Any]] = []
    if vix_candles_full:
        last_vix: Optional[float] = None
        # Build lookup by minute truncated to isoformat
        vix_by_ts: Dict[str, float] = {}
        for vc in vix_candles_full:
            vix_by_ts[vc.timestamp.isoformat()] = float(vc.close) if vc.close is not None else None

        # Walk full-day backbone using spot_series_full timestamps (or vix candle timestamps)
        all_ts = sorted({c.timestamp.isoformat() for c in spot_candles_full} if spot_candles_full else vix_by_ts.keys())
        for ts_key in all_ts:
            raw = vix_by_ts.get(ts_key)
            if raw is not None:
                last_vix = raw
                source = "actual"
            elif last_vix is not None:
                source = "forward_filled"
            else:
                source = "missing"
            vix_series_full.append({
                "timestamp":  ts_key,
                "vix_close":  last_vix,
                "vix_source": source,
            })
    elif spot_candles_full:
        # No VIX data at all — emit missing markers
        for candle in spot_candles_full:
            vix_series_full.append({
                "timestamp":  candle.timestamp.isoformat(),
                "vix_close":  None,
                "vix_source": "missing",
            })

    # ── Leg candles (option OHLC per leg for premium charts) ───────────────
    # leg_candles is pre-built in workbench.py as {leg_index_str: [...rows]}
    serialized_leg_candles = leg_candles or {}

    # ── MFE / MAE / max_drawdown ────────────────────────────────────────────
    net_mtm_values = [
        float(r.net_mtm) for r in mtm_rows if r.net_mtm is not None
    ]
    mfe = max(net_mtm_values) if net_mtm_values else None
    mae = min(net_mtm_values) if net_mtm_values else None

    # Max drawdown: worst decline from any running peak
    max_drawdown: Optional[float] = None
    if net_mtm_values:
        peak = net_mtm_values[0]
        worst_dd = 0.0
        for v in net_mtm_values:
            if v > peak:
                peak = v
            dd = v - peak
            if dd < worst_dd:
                worst_dd = dd
        max_drawdown = worst_dd if worst_dd < 0 else None

    # Entry credit total
    entry_credit_total = (
        float(run.entry_credit_total) if run.entry_credit_total is not None else None
    )

    # Data quality warnings
    missing_vix_count = sum(1 for r in vix_series_full if r["vix_source"] == "missing")
    fwd_fill_vix_count = sum(1 for r in vix_series_full if r["vix_source"] == "forward_filled")
    data_quality: List[Dict[str, Any]] = []
    if missing_vix_count:
        data_quality.append({
            "type":    "missing_vix",
            "message": f"VIX unavailable for {missing_vix_count} minute(s).",
        })
    if fwd_fill_vix_count:
        data_quality.append({
            "type":    "forward_filled_vix",
            "message": f"VIX forward-filled for {fwd_fill_vix_count} minute(s).",
        })

    return {
        "run": {
            "id":                    str(run.id),
            "strategy_id":           run.strategy_id,
            "instrument":            run.instrument,
            "trade_date":            run.trade_date.isoformat() if run.trade_date else None,
            "entry_time":            run.entry_time,
            "exit_time":             run.exit_time,
            "status":                run.status,
            "exit_reason":           run.exit_reason,
            "capital":               float(run.capital) if run.capital else None,
            "lots":                  run.approved_lots,
            "lot_size":              run.lot_size,
            "entry_credit_per_unit": float(run.entry_credit_per_unit) if run.entry_credit_per_unit is not None else None,
            "entry_credit_total":    entry_credit_total,
            "gross_pnl":             float(run.gross_pnl) if run.gross_pnl is not None else None,
            "total_charges":         float(run.total_charges) if run.total_charges is not None else None,
            "realized_net_pnl":      float(run.realized_net_pnl) if run.realized_net_pnl is not None else None,
            "mfe":                   mfe,
            "mae":                   mae,
            "max_drawdown":          max_drawdown,
            "warnings":              (run.result_json or {}).get("warnings", []),
        },
        "legs":              serialized_legs,
        "spot_series":       spot_series,
        "spot_series_full":  spot_series_full,
        "vix_series_full":   vix_series_full,
        "mtm_series":        mtm_series,
        "shadow_mtm_series": shadow_mtm_rows or [],
        "leg_candles":       serialized_leg_candles,
        "events":            serialized_events,
        "minute_table":      minute_table,
        "data_quality":      data_quality,
    }
