"""
Unit tests for app/services/strategy.py

Covers every cell in the PRD §7.3 decision matrix (positive cases)
plus all hard-override and boundary conditions (negative cases).
"""
import pytest

from app.services.strategy import build_legs, select_strategy


# ── Helper: pre-computed EMA values that force a given regime ─────────────────

def _bullish(diff_pct=0.20):
    """EMA5 > EMA20 by diff_pct %."""
    ema20 = 22000.0
    ema5 = ema20 * (1.0 + diff_pct / 100.0)
    return ema5, ema20


def _bearish(diff_pct=0.20):
    """EMA5 < EMA20 by diff_pct %."""
    ema20 = 22000.0
    ema5 = ema20 * (1.0 - diff_pct / 100.0)
    return ema5, ema20


def _neutral():
    """EMA5 ≈ EMA20, diff < 0.15%."""
    ema20 = 22000.0
    ema5 = ema20 * 1.001   # 0.1% → below threshold
    return ema5, ema20


# ── select_strategy — positive tests ─────────────────────────────────────────

class TestSelectStrategyPositive:
    """Each row of the PRD §7.3 decision matrix."""

    def test_bullish_iv_high_iron_condor(self):
        ema5, ema20 = _bullish()
        regime, strategy = select_strategy(ema5, ema20, rsi=55.0, iv_rank=40)
        assert regime == "BULLISH"
        assert strategy == "IRON_CONDOR"

    def test_bullish_iv_low_bull_put_spread(self):
        ema5, ema20 = _bullish()
        regime, strategy = select_strategy(ema5, ema20, rsi=55.0, iv_rank=20)
        assert regime == "BULLISH"
        assert strategy == "BULL_PUT_SPREAD"

    def test_bearish_iv_high_iron_condor(self):
        ema5, ema20 = _bearish()
        regime, strategy = select_strategy(ema5, ema20, rsi=45.0, iv_rank=35)
        assert regime == "BEARISH"
        assert strategy == "IRON_CONDOR"

    def test_bearish_iv_low_bear_call_spread(self):
        ema5, ema20 = _bearish()
        regime, strategy = select_strategy(ema5, ema20, rsi=45.0, iv_rank=25)
        assert regime == "BEARISH"
        assert strategy == "BEAR_CALL_SPREAD"

    def test_neutral_iv_high_iron_condor(self):
        ema5, ema20 = _neutral()
        regime, strategy = select_strategy(ema5, ema20, rsi=50.0, iv_rank=30)
        assert regime == "NEUTRAL"
        assert strategy == "IRON_CONDOR"

    def test_neutral_iv_low_no_trade(self):
        ema5, ema20 = _neutral()
        regime, strategy = select_strategy(ema5, ema20, rsi=50.0, iv_rank=10)
        assert regime == "NEUTRAL"
        assert strategy == "NO_TRADE"

    def test_returns_two_element_tuple(self):
        ema5, ema20 = _bullish()
        result = select_strategy(ema5, ema20, rsi=55.0, iv_rank=40)
        assert len(result) == 2

    def test_iv_rank_boundary_30_is_iron_condor(self):
        """iv_rank == 30 is the inclusive threshold for Iron Condor."""
        ema5, ema20 = _bullish()
        _, strategy = select_strategy(ema5, ema20, rsi=55.0, iv_rank=30)
        assert strategy == "IRON_CONDOR"

    def test_iv_rank_29_is_directional(self):
        ema5, ema20 = _bullish()
        _, strategy = select_strategy(ema5, ema20, rsi=55.0, iv_rank=29)
        assert strategy == "BULL_PUT_SPREAD"


# ── select_strategy — negative tests ─────────────────────────────────────────

class TestSelectStrategyNegative:
    """Hard overrides and boundary violations that force NO_TRADE."""

    def test_rsi_above_70_no_trade(self):
        ema5, ema20 = _bullish()
        _, strategy = select_strategy(ema5, ema20, rsi=71.0, iv_rank=40)
        assert strategy == "NO_TRADE"

    def test_rsi_exactly_70_no_trade(self):
        """RSI > 70 is the override; RSI=70 should pass (not trigger override)."""
        ema5, ema20 = _bullish()
        # rsi=70 is within BULLISH range 40-70 (inclusive), should trade
        _, strategy = select_strategy(ema5, ema20, rsi=70.0, iv_rank=40)
        assert strategy == "IRON_CONDOR"

    def test_rsi_below_30_no_trade(self):
        ema5, ema20 = _bearish()
        _, strategy = select_strategy(ema5, ema20, rsi=29.0, iv_rank=40)
        assert strategy == "NO_TRADE"

    def test_rsi_exactly_30_bearish_trades(self):
        """RSI=30 is within BEARISH range 30-60, should not be overridden."""
        ema5, ema20 = _bearish()
        _, strategy = select_strategy(ema5, ema20, rsi=30.0, iv_rank=40)
        assert strategy == "IRON_CONDOR"

    def test_bullish_rsi_outside_range_no_trade(self):
        """BULLISH but RSI=35 (below 40) → NO_TRADE."""
        ema5, ema20 = _bullish()
        _, strategy = select_strategy(ema5, ema20, rsi=35.0, iv_rank=40)
        assert strategy == "NO_TRADE"

    def test_bearish_rsi_outside_range_no_trade(self):
        """BEARISH but RSI=65 (above 60) → NO_TRADE."""
        ema5, ema20 = _bearish()
        _, strategy = select_strategy(ema5, ema20, rsi=65.0, iv_rank=40)
        assert strategy == "NO_TRADE"

    def test_neutral_rsi_outside_range_no_trade(self):
        """NEUTRAL but RSI=35 (below 40) → NO_TRADE."""
        ema5, ema20 = _neutral()
        _, strategy = select_strategy(ema5, ema20, rsi=35.0, iv_rank=40)
        assert strategy == "NO_TRADE"

    def test_ema_diff_below_threshold_is_neutral(self):
        """diff = 0.10% < 0.15% → NEUTRAL, not BULLISH."""
        ema5, ema20 = _bullish(diff_pct=0.10)
        regime, _ = select_strategy(ema5, ema20, rsi=50.0, iv_rank=40)
        assert regime == "NEUTRAL"

    def test_ema_diff_exactly_threshold_is_bullish(self):
        """diff = 0.15% (exactly) → BULLISH."""
        ema5, ema20 = _bullish(diff_pct=0.15)
        regime, _ = select_strategy(ema5, ema20, rsi=50.0, iv_rank=40)
        assert regime == "BULLISH"

    def test_zero_ema20_does_not_raise(self):
        """ema20=0 corner case — guard against division by zero."""
        regime, strategy = select_strategy(ema5=100.0, ema20=0.0, rsi=50.0, iv_rank=30)
        assert isinstance(regime, str)
        assert isinstance(strategy, str)


# ── build_legs ────────────────────────────────────────────────────────────────

class TestBuildLegs:
    SPOT = 22000.0
    INSTRUMENT = "NIFTY"   # tick=50
    VOL = 0.013
    MINUTES = 360

    def _build(self, strategy):
        return build_legs(self.SPOT, self.INSTRUMENT, strategy, self.VOL, self.MINUTES)

    def test_iron_condor_has_4_legs(self):
        legs = self._build("IRON_CONDOR")
        assert len(legs) == 4

    def test_bull_put_spread_has_2_legs(self):
        legs = self._build("BULL_PUT_SPREAD")
        assert len(legs) == 2

    def test_bear_call_spread_has_2_legs(self):
        legs = self._build("BEAR_CALL_SPREAD")
        assert len(legs) == 2

    def test_each_leg_has_required_keys(self):
        for strategy in ("IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
            legs = self._build(strategy)
            for leg in legs:
                assert {"act", "typ", "strike", "delta", "ep"}.issubset(leg.keys())

    def test_strikes_are_multiples_of_tick_nifty(self):
        legs = self._build("IRON_CONDOR")
        for leg in legs:
            assert leg["strike"] % 50 == 0

    def test_iron_condor_has_two_sells_two_buys(self):
        legs = self._build("IRON_CONDOR")
        sells = [l for l in legs if l["act"] == "SELL"]
        buys = [l for l in legs if l["act"] == "BUY"]
        assert len(sells) == 2 and len(buys) == 2

    def test_iron_condor_ce_strikes_above_atm(self):
        legs = self._build("IRON_CONDOR")
        atm = round(self.SPOT / 50) * 50
        ce_legs = [l for l in legs if l["typ"] == "CE"]
        for leg in ce_legs:
            assert leg["strike"] > atm

    def test_iron_condor_pe_strikes_below_atm(self):
        legs = self._build("IRON_CONDOR")
        atm = round(self.SPOT / 50) * 50
        pe_legs = [l for l in legs if l["typ"] == "PE"]
        for leg in pe_legs:
            assert leg["strike"] < atm

    def test_bull_put_spread_both_puts(self):
        legs = self._build("BULL_PUT_SPREAD")
        assert all(l["typ"] == "PE" for l in legs)

    def test_bull_put_spread_short_put_higher_strike(self):
        legs = self._build("BULL_PUT_SPREAD")
        sell_leg = next(l for l in legs if l["act"] == "SELL")
        buy_leg = next(l for l in legs if l["act"] == "BUY")
        assert sell_leg["strike"] > buy_leg["strike"]

    def test_bear_call_spread_both_calls(self):
        legs = self._build("BEAR_CALL_SPREAD")
        assert all(l["typ"] == "CE" for l in legs)

    def test_bear_call_spread_short_call_lower_strike(self):
        legs = self._build("BEAR_CALL_SPREAD")
        sell_leg = next(l for l in legs if l["act"] == "SELL")
        buy_leg = next(l for l in legs if l["act"] == "BUY")
        assert sell_leg["strike"] < buy_leg["strike"]

    def test_entry_prices_positive(self):
        for strategy in ("IRON_CONDOR", "BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
            legs = self._build(strategy)
            for leg in legs:
                assert leg["ep"] >= 0.50

    def test_unknown_strategy_returns_empty_list(self):
        legs = self._build("UNKNOWN_STRATEGY")
        assert legs == []

    def test_banknifty_strikes_multiples_of_100(self):
        legs = build_legs(48000.0, "BANKNIFTY", "IRON_CONDOR", self.VOL, self.MINUTES)
        for leg in legs:
            assert leg["strike"] % 100 == 0
