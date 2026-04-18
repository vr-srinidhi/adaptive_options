from datetime import time

from app.services.exit_engine import ExitEval, evaluate_exit


def _evaluate(**overrides) -> ExitEval:
    params = {
        "current_time": time(10, 30),
        "long_price": 62.0,
        "short_price": 30.0,
        "entry_debit": 30.0,
        "lot_size": 75,
        "approved_lots": 22,
        "total_max_loss": 49_500.0,
        "target_profit": 12_500.0,
        "estimated_charges": 250.0,
    }
    params.update(overrides)
    return evaluate_exit(
        **params,
    )


def test_trail_arms_on_peak_threshold():
    ev = _evaluate(
        long_price=62.31,
        short_price=30.0,
        trail_armed=False,
        peak_mtm=0.0,
    )
    assert ev.action == "HOLD"
    assert ev.trail_armed is True
    assert ev.peak_mtm == ev.total_mtm


def test_trail_exits_when_giveback_hit():
    ev = _evaluate(
        long_price=63.33,
        short_price=30.0,
        current_time=time(11, 0),
        trail_armed=True,
        peak_mtm=10_000.0,
    )
    assert ev.action == "EXIT_TRAIL"


def test_trail_does_not_fire_before_arming():
    ev = _evaluate(
        long_price=56.97,
        short_price=30.0,
        current_time=time(10, 0),
        trail_armed=False,
        peak_mtm=2_000.0,
    )
    assert ev.action == "HOLD"
    assert ev.trail_armed is False


def test_target_takes_precedence_over_trail():
    ev = _evaluate(
        long_price=70.0,
        short_price=30.0,
        current_time=time(12, 0),
        trail_armed=True,
        peak_mtm=13_000.0,
    )
    assert ev.action == "EXIT_TARGET"


def test_stop_takes_precedence_over_trail():
    ev = _evaluate(
        long_price=0.0,
        short_price=30.0,
        current_time=time(12, 0),
        trail_armed=True,
        peak_mtm=8_000.0,
    )
    assert ev.action == "EXIT_STOP"
