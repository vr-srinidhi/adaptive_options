"""Tests for order-book depth capture.

Covers the two properties the trading loop depends on — capture never blocks
and never raises — plus symbol parsing, which is where a wrong answer silently
corrupts the strike on every weekly row.
"""
import asyncio
from datetime import date, datetime

import pytest

from app.services import depth_capture as dc
from app.services.depth_capture import (
    capture_depth,
    parse_symbol,
    persist_depth,
    stop_depth_writer,
    _top_of_book,
)

TS = datetime(2026, 8, 31, 9, 50, 10)
TD = date(2026, 8, 31)


class _FakeSession:
    """Stands in for AsyncSessionLocal()."""

    def __init__(self, store, fail_on=None):
        self.store = store
        self.fail_on = fail_on
        self.pending = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.pending.append(obj)

    def add_all(self, objs):
        self.pending.extend(objs)

    async def commit(self):
        if self.fail_on and any(self.fail_on(o) for o in self.pending):
            raise RuntimeError("simulated driver error")
        self.store.extend(self.pending)
        self.pending = []


def _factory(store, fail_on=None):
    return lambda: _FakeSession(store, fail_on)


def _quote(last=65.8, bid=65.4, ask=66.1, bid_qty=1500, ask_qty=1200):
    return {
        "last_price": last, "volume": 913055, "oi": 4210500,
        "depth": {
            "buy": [{"price": bid, "quantity": bid_qty, "orders": 9}],
            "sell": [{"price": ask, "quantity": ask_qty, "orders": 7}],
        },
    }


# ── Symbol parsing ────────────────────────────────────────────────────────────
def test_parses_monthly_symbol():
    assert parse_symbol("NFO:NIFTY25AUG24300CE") == (24300, "CE")


def test_parses_weekly_symbol():
    """Regression: weekly expiry runs into the strike with no separator.

    Matching trailing digits yields 2690124300 -- a wrong strike that also
    overflows INTEGER and takes the insert down with it.
    """
    assert parse_symbol("NFO:NIFTY2690124300CE") == (24300, "CE")


def test_parses_weekly_letter_month_codes():
    # Oct/Nov/Dec are encoded as O/N/D, not 10/11/12.
    assert parse_symbol("NFO:NIFTY26O0124300PE") == (24300, "PE")
    assert parse_symbol("NFO:NIFTY26N1524500CE") == (24500, "CE")
    assert parse_symbol("NFO:NIFTY26D3123000PE") == (23000, "PE")


def test_parses_banknifty_and_bare_symbols():
    assert parse_symbol("NFO:BANKNIFTY25AUG54000PE") == (54000, "PE")
    assert parse_symbol("NIFTY25AUG24300CE") == (24300, "CE")   # no exchange prefix


def test_weekly_strike_stays_within_int4():
    strike, _ = parse_symbol("NFO:NIFTY2690124300CE")
    assert strike is not None and strike <= dc._INT4_MAX


def test_unparseable_symbol_is_not_fatal():
    assert parse_symbol("GARBAGE") == (None, None)
    assert parse_symbol("") == (None, None)
    assert parse_symbol("WEIRDFORMATCE") == (None, "CE")        # type salvaged


def test_oversized_strike_is_rejected_not_overflowed():
    assert parse_symbol("NFO:NIFTY25AUG99999999999CE") == (None, "CE")


# ── Top of book ───────────────────────────────────────────────────────────────
def test_top_of_book_extracts_best_prices_and_keeps_ladder():
    tob = _top_of_book(_quote())
    assert (tob["bid"], tob["ask"], tob["bid_qty"], tob["ask_qty"]) == (65.4, 66.1, 1500, 1200)
    assert tob["depth"]["buy"][0]["orders"] == 9


def test_top_of_book_tolerates_missing_ladder():
    assert _top_of_book({"last_price": 10.0}) == {
        "bid": None, "ask": None, "bid_qty": None, "ask_qty": None, "depth": None,
    }


# ── Persistence ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_persists_top_of_book_and_full_ladder():
    store = []
    n = await persist_depth({"NFO:NIFTY25AUG24300CE": _quote()}, TS, TD,
                            session_factory=_factory(store))
    assert n == 1
    row = store[0]
    assert (row.strike, row.option_type) == (24300, "CE")
    assert (float(row.bid), float(row.ask)) == (65.4, 66.1)
    assert row.depth_json["sell"][0]["price"] == 66.1
    assert row.volume == 913055 and row.open_interest == 4210500


@pytest.mark.asyncio
async def test_caller_metadata_takes_precedence_over_parsing():
    store = []
    await persist_depth({"WEIRD_SYMBOL": _quote()}, TS, TD,
                        meta={"WEIRD_SYMBOL": (24500, "PE")},
                        expiry_date=date(2026, 9, 1),
                        session_factory=_factory(store))
    row = store[0]
    assert (row.strike, row.option_type) == (24500, "PE")
    assert row.expiry_date == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_malformed_quote_does_not_lose_the_valid_rows():
    """One bad quote must cost one row, not the whole poll."""
    store = []
    n = await persist_depth(
        {
            "NFO:NIFTY25AUG24300CE": _quote(),
            "NFO:NIFTY25AUG24200PE": "not-a-dict",      # malformed
            "NFO:NIFTY25AUG24400CE": _quote(last=12.0),
        },
        TS, TD, session_factory=_factory(store),
    )
    assert n == 2
    assert {r.symbol for r in store} == {"NFO:NIFTY25AUG24300CE", "NFO:NIFTY25AUG24400CE"}


@pytest.mark.asyncio
async def test_non_numeric_values_are_dropped_not_raised():
    store = []
    bad = _quote()
    bad["last_price"] = object()
    bad["oi"] = float("nan")
    n = await persist_depth({"NFO:NIFTY25AUG24300CE": bad}, TS, TD,
                            session_factory=_factory(store))
    assert n == 1
    assert store[0].last_price is None and store[0].open_interest is None
    assert float(store[0].bid) == 65.4          # good fields still captured


@pytest.mark.asyncio
async def test_batch_commit_failure_falls_back_to_isolating_the_bad_row():
    store = []
    fail_on = lambda o: o.symbol.endswith("24200PE")
    n = await persist_depth(
        {"NFO:NIFTY25AUG24300CE": _quote(), "NFO:NIFTY25AUG24200PE": _quote()},
        TS, TD, session_factory=_factory(store, fail_on=fail_on),
    )
    assert n == 1
    assert store[0].symbol == "NFO:NIFTY25AUG24300CE"


@pytest.mark.asyncio
async def test_empty_payload_is_a_noop():
    assert await persist_depth({}, TS, TD, session_factory=_factory([])) == 0


@pytest.mark.asyncio
async def test_persist_swallows_unexpected_failure():
    """A dead pool costs analytics rows, and must not raise into the caller."""
    def exploding_factory():
        raise RuntimeError("pool exhausted")
    # Batch commit fails, per-row retry also fails -> 0 written, no exception.
    assert await persist_depth({"X": _quote()}, TS, TD,
                               session_factory=exploding_factory) == 0


# ── Queue behaviour: the trading-loop guarantees ─────────────────────────────
@pytest.mark.asyncio
async def test_capture_queues_and_writer_drains_it(monkeypatch):
    written = []

    async def fake_persist(**kwargs):
        written.append(kwargs)
        return 1

    monkeypatch.setattr(dc, "persist_depth", fake_persist)
    await stop_depth_writer()
    try:
        assert capture_depth({"NFO:NIFTY25AUG24300CE": _quote()}, TS, TD,
                             meta={"NFO:NIFTY25AUG24300CE": (24300, "CE")},
                             session_id="s1") is True
        for _ in range(50):                     # let the writer run
            await asyncio.sleep(0)
            if written:
                break
        assert len(written) == 1
        assert written[0]["session_id"] == "s1"
        assert written[0]["meta"] == {"NFO:NIFTY25AUG24300CE": (24300, "CE")}
    finally:
        await stop_depth_writer()


@pytest.mark.asyncio
async def test_capture_drops_instead_of_blocking_when_queue_is_full():
    """A stalled database must cost snapshots, never a delayed trade."""
    await stop_depth_writer()
    stuck = asyncio.Event()

    async def blocked():
        await stuck.wait()

    dc._queue = asyncio.Queue(maxsize=1)
    dc._queue_loop = asyncio.get_running_loop()
    dc._worker = asyncio.get_running_loop().create_task(blocked())
    try:
        assert capture_depth({"a": _quote()}, TS, TD) is True     # fills the queue
        assert capture_depth({"b": _quote()}, TS, TD) is False    # dropped, no raise
        assert capture_depth({"c": _quote()}, TS, TD) is False
    finally:
        stuck.set()
        dc._worker.cancel()
        dc._queue = dc._worker = dc._queue_loop = None


def test_capture_without_running_loop_is_a_noop():
    # Never raises, even with no event loop to schedule onto.
    assert capture_depth({"NFO:NIFTY25AUG24300CE": _quote()}, TS, TD) is False


@pytest.mark.asyncio
async def test_capture_of_empty_payload_is_skipped():
    assert capture_depth({}, TS, TD) is False


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_safe_when_never_started():
    await stop_depth_writer()
    await stop_depth_writer()
