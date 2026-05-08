from datetime import date, datetime

import pytest

import app.services.zerodha_client as zerodha


class _FakeKite:
    instances = []

    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None
        self.profile_error = None
        self.historical_records = [{"date": datetime(2026, 5, 6, 9, 15), "close": 22500}]
        self.instruments_rows = [{"tradingsymbol": "NIFTYCE"}]
        self.quote_rows = {"NSE:NIFTY 50": {"last_price": 22500.5}}
        _FakeKite.instances.append(self)

    def set_access_token(self, token):
        self.access_token = token

    def login_url(self):
        return "https://kite/login"

    def generate_session(self, request_token, api_secret):
        return {"access_token": f"access-{request_token}", "user_id": "AB123"}

    def profile(self):
        if self.profile_error:
            raise self.profile_error
        return {"user_id": "AB123"}

    def historical_data(self, **_kwargs):
        if isinstance(self.historical_records, Exception):
            raise self.historical_records
        return self.historical_records

    def instruments(self, segment):
        return [{"segment": segment, **row} for row in self.instruments_rows]

    def quote(self, symbols):
        if isinstance(self.quote_rows, Exception):
            raise self.quote_rows
        return self.quote_rows


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    _FakeKite.instances = []
    monkeypatch.setattr(zerodha, "KiteConnect", _FakeKite)
    monkeypatch.setattr(zerodha, "API_KEY", "api-key")
    monkeypatch.setattr(zerodha, "API_SECRET", "api-secret")
    monkeypatch.setattr(zerodha, "_kite", None)
    monkeypatch.setattr(zerodha, "_instruments_cache", None)


def test_singleton_login_session_profile_and_cache(monkeypatch):
    monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "preset-token")

    assert zerodha.get_login_url() == "https://kite/login"
    assert zerodha.get_access_token() == "preset-token"
    assert zerodha.generate_session("request-token")["access_token"] == "access-request-token"
    assert zerodha.get_profile() == {"user_id": "AB123"}
    assert zerodha.is_authenticated() is True

    rows = zerodha.get_instruments("NFO")
    assert rows == [{"segment": "NFO", "tradingsymbol": "NIFTYCE"}]
    _FakeKite.instances[0].instruments_rows = [{"tradingsymbol": "CHANGED"}]
    assert zerodha.get_instruments("NFO") == rows
    zerodha.invalidate_instruments_cache()
    assert zerodha.get_instruments("NFO")[0]["tradingsymbol"] == "CHANGED"


def test_authentication_and_configuration_failures(monkeypatch):
    monkeypatch.setattr(zerodha, "API_KEY", "")
    with pytest.raises(RuntimeError):
        zerodha.get_login_url()
    assert zerodha.get_access_token() is None

    monkeypatch.setattr(zerodha, "API_KEY", "api-key")
    monkeypatch.setattr(zerodha, "API_SECRET", "")
    with pytest.raises(RuntimeError):
        zerodha.generate_session("token")

    zerodha._kite = _FakeKite("api-key")
    zerodha._kite.profile_error = RuntimeError("bad token")
    assert zerodha.is_authenticated() is False


def test_fetch_candles_success_empty_and_api_error():
    zerodha._kite = _FakeKite("api-key")
    zerodha._kite.access_token = "token"
    assert zerodha.fetch_candles(123, date(2026, 5, 6))[0]["close"] == 22500

    zerodha._kite.historical_records = []
    with pytest.raises(zerodha.DataUnavailableError) as empty:
        zerodha.fetch_candles(123, date(2026, 5, 6))
    assert "No candle data" in str(empty.value)

    zerodha._kite.historical_records = RuntimeError("api down")
    with pytest.raises(zerodha.DataUnavailableError) as api_error:
        zerodha.fetch_candles(123, date(2026, 5, 6))
    assert "Zerodha API error" in str(api_error.value)


def test_token_scoped_helpers_and_live_quotes():
    assert zerodha.fetch_candles_with_token(123, date(2026, 5, 6), "token")[0]["close"] == 22500
    assert zerodha.get_instruments_with_token("token", "NSE")[0]["segment"] == "NSE"
    assert zerodha.validate_access_token_with_token("token") == {"user_id": "AB123"}
    assert zerodha.fetch_live_quote(["NSE:NIFTY 50", "MISSING"], "token") == {"NSE:NIFTY 50": 22500.5}

    class _FailingQuoteKite(_FakeKite):
        def __init__(self, api_key):
            super().__init__(api_key)
            self.quote_rows = RuntimeError("quote down")

    zerodha.KiteConnect = _FailingQuoteKite
    with pytest.raises(RuntimeError):
        zerodha.fetch_live_quote(["NSE:NIFTY 50"], "token")


def test_find_option_symbol_handles_date_and_datetime_expiry():
    instruments = [
        {"name": "NIFTY", "expiry": datetime(2026, 5, 14, 0, 0), "instrument_type": "CE", "strike": 22500, "tradingsymbol": "NIFTYCE"},
        {"name": "NIFTY", "expiry": date(2026, 5, 14), "instrument_type": "PE", "strike": 22500, "tradingsymbol": "NIFTYPE"},
    ]

    assert zerodha.find_option_symbol(instruments, "NIFTY", date(2026, 5, 14), "CE", 22500) == "NFO:NIFTYCE"
    assert zerodha.find_option_symbol(instruments, "NIFTY", date(2026, 5, 14), "PE", 22500) == "NFO:NIFTYPE"
    assert zerodha.find_option_symbol(instruments, "NIFTY", date(2026, 5, 14), "CE", 22600) is None
