from datetime import date, datetime
import sys
import types

_jose = types.ModuleType("jose")
_jose.JWTError = Exception
_jose.jwt = types.SimpleNamespace(
    encode=lambda *args, **kwargs: "token",
    decode=lambda *args, **kwargs: {},
)
sys.modules.setdefault("jose", _jose)

_passlib = types.ModuleType("passlib")
_passlib_context = types.ModuleType("passlib.context")


class _CryptContext:
    def __init__(self, *args, **kwargs):
        pass

    def hash(self, value):
        return value

    def verify(self, plain, hashed):
        return plain == hashed


_passlib_context.CryptContext = _CryptContext
sys.modules.setdefault("passlib", _passlib)
sys.modules.setdefault("passlib.context", _passlib_context)

from app.services.live_data_sync import (
    STATUS_FAILED,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SUCCESS,
    _sync_status_from_result,
)
from app.services.live_ingestion import _missing_option_contracts, _select_nearest_nifty_future, _to_futures_df


def test_select_nearest_nifty_future_uses_next_valid_expiry():
    trade_date = date(2026, 5, 6)
    instruments = [
        {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 4, 30), "instrument_token": 1},
        {"name": "BANKNIFTY", "instrument_type": "FUT", "expiry": date(2026, 5, 7), "instrument_token": 2},
        {"name": "NIFTY", "instrument_type": "CE", "expiry": date(2026, 5, 7), "instrument_token": 3},
        {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 5, 28), "instrument_token": 4},
        {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 5, 7), "instrument_token": 5},
    ]

    selected = _select_nearest_nifty_future(instruments, trade_date)

    assert selected["instrument_token"] == 5


def test_to_futures_df_matches_warehouse_columns():
    records = [{
        "date": datetime(2026, 5, 6, 9, 15),
        "open": 22500.0,
        "high": 22510.0,
        "low": 22490.0,
        "close": 22505.0,
        "volume": 1000,
        "oi": 50000,
    }]

    df = _to_futures_df(records, date(2026, 5, 6), date(2026, 5, 28))

    assert list(df.columns) == [
        "trade_date", "timestamp", "symbol", "expiry_date",
        "open", "high", "low", "close", "volume", "open_interest", "source_file",
    ]
    assert df.iloc[0]["symbol"] == "NIFTY"
    assert df.iloc[0]["open_interest"] == 50000
    assert df.iloc[0]["source_file"] == "zerodha_live"


def test_sync_status_requires_spot_and_options_for_success():
    assert _sync_status_from_result(
        {"status": "completed", "failed_items": []},
        {"spot_rows": 376, "options_rows": 10000},
    ) == STATUS_SUCCESS
    assert _sync_status_from_result(
        {"status": "completed_with_warnings", "failed_items": ["futures"]},
        {"spot_rows": 376, "options_rows": 10000},
    ) == STATUS_PARTIAL_SUCCESS
    assert _sync_status_from_result(
        {"status": "completed_with_warnings", "failed_items": ["spot"]},
        {"spot_rows": 0, "options_rows": 10000},
    ) == STATUS_FAILED


def test_missing_option_contracts_filters_contracts_already_in_warehouse():
    exp = date(2026, 5, 12)
    contracts = [
        (101, exp, "CE", 22500),
        (102, exp, "PE", 22500),
        (103, exp, "CE", 22550),
    ]
    existing = {
        (exp, "CE", 22500),
        (exp, "PE", 22500),
    }

    assert _missing_option_contracts(contracts, existing) == [
        (103, exp, "CE", 22550),
    ]
