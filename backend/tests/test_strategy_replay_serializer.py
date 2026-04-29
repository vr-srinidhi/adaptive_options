"""
Tests for strategy_replay_serializer.py

Covers:
- CE/PE MTM grouping (SELL and BUY formula)
- MFE / MAE / max_drawdown computation
- VIX forward-fill + data-quality warnings
- legs table now includes lots and lot_size
- Regression: existing payload keys are all present
"""
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.strategy_replay_serializer import (
    strategy_run_library_item,
    strategy_run_replay_payload,
)


# ── Minimal ORM-like stubs ────────────────────────────────────────────────────

def _run(**kwargs):
    defaults = dict(
        id=uuid4(),
        strategy_id="short_straddle",
        instrument="NIFTY",
        trade_date=date(2026, 4, 7),
        entry_time="09:50",
        exit_time="10:30",
        status="completed",
        exit_reason="TRAIL_EXIT",
        capital=Decimal("500000"),
        lot_size=75,
        approved_lots=2,
        entry_credit_per_unit=Decimal("200"),
        entry_credit_total=Decimal("30000"),
        gross_pnl=Decimal("12000"),
        total_charges=Decimal("1200"),
        realized_net_pnl=Decimal("10800"),
        config_json={},
        result_json={"warnings": []},
        created_at=datetime(2026, 4, 7, 10, 30),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _leg(leg_index, side, option_type, strike, entry_price, exit_price, gross_leg_pnl):
    return SimpleNamespace(
        id=uuid4(),
        leg_index=leg_index,
        side=side,
        option_type=option_type,
        strike=strike,
        expiry_date=date(2026, 4, 10),
        quantity=150,
        entry_price=Decimal(str(entry_price)),
        exit_price=Decimal(str(exit_price)),
        gross_leg_pnl=Decimal(str(gross_leg_pnl)),
    )


def _mtm(ts_str, net_mtm, gross_mtm=None, trail=None, vix=None, event_code=None):
    ts = datetime.fromisoformat(ts_str)
    return SimpleNamespace(
        timestamp=ts,
        spot_close=Decimal("23000"),
        vix_close=Decimal(str(vix)) if vix is not None else None,
        gross_mtm=Decimal(str(gross_mtm or net_mtm)),
        est_exit_charges=Decimal("200"),
        net_mtm=Decimal(str(net_mtm)),
        trail_stop_level=Decimal(str(trail)) if trail is not None else None,
        event_code=event_code,
    )


def _leg_mtm(leg_id, ts_str, price, gross_leg_pnl):
    return SimpleNamespace(
        leg_id=leg_id,
        timestamp=datetime.fromisoformat(ts_str),
        price=Decimal(str(price)),
        gross_leg_pnl=Decimal(str(gross_leg_pnl)),
        stale_minutes=0,
    )


def _event(ts_str, event_type, reason_code=None, reason_text=None):
    return SimpleNamespace(
        timestamp=datetime.fromisoformat(ts_str),
        event_type=event_type,
        reason_code=reason_code,
        reason_text=reason_text,
        payload_json=None,
    )


def _vix_candle(ts_str, close):
    return SimpleNamespace(
        timestamp=datetime.fromisoformat(ts_str),
        close=Decimal(str(close)),
    )


def _spot_candle(ts_str, close, open=None, high=None, low=None):
    return SimpleNamespace(
        timestamp=datetime.fromisoformat(ts_str),
        open=Decimal(str(open  if open  is not None else close)),
        high=Decimal(str(high  if high  is not None else close)),
        low=Decimal(str(low   if low   is not None else close)),
        close=Decimal(str(close)),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_payload(legs=None, mtm_rows=None, leg_mtm_rows=None, events=None, **run_kwargs):
    run = _run(**run_kwargs)
    legs = legs or []
    mtm_rows = mtm_rows or []
    leg_mtm_rows = leg_mtm_rows or []
    events = events or []
    return strategy_run_replay_payload(run, legs, mtm_rows, leg_mtm_rows, events)


# ── CE/PE MTM grouping tests ──────────────────────────────────────────────────

def test_ce_pe_mtm_grouped_per_minute():
    """CE and PE gross_leg_pnl rows are summed per minute into ce_mtm / pe_mtm."""
    ce = _leg(0, "SELL", "CE", 23000, 100, 82, 2700)
    pe = _leg(1, "SELL", "PE", 23000, 80,  65, 2250)

    ts = "2026-04-07T09:50:00"
    mtm_rows = [_mtm(ts, net_mtm=4950)]
    leg_mtm_rows = [
        _leg_mtm(ce.id, ts, 82, 2700),
        _leg_mtm(pe.id, ts, 65, 2250),
    ]
    payload = strategy_run_replay_payload(_run(), [ce, pe], mtm_rows, leg_mtm_rows, [])

    row = payload["mtm_series"][0]
    assert abs(row["ce_mtm"] - 2700) < 0.01, f"ce_mtm wrong: {row['ce_mtm']}"
    assert abs(row["pe_mtm"] - 2250) < 0.01, f"pe_mtm wrong: {row['pe_mtm']}"


def test_ce_pe_mtm_null_when_no_leg_data():
    """Rows without leg_mtm data return null ce_mtm and pe_mtm."""
    ts = "2026-04-07T09:50:00"
    mtm_rows = [_mtm(ts, net_mtm=1000)]
    payload = strategy_run_replay_payload(_run(), [], mtm_rows, [], [])
    row = payload["mtm_series"][0]
    assert row["ce_mtm"] is None
    assert row["pe_mtm"] is None


def test_multiple_ce_legs_summed():
    """When two CE legs exist (e.g., bear call spread), their pnl is summed."""
    ce1 = _leg(0, "SELL", "CE", 23100, 80, 60, 3000)
    ce2 = _leg(1, "BUY",  "CE", 23200, 40, 28, 1800)
    ts = "2026-04-07T09:50:00"
    mtm_rows = [_mtm(ts, net_mtm=4800)]
    leg_mtm_rows = [
        _leg_mtm(ce1.id, ts, 60, 3000),
        _leg_mtm(ce2.id, ts, 28, 1800),
    ]
    payload = strategy_run_replay_payload(_run(), [ce1, ce2], mtm_rows, leg_mtm_rows, [])
    row = payload["mtm_series"][0]
    assert abs(row["ce_mtm"] - 4800) < 0.01  # 3000 + 1800


# ── MFE / MAE / max_drawdown tests ───────────────────────────────────────────

def test_mfe_mae_computed_from_net_mtm():
    ts_base = "2026-04-07T09:5"
    mtm_rows = [
        _mtm(f"{ts_base}0:00", net_mtm=1000),
        _mtm(f"{ts_base}1:00", net_mtm=5000),
        _mtm(f"{ts_base}2:00", net_mtm=-2000),
        _mtm(f"{ts_base}3:00", net_mtm=3000),
    ]
    payload = strategy_run_replay_payload(_run(), [], mtm_rows, [], [])
    run = payload["run"]
    assert run["mfe"] == 5000.0, f"mfe={run['mfe']}"
    assert run["mae"] == -2000.0, f"mae={run['mae']}"


def test_max_drawdown_worst_decline_from_peak():
    """Max drawdown is peak-to-trough, not just the minimum value."""
    ts_base = "2026-04-07T09:5"
    mtm_rows = [
        _mtm(f"{ts_base}0:00", net_mtm=0),
        _mtm(f"{ts_base}1:00", net_mtm=8000),   # peak
        _mtm(f"{ts_base}2:00", net_mtm=3000),   # drawdown = -5000
        _mtm(f"{ts_base}3:00", net_mtm=9000),   # new peak
        _mtm(f"{ts_base}4:00", net_mtm=4500),   # drawdown = -4500
    ]
    payload = strategy_run_replay_payload(_run(), [], mtm_rows, [], [])
    run = payload["run"]
    # Worst drawdown starts at peak 8000 and drops to 3000 → -5000
    assert run["max_drawdown"] == -5000.0, f"max_drawdown={run['max_drawdown']}"


def test_mfe_mae_none_when_no_mtm_rows():
    payload = strategy_run_replay_payload(_run(), [], [], [], [])
    run = payload["run"]
    assert run["mfe"] is None
    assert run["mae"] is None
    assert run["max_drawdown"] is None


# ── VIX series tests ──────────────────────────────────────────────────────────

def test_vix_series_full_actual_source():
    spot_candles = [_spot_candle("2026-04-07T09:15:00", 23000)]
    vix_candles  = [_vix_candle("2026-04-07T09:15:00", 16.5)]
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=spot_candles,
        vix_candles_full=vix_candles,
    )
    row = payload["vix_series_full"][0]
    assert row["vix_close"] == 16.5
    assert row["vix_source"] == "actual"


def test_vix_forward_filled_when_gap():
    spot_candles = [
        _spot_candle("2026-04-07T09:15:00", 23000),
        _spot_candle("2026-04-07T09:16:00", 23010),
    ]
    vix_candles = [_vix_candle("2026-04-07T09:15:00", 17.0)]  # only first minute
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=spot_candles,
        vix_candles_full=vix_candles,
    )
    rows = payload["vix_series_full"]
    assert rows[0]["vix_source"] == "actual"
    assert rows[1]["vix_source"] == "forward_filled"
    assert rows[1]["vix_close"] == 17.0


def test_vix_missing_source_when_no_vix_data():
    spot_candles = [_spot_candle("2026-04-07T09:15:00", 23000)]
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=spot_candles,
        vix_candles_full=[],
    )
    rows = payload["vix_series_full"]
    assert rows[0]["vix_source"] == "missing"
    assert rows[0]["vix_close"] is None


def test_data_quality_warning_for_missing_vix():
    spot_candles = [_spot_candle("2026-04-07T09:15:00", 23000)]
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=spot_candles,
        vix_candles_full=[],
    )
    assert any(w["type"] == "missing_vix" for w in payload["data_quality"])


def test_data_quality_warning_for_forward_filled_vix():
    spot_candles = [
        _spot_candle("2026-04-07T09:15:00", 23000),
        _spot_candle("2026-04-07T09:16:00", 23010),
    ]
    vix_candles = [_vix_candle("2026-04-07T09:15:00", 17.0)]
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=spot_candles,
        vix_candles_full=vix_candles,
    )
    assert any(w["type"] == "forward_filled_vix" for w in payload["data_quality"])


# ── Spot series OHLC ─────────────────────────────────────────────────────────

def test_spot_series_full_includes_ohlc():
    """spot_series_full must carry open/high/low/close so CSV NIFTY columns are populated."""
    candle = _spot_candle("2026-04-07T09:15:00", close=23000, open=22980, high=23020, low=22970)
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=[candle],
    )
    row = payload["spot_series_full"][0]
    assert row["open"]  == 22980.0
    assert row["high"]  == 23020.0
    assert row["low"]   == 22970.0
    assert row["close"] == 23000.0


# ── Legs table regression tests ───────────────────────────────────────────────

def test_legs_include_lots_and_lot_size():
    run = _run(approved_lots=3, lot_size=75)
    leg = _leg(0, "SELL", "CE", 23000, 100, 80, 3000)
    payload = strategy_run_replay_payload(run, [leg], [], [], [])
    leg_row = payload["legs"][0]
    assert leg_row["lots"] == 3
    assert leg_row["lot_size"] == 75
    assert leg_row["quantity"] == 150  # from _leg fixture


def test_legs_include_expiry_date():
    leg = _leg(0, "SELL", "CE", 23000, 100, 80, 3000)
    payload = strategy_run_replay_payload(_run(), [leg], [], [], [])
    assert payload["legs"][0]["expiry_date"] == "2026-04-10"


# ── Payload shape regression ──────────────────────────────────────────────────

def test_all_top_level_keys_present():
    payload = strategy_run_replay_payload(_run(), [], [], [], [])
    expected_keys = {
        "run", "legs", "spot_series", "spot_series_full", "vix_series_full",
        "mtm_series", "shadow_mtm_series", "leg_candles", "events", "minute_table",
        "data_quality",
    }
    assert expected_keys <= set(payload.keys()), f"missing keys: {expected_keys - set(payload.keys())}"


def test_run_summary_includes_mfe_mae_drawdown():
    payload = strategy_run_replay_payload(_run(), [], [], [], [])
    run = payload["run"]
    assert "mfe" in run
    assert "mae" in run
    assert "max_drawdown" in run


def test_mtm_series_includes_ce_pe_mtm_keys():
    ts = "2026-04-07T09:50:00"
    payload = strategy_run_replay_payload(_run(), [], [_mtm(ts, net_mtm=1000)], [], [])
    row = payload["mtm_series"][0]
    assert "ce_mtm" in row
    assert "pe_mtm" in row


# ── vix_series_full regression — must survive CSV section write ──────────────

def test_vix_series_full_present_in_payload():
    """vix_series_full must be present so the CSV India VIX section can be written."""
    spot_candles = [_spot_candle("2026-04-07T09:15:00", 23000)]
    vix_candles  = [_vix_candle("2026-04-07T09:15:00", 15.5)]
    payload = strategy_run_replay_payload(
        _run(), [], [], [], [],
        spot_candles_full=spot_candles,
        vix_candles_full=vix_candles,
    )
    assert "vix_series_full" in payload
    row = payload["vix_series_full"][0]
    assert row["vix_close"] == 15.5
    assert row["vix_source"] == "actual"


# ── Library item ──────────────────────────────────────────────────────────────

def test_strategy_run_library_item_shape():
    run = _run()
    item = strategy_run_library_item(run)
    assert item["kind"] == "strategy_run"
    assert item["strategy_id"] == "short_straddle"
    assert "route" in item
    assert item["route"].startswith("/workbench/replay/strategy_run/")
