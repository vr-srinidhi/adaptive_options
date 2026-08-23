"""Coverage for _compute_shadow_mtm — the "if held" curve on the replay screen.

This function had no tests at all, which is how a refactor silently inverted
every shadow curve: the per-leg loop was converted to attribute access but the
price lookup kept reading names leaked from an earlier positional loop, so every
leg was priced with the *last* leg's quote.
"""
from datetime import date, datetime
from types import SimpleNamespace
import uuid

import pytest

import app.routers.workbench as workbench


class _Result:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows


class _FakeDb:
    """Returns option candles filtered the way the real query would."""
    def __init__(self, candles, spot_rows):
        self.candles = candles
        self.spot_rows = spot_rows
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        # First call in the function is the spot-candle query.
        if self.calls == 1:
            return _Result(self.spot_rows)
        # Subsequent calls are per-leg option queries; the compiled WHERE carries
        # the bound strike/option_type, so read them back off the statement.
        binds = stmt.compile().params
        strike = binds.get("strike_1")
        opt = binds.get("option_type_1")
        return _Result([c for c in self.candles
                        if c.strike == strike and c.option_type == opt])


def _candle(strike, opt, ts, close):
    return SimpleNamespace(strike=strike, option_type=opt, timestamp=ts, close=close)


def _leg(side, opt, strike, entry_price, quantity):
    return SimpleNamespace(side=side, option_type=opt, strike=strike,
                           expiry_date=date(2026, 8, 18), entry_price=entry_price,
                           quantity=quantity)


@pytest.mark.asyncio
async def test_each_leg_is_priced_with_its_own_quote():
    """The regression: a 3-leg book where every strike has a distinct price.

    If any leg borrows another's quote the resulting MTM is wildly wrong, and
    with the straddle/hedge prices used here it flips sign entirely.
    """
    ts = datetime(2026, 8, 12, 14, 30)
    run = SimpleNamespace(
        instrument="NIFTY", trade_date=date(2026, 8, 12), status="completed",
        lot_size=75, approved_lots=13,
        exit_time="14:00", id=uuid.uuid4(),
    )
    legs = [
        _leg("SELL", "CE", 24300, 300.0, 13 * 75),
        _leg("SELL", "PE", 24300, 100.0, 13 * 75),
        _leg("BUY",  "PE", 24100, 50.0,   4 * 75),   # smaller delta-hedge leg
    ]
    candles = [
        _candle(24300, "CE", ts, 320.0),
        _candle(24300, "PE", ts, 15.0),
        _candle(24100, "PE", ts, 8.0),
    ]
    db = _FakeDb(candles, [SimpleNamespace(timestamp=ts)])

    shadow = await workbench._compute_shadow_mtm(db, run, legs)

    assert len(shadow) == 1
    # Per-leg, per-quantity:
    #   SELL CE 24300: (300 - 320) * 975  = -19,500
    #   SELL PE 24300: (100 -  15) * 975  = +82,875
    #   BUY  PE 24100: (  8 -  50) * 300  = -12,600
    #                                  gross = +50,775
    # A leaked lookup would price all three at 8.0 and invert the sign.
    assert shadow[0]["net_mtm"] < 60_000
    assert shadow[0]["net_mtm"] > 40_000
