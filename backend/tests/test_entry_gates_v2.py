from datetime import date, datetime, time

from app.services.entry_gates import _g0_entry_cutoff, evaluate_gates


def _make_candle(ts: datetime, close: float = 22130.0) -> dict:
    return {
        "date": ts,
        "open": close - 1,
        "high": close + 1,
        "low": close - 2,
        "close": close,
        "volume": 100,
    }


def test_g0_blocks_entries_after_cutoff():
    result = _g0_entry_cutoff(
        now=datetime(2026, 4, 7, 13, 0),
        cutoff=time(13, 0),
    )
    assert result is not None
    assert result.action == "NO_TRADE"
    assert result.reason_code == "TOO_LATE_TO_ENTER"
    assert result.rejection_gate == "G0"


def test_g0_allows_entries_before_cutoff():
    assert _g0_entry_cutoff(
        now=datetime(2026, 4, 7, 12, 59),
        cutoff=time(13, 0),
    ) is None


def test_evaluate_gates_checks_cutoff_before_or_ready():
    result = evaluate_gates(
        candle=_make_candle(datetime(2026, 4, 7, 13, 5)),
        or_high=22100.0,
        or_low=21900.0,
        or_ready=False,
        has_open_trade=False,
        option_prices={},
        instrument="NIFTY",
        capital=2_500_000.0,
        expiry=date(2026, 4, 9),
        current_time=time(13, 5),
    )
    assert result.action == "NO_TRADE"
    assert result.reason_code == "TOO_LATE_TO_ENTER"
    assert result.rejection_gate == "G0"
