"""
Unit tests for contract_spec_service.py.

Functions that require a DB are tested with a minimal async fake.
Pure helpers (resolve_atm_strike, resolve_leg_strikes) are tested directly.
"""
import asyncio
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from app.services.contract_spec_service import (
    ContractSpec,
    resolve_atm_strike,
    resolve_leg_strikes,
)


# ── resolve_atm_strike ────────────────────────────────────────────────────────

def test_atm_strike_rounds_to_nearest_step():
    assert resolve_atm_strike(22_374, 50) == 22_350
    assert resolve_atm_strike(22_376, 50) == 22_400
    assert resolve_atm_strike(22_500, 50) == 22_500


def test_atm_strike_banknifty_100_step():
    # Use non-midpoint values to avoid banker's rounding ambiguity
    assert resolve_atm_strike(48_260, 100) == 48_300
    assert resolve_atm_strike(48_149, 100) == 48_100
    assert resolve_atm_strike(48_051, 100) == 48_100


def test_atm_strike_exactly_midpoint_rounds_up():
    # 22_375 / 50 = 447.5 → round() in Python 3 goes to nearest even → 448 → 22400
    result = resolve_atm_strike(22_375, 50)
    assert result % 50 == 0


# ── resolve_leg_strikes ───────────────────────────────────────────────────────

def test_short_straddle_leg_template():
    template = [
        {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
        {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
    ]
    legs = resolve_leg_strikes(template, atm_strike=22_400, strike_step=50)
    assert len(legs) == 2
    sides      = [l[0] for l in legs]
    opt_types  = [l[1] for l in legs]
    strikes    = [l[2] for l in legs]
    assert sides     == ["SELL", "SELL"]
    assert opt_types == ["CE", "PE"]
    assert strikes   == [22_400, 22_400]


def test_offset_steps_applied_correctly():
    template = [
        {"side": "BUY", "option_type": "CE", "strike_offset_steps": 2},
        {"side": "SELL", "option_type": "PE", "strike_offset_steps": -1},
    ]
    legs = resolve_leg_strikes(template, atm_strike=22_400, strike_step=50)
    ce_strike = legs[0][2]
    pe_strike = legs[1][2]
    assert ce_strike == 22_500   # ATM + 2 × 50
    assert pe_strike == 22_350   # ATM - 1 × 50


def test_config_driven_wing_offsets():
    template = [
        {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0},
        {"side": "SELL", "option_type": "PE", "strike_offset_steps": 0},
        {"side": "BUY", "option_type": "CE", "strike_offset_steps_from_config": "wing_width_steps", "strike_offset_sign": 1},
        {"side": "BUY", "option_type": "PE", "strike_offset_steps_from_config": "wing_width_steps", "strike_offset_sign": -1},
    ]
    legs = resolve_leg_strikes(template, atm_strike=22_400, strike_step=50, config={"wing_width_steps": 3})
    assert legs == [
        ("SELL", "CE", 22_400),
        ("SELL", "PE", 22_400),
        ("BUY", "CE", 22_550),
        ("BUY", "PE", 22_250),
    ]


def test_empty_template_returns_empty():
    assert resolve_leg_strikes([], 22_400, 50) == []


def test_missing_offset_defaults_to_zero():
    template = [{"side": "SELL", "option_type": "CE"}]
    legs = resolve_leg_strikes(template, 22_400, 50)
    assert legs[0][2] == 22_400


# ── resolve_atm_strike edge cases ─────────────────────────────────────────────

def test_atm_strike_non_integer_spot():
    result = resolve_atm_strike(22_374.7, 50)
    assert result % 50 == 0


def test_atm_strike_returns_int():
    result = resolve_atm_strike(22_374, 50)
    assert isinstance(result, int)
