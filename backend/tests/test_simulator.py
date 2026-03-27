"""
Unit tests for app/services/simulator.py

Positive tests: verify correct outputs for well-formed inputs.
Negative tests: verify edge-case and boundary behaviour.
"""
import math
from datetime import date, time

import numpy as np
import pytest

from app.services.simulator import (
    CANDLES_PER_DAY,
    ENTRY_CANDLE_IDX,
    EOD_CANDLE_IDX,
    _idx_to_time,
    _idx_to_time_obj,
    _seed,
    compute_ema,
    compute_rsi,
    generate_candles,
    get_iv_rank,
    price_option,
    run_day_simulation,
)

DATE_A = date(2025, 1, 6)   # Monday
DATE_B = date(2025, 1, 7)   # Tuesday


# ── _seed ─────────────────────────────────────────────────────────────────────

class TestSeed:
    def test_returns_integer(self):
        result = _seed(DATE_A, "NIFTY")
        assert isinstance(result, int)

    def test_deterministic_same_inputs(self):
        assert _seed(DATE_A, "NIFTY") == _seed(DATE_A, "NIFTY")

    def test_different_dates_produce_different_seeds(self):
        assert _seed(DATE_A, "NIFTY") != _seed(DATE_B, "NIFTY")

    def test_different_instruments_produce_different_seeds(self):
        assert _seed(DATE_A, "NIFTY") != _seed(DATE_A, "BANKNIFTY")

    def test_seed_is_nonnegative(self):
        assert _seed(DATE_A, "NIFTY") >= 0


# ── generate_candles ──────────────────────────────────────────────────────────

class TestGenerateCandles:
    def test_shape_is_375x4(self):
        candles, _ = generate_candles(DATE_A, "NIFTY")
        assert candles.shape == (CANDLES_PER_DAY, 4)

    def test_daily_vol_in_valid_range(self):
        _, daily_vol = generate_candles(DATE_A, "NIFTY")
        assert 0.007 <= daily_vol <= 0.020

    def test_deterministic_same_inputs(self):
        c1, v1 = generate_candles(DATE_A, "NIFTY")
        c2, v2 = generate_candles(DATE_A, "NIFTY")
        np.testing.assert_array_equal(c1, c2)
        assert v1 == v2

    def test_different_dates_give_different_candles(self):
        c1, _ = generate_candles(DATE_A, "NIFTY")
        c2, _ = generate_candles(DATE_B, "NIFTY")
        assert not np.array_equal(c1, c2)

    def test_all_prices_positive(self):
        candles, _ = generate_candles(DATE_A, "NIFTY")
        assert np.all(candles > 0)

    def test_ohlc_consistency_high_gte_low(self):
        candles, _ = generate_candles(DATE_A, "NIFTY")
        highs = candles[:, 1]
        lows = candles[:, 2]
        assert np.all(highs >= lows)

    def test_banknifty_base_price_higher_than_nifty(self):
        c_nifty, _ = generate_candles(DATE_A, "NIFTY")
        c_bank, _ = generate_candles(DATE_A, "BANKNIFTY")
        # BankNifty base is ~48000 vs Nifty ~22000
        assert c_bank[0, 3] > c_nifty[0, 3]


# ── compute_ema ───────────────────────────────────────────────────────────────

class TestComputeEma:
    def test_output_length_matches_input(self):
        prices = np.arange(1.0, 21.0)
        ema = compute_ema(prices, 5)
        assert len(ema) == len(prices)

    def test_first_value_equals_first_price(self):
        prices = np.array([100.0, 101.0, 102.0])
        ema = compute_ema(prices, 5)
        assert ema[0] == pytest.approx(100.0)

    def test_constant_prices_ema_equals_price(self):
        prices = np.full(50, 200.0)
        ema = compute_ema(prices, 5)
        assert ema[-1] == pytest.approx(200.0)

    def test_rising_prices_ema_lags_behind(self):
        prices = np.arange(1.0, 101.0)
        ema = compute_ema(prices, 20)
        # EMA should lag: last EMA < last price
        assert ema[-1] < prices[-1]

    def test_ema5_reacts_faster_than_ema20(self):
        """EMA(5) should be closer to latest price than EMA(20)."""
        prices = np.arange(1.0, 101.0)
        ema5 = compute_ema(prices, 5)
        ema20 = compute_ema(prices, 20)
        assert abs(ema5[-1] - prices[-1]) < abs(ema20[-1] - prices[-1])

    def test_single_element_array(self):
        prices = np.array([42.0])
        ema = compute_ema(prices, 5)
        assert ema[0] == pytest.approx(42.0)

    def test_returns_numpy_array(self):
        prices = np.arange(10.0)
        assert isinstance(compute_ema(prices, 3), np.ndarray)


# ── compute_rsi ───────────────────────────────────────────────────────────────

class TestComputeRsi:
    def test_output_length_matches_input(self):
        prices = np.arange(1.0, 50.0)
        rsi = compute_rsi(prices, 14)
        assert len(rsi) == len(prices)

    def test_values_in_0_to_100(self):
        prices = np.random.RandomState(42).normal(100, 5, 200)
        rsi = compute_rsi(prices, 14)
        assert np.all((rsi[14:] >= 0) & (rsi[14:] <= 100))

    def test_too_few_prices_returns_zeros(self):
        prices = np.arange(1.0, 10.0)   # only 9 elements, period=14
        rsi = compute_rsi(prices, 14)
        assert np.all(rsi == 0.0)

    def test_constant_prices_no_gain_no_loss(self):
        """Constant prices → avg_loss=0 → RSI = 100 (no losses)."""
        prices = np.full(30, 100.0)
        rsi = compute_rsi(prices, 14)
        assert rsi[14] == pytest.approx(100.0)

    def test_steadily_rising_prices_high_rsi(self):
        prices = np.linspace(100, 200, 100)
        rsi = compute_rsi(prices, 14)
        # All gains, no losses → RSI close to 100
        assert rsi[-1] > 90

    def test_steadily_falling_prices_low_rsi(self):
        prices = np.linspace(200, 100, 100)
        rsi = compute_rsi(prices, 14)
        # All losses, no gains → RSI close to 0
        assert rsi[-1] < 10

    def test_returns_numpy_array(self):
        prices = np.arange(50.0)
        assert isinstance(compute_rsi(prices), np.ndarray)


# ── get_iv_rank ───────────────────────────────────────────────────────────────

class TestGetIvRank:
    def test_in_valid_range(self):
        iv = get_iv_rank(DATE_A, "NIFTY")
        assert 15 <= iv <= 85

    def test_deterministic_same_inputs(self):
        assert get_iv_rank(DATE_A, "NIFTY") == get_iv_rank(DATE_A, "NIFTY")

    def test_different_instruments_may_differ(self):
        # Seeds differ, so ranks should differ for the same date
        iv_nifty = get_iv_rank(DATE_A, "NIFTY")
        iv_bank = get_iv_rank(DATE_A, "BANKNIFTY")
        # They should be independent draws (not guaranteed equal)
        assert isinstance(iv_nifty, int) and isinstance(iv_bank, int)

    def test_different_dates_may_differ(self):
        iv1 = get_iv_rank(DATE_A, "NIFTY")
        iv2 = get_iv_rank(DATE_B, "NIFTY")
        assert isinstance(iv1, int) and isinstance(iv2, int)

    def test_returns_int(self):
        assert isinstance(get_iv_rank(DATE_A, "NIFTY"), int)


# ── price_option ──────────────────────────────────────────────────────────────

class TestPriceOption:
    SPOT = 22000.0
    VOL = 0.013
    MINUTES = 360

    def test_minimum_price_is_050(self):
        # Deep OTM call with zero remaining time → floor at 0.50
        price = price_option(self.SPOT, self.SPOT + 5000, self.VOL, 0, "CE")
        assert price == pytest.approx(0.50)

    def test_itm_ce_greater_than_otm_ce(self):
        itm = price_option(self.SPOT, self.SPOT - 200, self.VOL, self.MINUTES, "CE")
        otm = price_option(self.SPOT, self.SPOT + 200, self.VOL, self.MINUTES, "CE")
        assert itm > otm

    def test_itm_pe_greater_than_otm_pe(self):
        itm = price_option(self.SPOT, self.SPOT + 200, self.VOL, self.MINUTES, "PE")
        otm = price_option(self.SPOT, self.SPOT - 200, self.VOL, self.MINUTES, "PE")
        assert itm > otm

    def test_ce_intrinsic_included_when_itm(self):
        strike = self.SPOT - 500  # deep ITM call
        price = price_option(self.SPOT, strike, self.VOL, self.MINUTES, "CE")
        assert price >= 500.0

    def test_pe_intrinsic_included_when_itm(self):
        strike = self.SPOT + 500  # deep ITM put
        price = price_option(self.SPOT, strike, self.VOL, self.MINUTES, "PE")
        assert price >= 500.0

    def test_zero_remaining_otm_returns_floor(self):
        price = price_option(self.SPOT, self.SPOT + 1000, self.VOL, 0, "CE")
        assert price == pytest.approx(0.50)

    def test_price_increases_with_more_time(self):
        p_short = price_option(self.SPOT, self.SPOT, self.VOL, 30, "CE")
        p_long = price_option(self.SPOT, self.SPOT, self.VOL, 360, "CE")
        assert p_long > p_short

    def test_higher_vol_gives_higher_price(self):
        low_vol = price_option(self.SPOT, self.SPOT, 0.008, self.MINUTES, "CE")
        high_vol = price_option(self.SPOT, self.SPOT, 0.020, self.MINUTES, "CE")
        assert high_vol > low_vol

    def test_returns_float(self):
        price = price_option(self.SPOT, self.SPOT, self.VOL, self.MINUTES, "CE")
        assert isinstance(price, float)


# ── _idx_to_time ──────────────────────────────────────────────────────────────

class TestIdxToTime:
    def test_idx_0_is_0915(self):
        assert _idx_to_time(0) == "09:15"

    def test_entry_candle_idx_is_0930(self):
        assert _idx_to_time(ENTRY_CANDLE_IDX) == "09:30"

    def test_eod_candle_idx_is_1515(self):
        assert _idx_to_time(EOD_CANDLE_IDX) == "15:15"

    def test_last_candle_374_is_1529(self):
        assert _idx_to_time(374) == "15:29"

    def test_returns_string(self):
        assert isinstance(_idx_to_time(0), str)

    def test_format_is_hhmm(self):
        result = _idx_to_time(45)
        assert len(result) == 5 and result[2] == ":"


# ── _idx_to_time_obj ──────────────────────────────────────────────────────────

class TestIdxToTimeObj:
    def test_idx_0_returns_time_0915(self):
        assert _idx_to_time_obj(0) == time(9, 15)

    def test_entry_idx_returns_time_0930(self):
        assert _idx_to_time_obj(ENTRY_CANDLE_IDX) == time(9, 30)

    def test_eod_idx_returns_time_1515(self):
        assert _idx_to_time_obj(EOD_CANDLE_IDX) == time(15, 15)

    def test_idx_60_crosses_hour_boundary(self):
        # 9:15 + 60 min = 10:15
        assert _idx_to_time_obj(60) == time(10, 15)

    def test_returns_time_object(self):
        assert isinstance(_idx_to_time_obj(0), time)


# ── run_day_simulation ────────────────────────────────────────────────────────

class TestRunDaySimulation:
    CAPITAL = 500_000.0

    def test_returns_dict_with_required_keys(self):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        required = {
            "instrument", "session_date", "capital", "regime", "iv_rank",
            "strategy", "entry_time", "exit_time", "exit_reason",
            "spot_in", "spot_out", "lots", "max_profit", "max_loss",
            "pnl", "pnl_pct", "wl", "ema5", "ema20", "rsi14",
            "legs", "min_data",
        }
        assert required.issubset(result.keys())

    def test_deterministic_same_date(self):
        r1 = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        r2 = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        assert r1["pnl"] == r2["pnl"]
        assert r1["strategy"] == r2["strategy"]

    def test_instrument_field_preserved(self):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        assert result["instrument"] == "NIFTY"

    def test_capital_field_preserved(self):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        assert result["capital"] == self.CAPITAL

    def test_wl_is_valid_value(self):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        assert result["wl"] in ("WIN", "LOSS", "BREAK_EVEN", "NO_TRADE")

    def test_exit_reason_is_valid(self):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        assert result["exit_reason"] in ("PROFIT_TARGET", "HARD_EXIT", "END_OF_DAY", "NO_SIGNAL")

    def test_no_trade_has_empty_legs(self, mocker):
        mocker.patch(
            "app.services.strategy.select_strategy",
            return_value=("NEUTRAL", "NO_TRADE"),
        )
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        assert result["strategy"] == "NO_TRADE"
        assert result["legs"] == []
        assert result["min_data"] == []
        assert result["pnl"] == 0.0

    def test_lots_at_least_1_when_trade(self, mocker):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        if result["strategy"] != "NO_TRADE":
            assert result["lots"] >= 1

    def test_larger_capital_gives_more_lots(self, mocker):
        r_small = run_day_simulation(DATE_A, "NIFTY", 100_000.0)
        r_large = run_day_simulation(DATE_A, "NIFTY", 5_000_000.0)
        if r_small["strategy"] != "NO_TRADE" and r_large["strategy"] != "NO_TRADE":
            assert r_large["lots"] >= r_small["lots"]

    def test_min_data_entries_have_required_keys(self):
        result = run_day_simulation(DATE_A, "NIFTY", self.CAPITAL)
        for entry in result["min_data"]:
            assert {"time", "spot", "pnl"}.issubset(entry.keys())
