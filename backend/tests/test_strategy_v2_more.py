import pandas as pd

from app.services import strategy


def _df(close=100.0, rsi=55.0, ema9=101.0, ema21=100.0, rows=40, **last):
    data = []
    for i in range(rows):
        data.append({
            "open": float(close) - 0.5,
            "high": float(close) + 1,
            "low": float(close) - 1,
            "close": float(close),
            "ema9": float(ema9),
            "ema21": float(ema21),
            "rsi": float(rsi),
            "atr14": 1.0,
            "ema_cross_change": 0,
        })
    data[-1].update(last)
    return pd.DataFrame(data)


def test_detect_regime_covers_major_branches():
    assert strategy.detect_regime(_df(rows=10), 5) == "INITIALIZING"
    assert strategy.detect_regime(_df(rsi=float("nan")), 35) == "INITIALIZING"
    panic = _df(close=95, rsi=20, ema9=94, ema21=100)
    panic.loc[30:35, "open"] = 96
    panic.loc[35, "high"] = 100
    panic.loc[35, "low"] = 90
    assert strategy.detect_regime(panic, 35) == "PANIC_SELL"
    assert strategy.detect_regime(_df(close=103, rsi=80, ema9=104, ema21=100), 35) == "OVERBOUGHT_REVERSAL"
    assert strategy.detect_regime(_df(close=97, rsi=20, ema9=96, ema21=100), 35) == "OVERSOLD_REVERSAL"

    choppy = _df()
    choppy.loc[5:35, "ema_cross_change"] = [1, -1] * 15 + [1]
    assert strategy.detect_regime(choppy, 35) == "CHOPPY"

    bottoming = _df(close=100, rsi=40, ema9=100, ema21=100)
    bottoming.loc[20:25, "rsi"] = 25
    bottoming.loc[35, ["high", "low"]] = [100.2, 99.8]
    assert strategy.detect_regime(bottoming, 35) == "BOTTOMING"
    consolidation = _df(close=100, rsi=50, ema9=100.01, ema21=100)
    consolidation.loc[:, "high"] = 100.1
    consolidation.loc[:, "low"] = 99.9
    assert strategy.detect_regime(consolidation, 35) == "CONSOLIDATION"
    regime_breakout_up = _df(close=110, rsi=60, ema9=109, ema21=100)
    regime_breakout_up.loc[:, "high"] = 110
    regime_breakout_up.loc[:, "low"] = 108
    assert strategy.detect_regime(regime_breakout_up, 35) == "BREAKOUT_UP"
    regime_breakout_down = _df(close=90, rsi=40, ema9=90, ema21=100)
    regime_breakout_down.loc[:, "high"] = 92
    regime_breakout_down.loc[:, "low"] = 90
    assert strategy.detect_regime(regime_breakout_down, 35) == "BREAKOUT_DOWN"
    assert strategy.regime_to_simple("PANIC_SELL") == "BEARISH"


def test_scan_signals_and_strategy_mapping_cover_signal_types():
    reversal_long = _df(close=100, rsi=35, ema9=101, ema21=100)
    reversal_long.loc[20:25, "rsi"] = 25
    reversal_long.loc[35, ["open", "high", "low", "ema_cross_change"]] = [98, 101, 98.5, 1]
    reversal_long.loc[30, "low"] = 98
    reversal_long.loc[34, "low"] = 98.1
    signals = strategy.scan_signals(reversal_long, 35, "BOTTOMING")
    assert signals[0]["type"] == "REVERSAL_LONG"

    reversal_short = _df(close=100, rsi=65, ema9=99, ema21=100)
    reversal_short.loc[20:25, "rsi"] = 75
    reversal_short.loc[35, ["open", "ema_cross_change"]] = [102, -1]
    reversal_short.loc[30, "high"] = 102
    reversal_short.loc[34, "high"] = 102.1
    assert strategy.scan_signals(reversal_short, 35, "OVERBOUGHT_REVERSAL")[0]["type"] == "REVERSAL_SHORT"

    breakout_up = _df(close=110, rsi=60, ema9=111, ema21=100)
    breakout_up.loc[:, "high"] = 110
    assert strategy.scan_signals(breakout_up, 35, "BREAKOUT_UP")[0]["type"] == "BREAKOUT_LONG"
    breakout_down = _df(close=90, rsi=40, ema9=89, ema21=100)
    breakout_down.loc[:, "low"] = 90
    breakout_down.loc[35, "open"] = 91
    assert strategy.scan_signals(breakout_down, 35, "BREAKOUT_DOWN")[0]["type"] == "BREAKOUT_SHORT"
    assert strategy.scan_signals(_df(close=100.2, rsi=50, ema9=101, ema21=100, open=100), 35, "TRENDING_UP")[0]["type"] == "TREND_CONTINUATION_LONG"
    assert strategy.scan_signals(_df(close=99.8, rsi=50, ema9=99, ema21=100, open=100), 35, "TRENDING_DOWN")[0]["type"] == "TREND_CONTINUATION_SHORT"

    range_signals = strategy.scan_signals(_df(close=100, rsi=50, ema9=100, ema21=100), 35, "CONSOLIDATION")
    assert {s["type"] for s in range_signals} >= {"PREMIUM_SELL_BULLISH", "PREMIUM_SELL_BEARISH", "PREMIUM_SELL_RANGE"}
    assert strategy._signal_to_strategy("REVERSAL_LONG", 60) == "BULL_CALL_SPREAD"
    assert strategy._signal_to_strategy("REVERSAL_SHORT", 20) == "LONG_PE"
    assert strategy._signal_to_strategy("UNKNOWN", 99) == "NO_TRADE"


def test_select_strategy_v2_and_build_legs_cover_trade_and_no_trade(monkeypatch):
    monkeypatch.setattr(strategy, "detect_regime", lambda _df, _idx: "PANIC_SELL")
    assert strategy.select_strategy_v2(_df(), 35, 50) == ("PANIC_SELL", "NO_TRADE", "NO_SIGNAL", 0)

    monkeypatch.setattr(strategy, "detect_regime", lambda _df, _idx: "BREAKOUT_UP")
    monkeypatch.setattr(strategy, "scan_signals", lambda _df, _idx, _regime: [{"type": "BREAKOUT_LONG", "score": 90}])
    assert strategy.select_strategy_v2(_df(), 35, 50) == ("BREAKOUT_UP", "LONG_CE", "BREAKOUT_LONG", 90)

    for code, expected_len in [
        ("IRON_CONDOR", 4),
        ("BULL_PUT_SPREAD", 2),
        ("BEAR_CALL_SPREAD", 2),
        ("LONG_CE", 1),
        ("LONG_PE", 1),
        ("BULL_CALL_SPREAD", 2),
        ("BEAR_PUT_SPREAD", 2),
        ("NO_TRADE", 0),
    ]:
        legs = strategy.build_legs(22500, "NIFTY", code, 0.01, 300)
        assert len(legs) == expected_len
