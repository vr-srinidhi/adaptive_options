"""
Post-hoc execution cost: what a session's fills would have cost against the
order book that was actually on offer.

Every P&L figure this system reports assumes we transacted at the observed
price. Real fills happen at the bid when selling and the ask when buying, and
walk deeper into the book once size exceeds what rests at the touch. This
module re-prices recorded fills against captured depth to measure that gap.

Read-only and entirely offline. It never runs during a session, never places
an order, and no trading path reads its output.

Two bounds are reported rather than one number:

  walked   fill from the touch outward, consuming real resting size  (pessimistic)
  mid      fill at (bid+ask)/2                                       (optimistic)

The truth sits between them. Quoting a single point estimate from a model
calibrated on depth alone would imply a precision we have not earned -- until
real orders are placed there is no ground truth to check it against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# A fill is matched to the depth snapshot nearest in time. Polls are ~10s
# apart, so anything further away than this is not describing the same book.
DEFAULT_TOLERANCE_SECONDS = 30.0


# ── Book math (pure) ─────────────────────────────────────────────────────────
def walk_book(levels: Sequence[Dict[str, Any]], qty: int, *, buying: bool) -> Tuple[Optional[float], int]:
    """Consume `qty` from the touch outward. Returns (avg_price, filled_qty).

    Selling consumes bids (highest first); buying consumes asks (lowest first).
    If the visible book cannot fill the order, the average covers only what was
    filled and the caller sees the shortfall in `filled_qty`.
    """
    if not levels or qty <= 0:
        return None, 0
    usable = []
    for lv in levels:
        try:
            price = float(lv.get("price"))
            available = int(lv.get("quantity") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if price > 0 and available > 0:
            usable.append((price, available))
    if not usable:
        return None, 0
    # Defensive: Zerodha returns best-first, but do not depend on it.
    usable.sort(key=lambda x: x[0], reverse=not buying)

    remaining, notional, filled = qty, 0.0, 0
    for price, available in usable:
        take = min(remaining, available)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled == 0:
        return None, 0
    return notional / filled, filled


def mid_price(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def slippage_per_unit(side: str, assumed: float, realistic: float) -> float:
    """Cost per unit, positive = worse than assumed.

    Selling below the assumed price and buying above it both cost money.
    """
    return (assumed - realistic) if side.upper() == "SELL" else (realistic - assumed)


# ── Fill re-pricing ──────────────────────────────────────────────────────────
@dataclass
class FillCost:
    label: str
    side: str
    option_type: str
    strike: int
    quantity: int
    timestamp: Optional[datetime]
    assumed_price: Optional[float] = None
    touch_price: Optional[float] = None
    walked_price: Optional[float] = None
    mid: Optional[float] = None
    filled_qty: int = 0
    shortfall_qty: int = 0
    cost_walked: Optional[float] = None
    cost_mid: Optional[float] = None
    note: Optional[str] = None

    @property
    def priced(self) -> bool:
        return self.cost_walked is not None

    @property
    def fully_priced(self) -> bool:
        """Priced with the whole order absorbed by the visible book."""
        return self.priced and self.shortfall_qty == 0

    @property
    def has_mid(self) -> bool:
        return self.cost_mid is not None


def price_fill(
    *,
    label: str,
    side: str,
    option_type: str,
    strike: int,
    quantity: int,
    assumed_price: Optional[float],
    timestamp: Optional[datetime],
    depth: Optional[Dict[str, Any]],
) -> FillCost:
    """Re-price one fill against the book. Never raises; unpriceable fills are
    returned with a note so they are visible in the report rather than being
    silently treated as costless."""
    out = FillCost(label=label, side=side, option_type=option_type, strike=strike,
                   quantity=quantity, timestamp=timestamp, assumed_price=assumed_price)
    if assumed_price is None:
        out.note = "no recorded price"
        return out
    if depth is None:
        out.note = "no depth captured"
        return out

    buying = side.upper() == "BUY"
    ladder = (depth.get("depth_json") or {})
    levels = ladder.get("sell") if buying else ladder.get("buy")

    bid, ask = depth.get("bid"), depth.get("ask")
    out.touch_price = ask if buying else bid
    out.mid = mid_price(bid, ask)

    walked, filled = walk_book(levels or [], quantity, buying=buying)
    if walked is None:
        # Without a ladder we know the touch price but not the size behind it.
        # Costing the whole order at the touch would assume unlimited depth
        # there, which is exactly the flattering assumption this report exists
        # to remove, so the walked bound is left unpriced.
        out.note = "no ladder" if out.touch_price is not None else "no bid/ask or ladder"
        return out

    # Cost only what the visible book could actually absorb. The unfilled
    # remainder is reported as a shortfall rather than imputed at the average
    # of the filled portion, which would understate the pessimistic bound.
    out.walked_price = round(walked, 4)
    out.filled_qty = filled
    out.shortfall_qty = max(0, quantity - filled)
    out.cost_walked = round(slippage_per_unit(side, assumed_price, walked) * filled, 2)
    if out.mid is not None:
        out.cost_mid = round(slippage_per_unit(side, assumed_price, out.mid) * filled, 2)
    if out.shortfall_qty:
        note = f"book covered {filled} of {quantity}"
        out.note = f"{out.note}; {note}" if out.note else note
    return out


# ── Depth lookup ─────────────────────────────────────────────────────────────
def nearest_depth(
    snapshots: Sequence[Dict[str, Any]],
    timestamp: Optional[datetime],
    *,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Closest snapshot in time, or None if nothing is close enough.

    Returning None rather than the nearest-at-any-distance is deliberate: a
    book from ten minutes away is not evidence about this fill.
    """
    if not snapshots or timestamp is None:
        return None
    best, best_gap = None, None
    for snap in snapshots:
        ts = snap.get("timestamp")
        if ts is None:
            continue
        gap = abs((ts - timestamp).total_seconds())
        if best_gap is None or gap < best_gap:
            best, best_gap = snap, gap
    if best_gap is None or best_gap > tolerance_seconds:
        return None
    return best


def contract_key(row: Dict[str, Any]) -> Optional[Tuple[Any, int, str]]:
    """Identity of the contract a depth row or fill refers to.

    Strike and option type alone are not an identity: the same 24300 CE exists
    on every expiry, so a current-expiry fill could otherwise be matched
    against a next-expiry book that happens to sit nearer in time. Expiry is
    the discriminator both depth rows and legs carry.
    """
    strike, opt, expiry = row.get("strike"), row.get("option_type"), row.get("expiry_date")
    if strike is None or opt is None:
        return None
    try:
        return (expiry, int(strike), str(opt))
    except (TypeError, ValueError):
        return None


def index_depth(rows: Sequence[Dict[str, Any]]) -> Dict[Tuple[Any, int, str], List[Dict[str, Any]]]:
    """Group depth rows by full contract identity for lookup."""
    out: Dict[Tuple[Any, int, str], List[Dict[str, Any]]] = {}
    for row in rows:
        key = contract_key(row)
        if key is None:
            continue
        out.setdefault(key, []).append(row)
    return out


# ── Session roll-up ──────────────────────────────────────────────────────────
@dataclass
class SessionCost:
    run_id: str
    trade_date: Any
    fills: List[FillCost] = field(default_factory=list)

    @property
    def priced_fills(self) -> List[FillCost]:
        return [f for f in self.fills if f.priced]

    @property
    def unpriced_fills(self) -> List[FillCost]:
        return [f for f in self.fills if not f.priced]

    @property
    def cost_walked(self) -> float:
        return round(sum(f.cost_walked or 0.0 for f in self.priced_fills), 2)

    @property
    def cost_mid(self) -> Optional[float]:
        """Midpoint bound, or None when any priced fill has no midpoint.

        A one-sided book yields a walked cost but no midpoint. Summing only the
        fills that have one would report a confident optimistic bound built
        from a subset of the position, so the bound is withheld instead.
        """
        priced = self.priced_fills
        if not priced or any(not f.has_mid for f in priced):
            return None
        return round(sum(f.cost_mid or 0.0 for f in priced), 2)

    @property
    def coverage(self) -> float:
        """Fraction of fills we could price at all. A cost figure from 30%
        coverage is not a session cost, and the report must say so."""
        return round(len(self.priced_fills) / len(self.fills), 3) if self.fills else 0.0

    @property
    def quantity_coverage(self) -> float:
        """Fraction of total order quantity the visible book could absorb.

        Distinct from `coverage`: every fill can be priced while the book still
        covered only part of each order.
        """
        total = sum(f.quantity for f in self.fills)
        if not total:
            return 0.0
        return round(sum(f.filled_qty for f in self.priced_fills) / total, 3)

    @property
    def complete(self) -> bool:
        """True only if every fill was priced, in full, on both bounds.

        Anything less means the cost total covers part of the session, and a
        session-level P&L adjustment built on it would silently treat the rest
        as costless.
        """
        return (
            bool(self.fills)
            and not self.unpriced_fills
            and all(f.fully_priced for f in self.fills)
            and self.cost_mid is not None
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trade_date": str(self.trade_date),
            "fills_total": len(self.fills),
            "fills_priced": len(self.priced_fills),
            "coverage": self.coverage,
            "quantity_coverage": self.quantity_coverage,
            "complete": self.complete,
            "cost_mid": self.cost_mid,
            "cost_walked": self.cost_walked,
            "shortfall_fills": sum(1 for f in self.priced_fills if f.shortfall_qty),
        }


def cost_session(
    *,
    run_id: str,
    trade_date: Any,
    fills: Sequence[Dict[str, Any]],
    depth_rows: Sequence[Dict[str, Any]],
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
) -> SessionCost:
    """Re-price every fill in one session.

    `fills` items: label, side, option_type, strike, quantity, price, timestamp.
    """
    by_contract = index_depth(depth_rows)
    out = SessionCost(run_id=str(run_id), trade_date=trade_date)
    for fill in fills:
        key = contract_key(fill)
        if key is None:
            continue
        _expiry, strike, opt = key
        snaps = by_contract.get(key, [])
        depth = nearest_depth(snaps, fill.get("timestamp"), tolerance_seconds=tolerance_seconds)
        out.fills.append(price_fill(
            label=str(fill.get("label", "")),
            side=str(fill.get("side", "")),
            option_type=opt,
            strike=strike,
            quantity=int(fill.get("quantity") or 0),
            assumed_price=(float(fill["price"]) if fill.get("price") is not None else None),
            timestamp=fill.get("timestamp"),
            depth=depth,
        ))
    return out
