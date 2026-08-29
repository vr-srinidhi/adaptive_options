"""
Best-effort persistence of order-book depth seen during the live paper poll.

Design rule: **this must never affect trading.** Two mechanisms enforce it:

1. *Never blocks.* `capture_depth()` is synchronous and non-blocking — it drops
   a snapshot onto a bounded queue and returns. A single background writer
   drains it. The trading loop never awaits a database write.

2. *Never starves the pool.* One writer means analytics holds at most **one**
   connection from the shared pool, no matter how slow the database becomes.
   Spawning a task per poll would let writers accumulate without limit and
   compete with hedge and square-off writes for the same connections.

When the queue is full, snapshots are **dropped**. Losing analytics is always
preferable to delaying a trade.

Every entry point swallows its own exceptions. A capture failure costs one row
of analytics; it must not cost a hedge, an exit, or a session.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from app.database import AsyncSessionLocal
from app.models.option_depth import OptionDepthSnapshot

log = logging.getLogger(__name__)

# Column bounds. Exceeding them raises inside the driver at commit time, which
# would take down the whole batch, so oversized values are rejected up front.
_INT4_MAX = 2_147_483_647
_INT8_MAX = 9_223_372_036_854_775_807
_NUMERIC_MAX = 10 ** 10          # Numeric(12, 2)
_SYMBOL_MAX = 60                 # String(60)

# One poll writes at most 4 rows, roughly every 10s. 500 rows is ~20 minutes of
# backlog: far more headroom than a healthy database needs, and a hard ceiling
# if it stalls.
_QUEUE_MAXSIZE = 500


# ── Symbol parsing ────────────────────────────────────────────────────────────
# Zerodha uses two NFO formats, and they are genuinely ambiguous to a naive
# "trailing digits" match:
#
#   monthly   NIFTY25AUG24300CE     -> yy=25 mon=AUG   strike=24300
#   weekly    NIFTY2690124300CE     -> yy=26 m=9 dd=01 strike=24300
#
# In the weekly form the expiry runs straight into the strike with no separator,
# so matching trailing digits yields 2690124300 — a wrong strike that also
# overflows INTEGER and fails the insert.
#
# Weekly month codes are 1-9 for Jan-Sep, then O, N, D.
# https://kite.trade/forum/discussion/5574/change-in-format-of-weekly-options-instruments
#
# Monthly is tried first: its three-letter month is unambiguous, whereas the
# weekly pattern could otherwise mis-split a monthly symbol.
_MONTHLY_RE = re.compile(r"^[A-Z]+(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<strike>\d+)(?P<opt>CE|PE)$")
_WEEKLY_RE = re.compile(r"^[A-Z]+(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+)(?P<opt>CE|PE)$")


def parse_symbol(symbol: str) -> Tuple[Optional[int], Optional[str]]:
    """Best-effort strike/type from a tradingsymbol.

    Only a fallback: the engine passes known strikes via `meta`, which is
    always more reliable than re-deriving what the caller already had.
    """
    s = (symbol or "").split(":")[-1].strip().upper()
    for rx in (_MONTHLY_RE, _WEEKLY_RE):
        m = rx.match(s)
        if m:
            opt = m.group("opt")
            try:
                strike = int(m.group("strike"))
            except (TypeError, ValueError):
                return None, opt
            return (strike if 0 < strike <= _INT4_MAX else None), opt
    # Unrecognised layout — salvage the option type if it is there.
    if len(s) > 2 and s[-2:] in ("CE", "PE"):
        return None, s[-2:]
    return None, None


def _top_of_book(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Best bid/ask from the depth ladder, tolerating a missing ladder."""
    out: Dict[str, Any] = {"bid": None, "ask": None, "bid_qty": None, "ask_qty": None, "depth": None}
    depth = (quote or {}).get("depth") or {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    if buy:
        out["bid"] = buy[0].get("price")
        out["bid_qty"] = buy[0].get("quantity")
    if sell:
        out["ask"] = sell[0].get("price")
        out["ask_qty"] = sell[0].get("quantity")
    if buy or sell:
        out["depth"] = {"buy": buy, "sell": sell}
    return out


# ── Coercion ──────────────────────────────────────────────────────────────────
def _safe_int(value: Any, cap: int) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if -cap <= out <= cap else None


def _safe_price(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):   # NaN / inf
        return None
    if abs(out) >= _NUMERIC_MAX:
        return None
    return round(out, 2)


def _build_row(
    symbol: str,
    quote: Dict[str, Any],
    ts: datetime,
    trade_date,
    meta: Optional[Dict[str, Tuple[Optional[int], Optional[str]]]],
    expiry_date,
    session_id: Optional[str],
) -> Optional[OptionDepthSnapshot]:
    """Validate one quote into a row, or return None (logged) if unusable."""
    try:
        if not isinstance(symbol, str) or not symbol or len(symbol) > _SYMBOL_MAX:
            log.warning("depth capture: unusable symbol %r, skipped", symbol)
            return None
        if not isinstance(quote, dict):
            log.warning("depth capture: %s has non-dict quote, skipped", symbol)
            return None

        # Caller-supplied metadata wins: the engine resolved these strikes from
        # the instruments master, so they need no parsing.
        strike = opt_type = None
        if meta and symbol in meta:
            strike, opt_type = meta[symbol]
        if strike is None or opt_type is None:
            p_strike, p_opt = parse_symbol(symbol)
            strike = strike if strike is not None else p_strike
            opt_type = opt_type if opt_type is not None else p_opt

        strike = _safe_int(strike, _INT4_MAX)
        if opt_type not in ("CE", "PE"):
            opt_type = None

        tob = _top_of_book(quote)
        return OptionDepthSnapshot(
            trade_date=trade_date,
            timestamp=ts.replace(tzinfo=None),      # column is timezone=False
            symbol=symbol,
            strike=strike,
            option_type=opt_type,
            expiry_date=expiry_date,
            last_price=_safe_price(quote.get("last_price")),
            bid=_safe_price(tob["bid"]),
            ask=_safe_price(tob["ask"]),
            bid_qty=_safe_int(tob["bid_qty"], _INT4_MAX),
            ask_qty=_safe_int(tob["ask_qty"], _INT4_MAX),
            depth_json=tob["depth"],
            volume=_safe_int(quote.get("volume") or quote.get("volume_traded"), _INT8_MAX),
            open_interest=_safe_int(quote.get("oi"), _INT8_MAX),
            session_id=str(session_id) if session_id else None,
        )
    except Exception as exc:
        log.warning("depth capture: building row for %s failed (%s), skipped", symbol, exc)
        return None


# ── Persistence ───────────────────────────────────────────────────────────────
async def persist_depth(
    raw: Dict[str, Dict[str, Any]],
    ts: datetime,
    trade_date,
    *,
    meta: Optional[Dict[str, Tuple[Optional[int], Optional[str]]]] = None,
    expiry_date=None,
    session_id: Optional[str] = None,
    session_factory=None,
) -> Optional[int]:
    """Write one poll's snapshots. Returns rows written, or None on failure.

    Rows are validated individually and invalid ones are skipped, so one bad
    quote never costs the rest of the poll. If the batch commit still fails,
    rows are retried one at a time to isolate the offender.
    """
    if not raw:
        return 0
    factory = session_factory or AsyncSessionLocal
    try:
        rows = []
        for symbol, quote in raw.items():
            row = _build_row(symbol, quote, ts, trade_date, meta, expiry_date, session_id)
            if row is not None:
                rows.append(row)
        if not rows:
            return 0
        try:
            async with factory() as db:
                db.add_all(rows)
                await db.commit()
            return len(rows)
        except Exception as exc:
            # Unexpected at this point — validation should have caught bad data.
            # Retry individually so we lose only the offending row.
            log.warning("depth capture: batch commit failed (%s), retrying rows", exc)
            written = 0
            for row in rows:
                try:
                    async with factory() as db:
                        db.add(row)
                        await db.commit()
                    written += 1
                except Exception as row_exc:
                    log.warning("depth capture: row %s failed: %s", row.symbol, row_exc)
            return written
    except Exception as exc:
        # Analytics only — never propagate into the trading loop.
        log.warning("depth capture failed at %s: %s", ts, exc)
        return None


# ── Bounded background writer ────────────────────────────────────────────────
_queue: Optional[asyncio.Queue] = None
_worker: Optional[asyncio.Task] = None
_queue_loop: Optional[asyncio.AbstractEventLoop] = None
_dropped = 0


async def _writer_loop(queue: asyncio.Queue) -> None:
    """Drain the queue forever. One writer = one pooled connection, at most."""
    while True:
        item = await queue.get()
        try:
            if item is None:            # shutdown sentinel
                return
            await persist_depth(**item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("depth writer: item failed: %s", exc)
        finally:
            queue.task_done()


def _ensure_worker() -> Optional[asyncio.Queue]:
    global _queue, _worker, _queue_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None                     # no loop (e.g. sync test) — skip capture
    # Rebuild if never started, if the worker died, or if we are on a different
    # loop than the queue was created on (queues are not portable across loops).
    if _queue is None or _worker is None or _worker.done() or _queue_loop is not loop:
        _queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        _queue_loop = loop
        _worker = loop.create_task(_writer_loop(_queue), name="depth-writer")
    return _queue


def capture_depth(
    raw: Dict[str, Dict[str, Any]],
    ts: datetime,
    trade_date,
    *,
    meta: Optional[Dict[str, Tuple[Optional[int], Optional[str]]]] = None,
    expiry_date=None,
    session_id: Optional[str] = None,
) -> bool:
    """Queue one poll's depth for writing. Non-blocking; never raises.

    Returns True if queued. False means dropped — by design, not by error.
    """
    global _dropped
    try:
        if not raw:
            return False
        queue = _ensure_worker()
        if queue is None:
            return False
        queue.put_nowait({
            "raw": raw,
            "ts": ts,
            "trade_date": trade_date,
            "meta": meta,
            "expiry_date": expiry_date,
            "session_id": session_id,
        })
        return True
    except asyncio.QueueFull:
        _dropped += 1
        if _dropped % 50 == 1:          # throttle: a stalled DB would flood logs
            log.warning(
                "depth capture: queue full, snapshot dropped (%d dropped so far)", _dropped
            )
        return False
    except Exception as exc:
        log.warning("depth capture: enqueue failed: %s", exc)
        return False


async def stop_depth_writer(timeout: float = 5.0) -> None:
    """Flush and stop the writer. Called from application shutdown."""
    global _queue, _worker, _queue_loop
    queue, worker = _queue, _worker
    _queue = _worker = _queue_loop = None
    if queue is None or worker is None or worker.done():
        return
    try:
        queue.put_nowait(None)
        await asyncio.wait_for(worker, timeout=timeout)
    except (asyncio.QueueFull, asyncio.TimeoutError):
        worker.cancel()
    except Exception as exc:
        log.warning("depth writer: shutdown failed: %s", exc)
