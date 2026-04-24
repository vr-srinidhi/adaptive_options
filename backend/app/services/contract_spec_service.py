"""
Contract specification service.

Resolves lot size, strike step, and expiry for any instrument + date combination
by querying instrument_contract_specs (seeded at startup).

Public API
----------
get_contract_spec(db, instrument, trade_date)   → ContractSpec
resolve_atm_strike(spot_close, strike_step)     → int
resolve_expiry(db, instrument, trade_date, entry_time_str, *, require_vix=False) → ExpiryResult
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_type, datetime, time
from typing import List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.historical import OptionsCandle, VixCandle
from app.models.strategy_run import InstrumentContractSpec

log = logging.getLogger(__name__)


@dataclass
class ContractSpec:
    instrument: str
    lot_size: int
    strike_step: int
    weekly_expiry_weekday: int
    estimated_margin_per_lot: float


@dataclass
class ExpiryResult:
    expiry: date_type
    warnings: List[str]


async def get_contract_spec(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
) -> ContractSpec:
    """
    Return the contract spec effective on trade_date.

    Falls back to hardcoded NIFTY defaults if no row is found so the engine
    never hard-crashes on a missing seed.
    """
    result = await db.execute(
        select(InstrumentContractSpec)
        .where(
            InstrumentContractSpec.instrument == instrument,
            InstrumentContractSpec.effective_from <= trade_date,
            (InstrumentContractSpec.effective_to >= trade_date)
            | (InstrumentContractSpec.effective_to.is_(None)),
        )
        .order_by(InstrumentContractSpec.effective_from.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        log.warning(
            "No contract spec found for %s on %s — using hardcoded NIFTY defaults",
            instrument, trade_date,
        )
        return ContractSpec(
            instrument=instrument,
            lot_size=75,
            strike_step=50,
            weekly_expiry_weekday=3,
            estimated_margin_per_lot=180_000.0,
        )
    return ContractSpec(
        instrument=instrument,
        lot_size=row.lot_size,
        strike_step=row.strike_step,
        weekly_expiry_weekday=row.weekly_expiry_weekday,
        estimated_margin_per_lot=float(row.estimated_margin_per_lot or 180_000),
    )


def resolve_atm_strike(spot_close: float, strike_step: int) -> int:
    """Round spot to nearest valid strike step."""
    return int(round(spot_close / strike_step) * strike_step)


async def resolve_expiry(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
    entry_time_str: str,
    *,
    require_vix: bool = False,
) -> ExpiryResult:
    """
    Find the nearest weekly expiry >= trade_date that has both CE + PE
    coverage near ATM at the entry minute.

    Falls back to the next eligible expiry if the nearest one lacks data.
    Returns an ExpiryResult with the chosen expiry and any warnings.
    """
    warnings: List[str] = []

    # Parse entry time
    try:
        h, m = map(int, entry_time_str.split(":"))
        entry_time = time(h, m)
    except Exception:
        entry_time = time(9, 50)
        warnings.append(f"Could not parse entry_time '{entry_time_str}'; defaulted to 09:50.")

    entry_dt = datetime.combine(trade_date, entry_time)

    # Fetch all available expiries for this day
    rows = (await db.execute(
        text(
            "SELECT DISTINCT expiry_date FROM options_candles "
            "WHERE symbol = :sym AND trade_date = :td AND expiry_date >= :td "
            "ORDER BY expiry_date ASC LIMIT 10"
        ),
        {"sym": instrument, "td": trade_date},
    )).fetchall()

    if not rows:
        raise ValueError(
            f"No option data found for {instrument} on {trade_date}. "
            "Ensure data has been ingested into the warehouse."
        )

    # Try each expiry until we find one with CE + PE at the entry minute
    for (expiry,) in rows:
        count_result = (await db.execute(
            select(OptionsCandle)
            .where(
                OptionsCandle.symbol == instrument,
                OptionsCandle.trade_date == trade_date,
                OptionsCandle.expiry_date == expiry,
                OptionsCandle.timestamp == entry_dt,
            )
            .limit(4)
        )).scalars().all()

        option_types_available = {r.option_type for r in count_result}
        if "CE" in option_types_available and "PE" in option_types_available:
            if expiry != rows[0][0]:
                warnings.append(
                    f"Nearest expiry {rows[0][0]} had no CE/PE data at entry; "
                    f"using {expiry} instead."
                )
            return ExpiryResult(expiry=expiry, warnings=warnings)

    raise ValueError(
        f"No expiry with both CE and PE data at {entry_time_str} found for "
        f"{instrument} on {trade_date}."
    )


async def get_spot_at_entry(
    db: AsyncSession,
    instrument: str,
    trade_date: date_type,
    entry_time_str: str,
) -> Optional[float]:
    """Return the spot close at the entry minute, or None if not available."""
    try:
        h, m = map(int, entry_time_str.split(":"))
        entry_dt = datetime.combine(trade_date, time(h, m))
    except Exception:
        return None

    from app.models.historical import SpotCandle
    result = await db.execute(
        select(SpotCandle)
        .where(
            SpotCandle.symbol == instrument,
            SpotCandle.trade_date == trade_date,
            SpotCandle.timestamp == entry_dt,
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return float(row.close) if row and row.close is not None else None


async def get_vix_at_entry(
    db: AsyncSession,
    trade_date: date_type,
    entry_time_str: str,
) -> Optional[float]:
    """Return the VIX close at the entry minute, or None if not available."""
    try:
        h, m = map(int, entry_time_str.split(":"))
        entry_dt = datetime.combine(trade_date, time(h, m))
    except Exception:
        return None

    result = await db.execute(
        select(VixCandle)
        .where(
            VixCandle.trade_date == trade_date,
            VixCandle.timestamp == entry_dt,
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return float(row.close) if row and row.close is not None else None


def resolve_leg_strikes(
    leg_template: list,
    atm_strike: int,
    strike_step: int,
) -> List[Tuple[str, str, int]]:
    """
    Resolve concrete (side, option_type, strike) tuples from a leg template.

    leg_template entry: {"side": "SELL", "option_type": "CE", "strike_offset_steps": 0}
    strike_offset_steps=0  → ATM
    strike_offset_steps=-2 → ATM - 2 × strike_step
    """
    resolved = []
    for leg in leg_template:
        offset = leg.get("strike_offset_steps", 0)
        strike = atm_strike + offset * strike_step
        resolved.append((leg["side"], leg["option_type"], strike))
    return resolved
