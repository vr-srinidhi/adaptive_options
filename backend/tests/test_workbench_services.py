from datetime import date, datetime
from types import SimpleNamespace

from app.services.workbench_catalog import get_strategy, list_strategies, supported_strategy_ids
from app.services.workbench_views import (
    historical_batch_library_item,
    paper_session_library_item,
    parse_compare_refs,
)


def test_strategy_catalog_contains_orb_executor():
    strategies = list_strategies()
    orb = next(item for item in strategies if item["id"] == "orb_intraday_spread")
    assert orb["status"] == "available"
    assert "paper_replay" in orb["modes"]
    assert "historical_backtest" in orb["modes"]


def test_supported_strategy_ids_only_exposes_available_entries():
    supported = supported_strategy_ids()
    assert "orb_intraday_spread" in supported
    assert "buy_call" not in supported


def test_get_strategy_returns_copy_not_shared_reference():
    strategy = get_strategy("orb_intraday_spread")
    strategy["name"] = "Changed"
    assert get_strategy("orb_intraday_spread")["name"] == "Opening Range Spread"


def test_parse_compare_refs_accepts_valid_csv_refs():
    refs = parse_compare_refs("paper_session:123,historical_batch:456")
    assert refs == [("paper_session", "123"), ("historical_batch", "456")]


def test_parse_compare_refs_rejects_invalid_shapes():
    try:
        parse_compare_refs("paper_session-only")
    except ValueError as exc:
        assert "Invalid compare ref" in str(exc)
    else:
        raise AssertionError("Expected invalid compare refs to raise ValueError")


def test_paper_session_library_item_normalizes_session_fields():
    session = SimpleNamespace(
        id="sess-1",
        instrument="NIFTY",
        session_date=date(2026, 4, 7),
        summary_pnl=None,
        capital=2500000,
        status="COMPLETED",
        final_session_state="TRADE_CLOSED",
        decision_count=42,
        created_at=datetime(2026, 4, 7, 15, 30),
    )
    trade = SimpleNamespace(realized_net_pnl=1250.5, strategy_version="v1.0")

    item = paper_session_library_item(session, trade)

    assert item["kind"] == "paper_session"
    assert item["pnl"] == 1250.5
    assert item["metrics"]["trade_opened"] is True
    assert item["route"].endswith("/paper_session/sess-1")


def test_historical_batch_library_item_adds_win_rate_when_session_count_known():
    batch = SimpleNamespace(
        id="batch-1",
        name="Jan replay",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status="completed",
        strategy_id="orb_intraday_spread",
        strategy_version="v1.0",
        strategy_config_snapshot={"instrument": "NIFTY", "capital": 2500000},
        created_at=datetime(2026, 2, 1, 10, 0),
        total_pnl=10000,
        completed_sessions=19,
        total_sessions=20,
        failed_sessions=1,
        skipped_sessions=0,
    )

    item = historical_batch_library_item(batch, sessions_total=20, winning_sessions=12)

    assert item["kind"] == "historical_batch"
    assert item["metrics"]["win_rate"] == 60.0
    assert item["route"].endswith("/historical_batch/batch-1")
