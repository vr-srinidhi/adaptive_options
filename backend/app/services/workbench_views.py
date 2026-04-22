from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from app.models.historical import SessionBatch, TradingDay
from app.models.paper_trade import (
    MinuteDecision,
    PaperCandleSeries,
    PaperSession,
    PaperTradeHeader,
    PaperTradeLeg,
    PaperTradeMinuteMark,
)
from app.services.strategy_config import WORKBENCH_STRATEGY_ID, WORKBENCH_STRATEGY_NAME


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def resolve_strategy_identity(
    snapshot: dict | None,
    *,
    fallback_id: str | None = None,
    fallback_name: str | None = None,
) -> tuple[str, str]:
    payload = snapshot or {}
    strategy_id = payload.get("strategy_id") or fallback_id or WORKBENCH_STRATEGY_ID
    strategy_name = payload.get("strategy_name") or fallback_name or WORKBENCH_STRATEGY_NAME
    return strategy_id, strategy_name


def serialize_strategy_metrics(trading_days: Iterable[TradingDay]) -> dict:
    rows = list(trading_days)
    ready_rows = [row for row in rows if row.backtest_ready]
    latest_ready_day = max((row.trade_date for row in ready_rows), default=None)
    return {
        "total_days": len(rows),
        "ready_days": len(ready_rows),
        "latest_ready_day": _iso_date(latest_ready_day),
        "ingestion_status_breakdown": dict(Counter(row.ingestion_status for row in rows)),
    }


def paper_session_library_item(session: PaperSession, trade: PaperTradeHeader | None = None) -> dict:
    pnl = session.summary_pnl
    if pnl is None and trade is not None:
        pnl = trade.realized_net_pnl

    strategy_id, strategy_name = resolve_strategy_identity(
        session.strategy_config_snapshot,
        fallback_id=WORKBENCH_STRATEGY_ID if session.session_type == "paper_replay" else None,
        fallback_name=WORKBENCH_STRATEGY_NAME,
    )
    title = f"{session.instrument} replay"
    subtitle = session.session_date.isoformat()
    return {
        "kind": "paper_session",
        "id": str(session.id),
        "title": title,
        "subtitle": subtitle,
        "status": session.status,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "strategy_version": (session.strategy_config_snapshot or {}).get("strategy_version") or (trade.strategy_version if trade else None),
        "instrument": session.instrument,
        "run_mode": "paper_replay",
        "date_label": session.session_date.isoformat(),
        "created_at": _iso_datetime(session.created_at),
        "pnl": _to_float(pnl),
        "summary": session.final_session_state or session.status,
        "metrics": {
            "decision_count": session.decision_count or 0,
            "capital": _to_float(session.capital),
            "trade_opened": trade is not None,
        },
        "route": f"/workbench/replay/paper_session/{session.id}",
        "legacy_route": f"/paper/session/{session.id}",
    }


def historical_batch_library_item(
    batch: SessionBatch,
    sessions_total: int | None = None,
    winning_sessions: int | None = None,
) -> dict:
    total = sessions_total if sessions_total is not None else batch.total_sessions
    wins = winning_sessions if winning_sessions is not None else 0
    win_rate = None
    if total:
        win_rate = round((wins / total) * 100, 1)

    strategy_id, strategy_name = resolve_strategy_identity(
        batch.strategy_config_snapshot,
        fallback_id=batch.strategy_id,
        fallback_name=WORKBENCH_STRATEGY_NAME,
    )
    return {
        "kind": "historical_batch",
        "id": str(batch.id),
        "title": batch.name,
        "subtitle": f"{batch.start_date.isoformat()} → {batch.end_date.isoformat()}",
        "status": batch.status,
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "strategy_version": (batch.strategy_config_snapshot or {}).get("strategy_version") or batch.strategy_version,
        "instrument": (batch.strategy_config_snapshot or {}).get("instrument"),
        "run_mode": "historical_backtest",
        "date_label": f"{batch.start_date.isoformat()} → {batch.end_date.isoformat()}",
        "created_at": _iso_datetime(batch.created_at),
        "pnl": _to_float(batch.total_pnl),
        "summary": f"{batch.completed_sessions}/{batch.total_sessions or total} sessions complete",
        "metrics": {
            "total_sessions": total,
            "completed_sessions": batch.completed_sessions,
            "failed_sessions": batch.failed_sessions,
            "skipped_sessions": batch.skipped_sessions,
            "win_rate": win_rate,
            "capital": _to_float((batch.strategy_config_snapshot or {}).get("capital")),
        },
        "route": f"/workbench/history/historical_batch/{batch.id}",
        "legacy_route": f"/backtests/{batch.id}",
    }


def serialize_trade(trade: PaperTradeHeader | None, legs: list[PaperTradeLeg] | None = None) -> dict | None:
    if trade is None:
        return None
    payload = {
        "id": str(trade.id),
        "session_id": str(trade.session_id),
        "entry_time": _iso_datetime(trade.entry_time),
        "exit_time": _iso_datetime(trade.exit_time),
        "bias": trade.bias,
        "expiry": _iso_date(trade.expiry),
        "lot_size": trade.lot_size,
        "approved_lots": trade.approved_lots,
        "entry_debit": _to_float(trade.entry_debit),
        "total_max_loss": _to_float(trade.total_max_loss),
        "target_profit": _to_float(trade.target_profit),
        "realized_gross_pnl": _to_float(trade.realized_gross_pnl),
        "realized_net_pnl": _to_float(trade.realized_net_pnl),
        "charges": _to_float(trade.charges),
        "charges_breakdown_json": trade.charges_breakdown_json,
        "strategy_name": trade.strategy_name,
        "strategy_version": trade.strategy_version,
        "strategy_params_json": trade.strategy_params_json,
        "risk_cap": _to_float(trade.risk_cap),
        "entry_reason_code": trade.entry_reason_code,
        "entry_reason_text": trade.entry_reason_text,
        "selection_method": trade.selection_method,
        "selected_candidate_rank": trade.selected_candidate_rank,
        "selected_candidate_score": _to_float(trade.selected_candidate_score),
        "selected_candidate_score_breakdown_json": trade.selected_candidate_score_breakdown_json,
        "status": trade.status,
        "exit_reason": trade.exit_reason,
        "long_strike": trade.long_strike,
        "short_strike": trade.short_strike,
        "option_type": trade.option_type,
    }
    if legs is not None:
        payload["legs"] = [
            {
                "leg_side": leg.leg_side,
                "option_type": leg.option_type,
                "strike": leg.strike,
                "expiry": _iso_date(leg.expiry),
                "entry_price": _to_float(leg.entry_price),
                "exit_price": _to_float(leg.exit_price),
            }
            for leg in legs
        ]
    return payload


def serialize_decision(decision: MinuteDecision) -> dict:
    return {
        "id": str(decision.id),
        "timestamp": _iso_datetime(decision.timestamp),
        "spot_close": _to_float(decision.spot_close),
        "opening_range_high": _to_float(decision.opening_range_high),
        "opening_range_low": _to_float(decision.opening_range_low),
        "trade_state": decision.trade_state,
        "signal_state": decision.signal_state,
        "action": decision.action,
        "reason_code": decision.reason_code,
        "reason_text": decision.reason_text,
        "candidate_structure": decision.candidate_structure,
        "computed_max_loss": _to_float(decision.computed_max_loss),
        "computed_target": _to_float(decision.computed_target),
        "session_state": decision.session_state,
        "signal_substate": decision.signal_substate,
        "rejection_gate": decision.rejection_gate,
        "price_freshness_json": decision.price_freshness_json,
        "candidate_ranking_json": decision.candidate_ranking_json,
        "selected_candidate_rank": decision.selected_candidate_rank,
        "selected_candidate_score": _to_float(decision.selected_candidate_score),
        "selected_candidate_score_breakdown_json": decision.selected_candidate_score_breakdown_json,
    }


def serialize_mark(mark: PaperTradeMinuteMark) -> dict:
    return {
        "timestamp": _iso_datetime(mark.timestamp),
        "long_leg_price": _to_float(mark.long_leg_price),
        "short_leg_price": _to_float(mark.short_leg_price),
        "current_spread_value": _to_float(mark.current_spread_value),
        "mtm_per_lot": _to_float(mark.mtm_per_lot),
        "total_mtm": _to_float(mark.total_mtm),
        "distance_to_target": _to_float(mark.distance_to_target),
        "distance_to_stop": _to_float(mark.distance_to_stop),
        "action": mark.action,
        "reason": mark.reason,
        "gross_mtm": _to_float(mark.gross_mtm),
        "estimated_exit_charges": _to_float(mark.estimated_exit_charges),
        "estimated_net_mtm": _to_float(mark.estimated_net_mtm),
        "price_freshness_json": mark.price_freshness_json,
    }


def serialize_candle_series(series: PaperCandleSeries) -> dict:
    return {
        "series_type": series.series_type,
        "candles": series.candles,
    }


def replay_payload(
    *,
    session: PaperSession,
    trade: PaperTradeHeader | None,
    decisions: list[MinuteDecision],
    marks: list[PaperTradeMinuteMark],
    candle_series: list[PaperCandleSeries] | None = None,
    legs: list[PaperTradeLeg] | None = None,
    kind: str,
) -> dict:
    decision_counter = Counter()
    reason_counter = Counter()
    for row in decisions:
        if row.action:
            decision_counter[row.action] += 1
        if row.reason_code:
            reason_counter[row.reason_code] += 1

    pnl = session.summary_pnl
    if pnl is None and trade is not None:
        pnl = trade.realized_net_pnl

    return {
        "kind": kind,
        "session": {
            "id": str(session.id),
            "instrument": session.instrument,
            "session_date": session.session_date.isoformat(),
            "capital": _to_float(session.capital),
            "status": session.status,
            "final_session_state": session.final_session_state,
            "session_type": session.session_type,
            "execution_mode": session.execution_mode,
            "source_mode": session.source_mode,
            "summary_pnl": _to_float(pnl),
            "decision_count": session.decision_count,
            "created_at": _iso_datetime(session.created_at),
            "strategy_config_snapshot": session.strategy_config_snapshot,
            "error_message": session.error_message,
            "batch_id": str(session.batch_id) if session.batch_id else None,
        },
        "trade": serialize_trade(trade, legs=legs),
        "decisions": [serialize_decision(item) for item in decisions],
        "marks": [serialize_mark(item) for item in marks],
        "candle_series": [serialize_candle_series(item) for item in (candle_series or [])],
        "explainability": {
            "action_counts": dict(decision_counter),
            "reason_code_counts": dict(reason_counter),
            "entry_reason": trade.entry_reason_text if trade else None,
            "exit_reason": trade.exit_reason if trade else None,
            "no_trade_reason": next(
                (
                    row.reason_text
                    for row in decisions
                    if row.action == "NO_TRADE" and row.reason_text
                ),
                None,
            ),
        },
    }


def parse_compare_refs(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    refs = []
    for chunk in raw.split(","):
        value = chunk.strip()
        if not value:
            continue
        kind, sep, item_id = value.partition(":")
        if not sep or not kind or not item_id:
            raise ValueError(f"Invalid compare ref: {value}")
        refs.append((kind, item_id))
    return refs
