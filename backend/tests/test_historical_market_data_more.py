from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.historical_market_data as market_data


class _ScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def scalars(self):
        return _ScalarResult(self._rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, result_sets):
        self.result_sets = [list(rows) for rows in result_sets]

    async def execute(self, *_args, **_kwargs):
        if not self.result_sets:
            raise AssertionError("unexpected query")
        return _Result(self.result_sets.pop(0))


def test_row_to_candle_helpers_use_ltp_fallbacks_and_defaults():
    ts = datetime(2026, 5, 6, 9, 15)
    spot = SimpleNamespace(timestamp=ts, open=22500, high=22510, low=22490, close=22505, volume=None)
    option = SimpleNamespace(
        timestamp=ts,
        open=None,
        high=None,
        low=None,
        close=Decimal("100.50"),
        ltp=Decimal("101.25"),
        volume=None,
        open_interest=None,
    )

    assert market_data._spot_row_to_candle(spot)["volume"] == 0
    ltp = market_data._option_row_to_candle(option, "ltp")
    close = market_data._option_row_to_candle(option, "close")
    assert ltp["close"] == 101.25
    assert ltp["open"] == 101.25
    assert ltp["oi"] == 0
    assert close["close"] == 100.5


@pytest.mark.asyncio
async def test_expiry_and_vix_loaders_serialize_rows():
    trade_date = date(2026, 5, 6)
    ts = datetime(2026, 5, 6, 9, 15)
    vix_row = SimpleNamespace(timestamp=ts, open=12, high=13, low=11, close=12.5)
    db = _FakeDb([
        [(date(2026, 5, 12),), (date(2026, 5, 19),)],
        [(date(2026, 5, 28),)],
        [vix_row],
    ])

    assert await market_data.resolve_expiry_from_db(db, "NIFTY", trade_date) == date(2026, 5, 12)
    assert await market_data.resolve_monthly_expiry_from_db(db, "NIFTY", trade_date) == date(2026, 5, 28)
    candles = await market_data.load_vix_candles(db, trade_date)
    assert candles[0]["close"] == 12.5
    assert market_data.vix_at_time(candles, ts) == 12.5
    assert market_data.vix_at_time(candles, datetime(2026, 5, 6, 9, 16)) is None


@pytest.mark.asyncio
async def test_option_loader_builds_minute_index_and_filters_requested_legs():
    trade_date = date(2026, 5, 6)
    rows = [
        SimpleNamespace(
            timestamp=datetime(2026, 5, 6, 9, 14),
            strike=22500,
            option_type="CE",
            open=100,
            high=101,
            low=99,
            close=100,
            ltp=101,
            volume=10,
            open_interest=1000,
        ),
        SimpleNamespace(
            timestamp=datetime(2026, 5, 6, 9, 16),
            strike=22500,
            option_type="CE",
            open=102,
            high=103,
            low=101,
            close=102,
            ltp=None,
            volume=11,
            open_interest=1001,
        ),
        SimpleNamespace(
            timestamp=datetime(2026, 5, 6, 9, 16),
            strike=22600,
            option_type="PE",
            open=50,
            high=51,
            low=49,
            close=50,
            ltp=50,
            volume=1,
            open_interest=1,
        ),
    ]

    index, raw = await market_data.load_option_candles_for_strikes(
        _FakeDb([rows]),
        "NIFTY",
        trade_date,
        date(2026, 5, 12),
        {(22500, "CE")},
        "ltp",
    )

    assert set(raw) == {(22500, "CE")}
    assert -1 not in index[(22500, "CE")]
    assert index[(22500, "CE")][1] == {"price": 102.0, "volume": 11, "oi": 1001}


@pytest.mark.asyncio
async def test_high_level_loader_returns_none_for_missing_prerequisites(monkeypatch):
    async def short_spot(_db, _instrument, _trade_date):
        return [{"date": datetime(2026, 5, 6, 9, 15), "close": 22500}]

    monkeypatch.setattr(market_data, "load_spot_candles", short_spot)
    assert await market_data.load_historical_session_data(object(), "NIFTY", date(2026, 5, 6), {(22500, "CE")}) is None

    async def enough_spot(_db, _instrument, _trade_date):
        return [{"date": datetime(2026, 5, 6, 9, 15), "close": 22500}] * market_data.OR_WINDOW_MINUTES

    async def no_expiry(_db, _instrument, _trade_date):
        return None

    monkeypatch.setattr(market_data, "load_spot_candles", enough_spot)
    monkeypatch.setattr(market_data, "resolve_expiry_from_db", no_expiry)
    assert await market_data.load_historical_session_data(object(), "NIFTY", date(2026, 5, 6), {(22500, "CE")}) is None


@pytest.mark.asyncio
async def test_high_level_loader_returns_market_payload(monkeypatch):
    trade_date = date(2026, 5, 6)
    spot = [{"date": datetime(2026, 5, 6, 9, 15), "close": 22500}] * market_data.OR_WINDOW_MINUTES

    async def load_spot(_db, _instrument, _trade_date):
        return spot

    async def weekly(_db, _instrument, _trade_date):
        return date(2026, 5, 12)

    async def monthly(_db, _instrument, _trade_date):
        return date(2026, 5, 28)

    async def options(_db, _instrument, _trade_date, expiry, legs, source):
        return {"idx": expiry}, {"raw": source}

    monkeypatch.setattr(market_data, "load_spot_candles", load_spot)
    monkeypatch.setattr(market_data, "resolve_expiry_from_db", weekly)
    monkeypatch.setattr(market_data, "resolve_monthly_expiry_from_db", monthly)
    monkeypatch.setattr(market_data, "load_option_candles_for_strikes", options)

    payload = await market_data.load_historical_session_data(
        object(),
        "NIFTY",
        trade_date,
        {(22500, "CE")},
        "close",
    )

    assert payload["spot_candles"] == spot
    assert payload["expiry"] == date(2026, 5, 12)
    assert payload["monthly_expiry"] == date(2026, 5, 28)
    assert payload["lot_size"] == 75
    assert payload["option_market_index"] == {"idx": date(2026, 5, 12)}
    assert payload["option_price_source"] == "close"
