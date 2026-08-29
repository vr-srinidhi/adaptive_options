"""
Best-effort persistence of order-book depth seen during the live paper poll.

Design rule: **this must never affect trading.** Every entry point swallows its
own exceptions and returns None. A capture failure loses one row of analytics;
it must not lose a hedge, an exit, or a session.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from app.database import AsyncSessionLocal
from app.models.option_depth import OptionDepthSnapshot

log = logging.getLogger(__name__)

# NFO:NIFTY25AUG24300CE  ->  strike 24300, type CE
_SYM_RE = re.compile(r"(\d+)(CE|PE)$")


def _parse_symbol(symbol: str) -> tuple[Optional[int], Optional[str]]:
    m = _SYM_RE.search(symbol or "")
    if not m:
        return None, None
    try:
        return int(m.group(1)), m.group(2)
    except Exception:
        return None, m.group(2)


def _top_of_book(quote: Dict[str, Any]) -> Dict[str, Any]:
    """Pull best bid/ask out of the depth ladder, tolerating a missing ladder."""
    out = {"bid": None, "ask": None, "bid_qty": None, "ask_qty": None, "depth": None}
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


async def persist_depth(
    raw: Dict[str, Dict[str, Any]],
    ts: datetime,
    trade_date,
    session_id: Optional[str] = None,
) -> Optional[int]:
    """Store one poll's worth of quotes. Returns rows written, or None on failure.

    `raw` is the payload from zerodha_client.fetch_quote_with_depth.
    """
    if not raw:
        return 0
    try:
        rows = []
        for symbol, q in raw.items():
            strike, opt_type = _parse_symbol(symbol)
            tob = _top_of_book(q)
            rows.append(OptionDepthSnapshot(
                trade_date=trade_date,
                timestamp=ts.replace(tzinfo=None),   # column is timezone=False
                symbol=symbol,
                strike=strike,
                option_type=opt_type,
                last_price=q.get("last_price"),
                bid=tob["bid"],
                ask=tob["ask"],
                bid_qty=tob["bid_qty"],
                ask_qty=tob["ask_qty"],
                depth_json=tob["depth"],
                volume=q.get("volume") or q.get("volume_traded"),
                open_interest=q.get("oi"),
                session_id=str(session_id) if session_id else None,
            ))
        async with AsyncSessionLocal() as db:
            db.add_all(rows)
            await db.commit()
        return len(rows)
    except Exception as exc:
        # Analytics only — never propagate into the trading loop.
        log.warning("depth capture failed at %s: %s", ts, exc)
        return None
