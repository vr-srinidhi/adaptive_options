"""
REST endpoints per PRD Section 5.
"""
import uuid
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.session import BacktestSession
from app.services.simulator import run_day_simulation

router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────
class RunBacktestRequest(BaseModel):
    instrument: str
    startDate: str
    endDate: str
    capital: float


# ── Helpers ───────────────────────────────────────────────────────────────────
def _trading_days(start: date, end: date) -> List[date]:
    days, current = [], start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _to_dict(s: BacktestSession, full: bool = True) -> dict:
    d = {
        "id": str(s.id),
        "instrument": s.instrument,
        "session_date": str(s.session_date),
        "capital": float(s.capital),
        "regime": s.regime,
        "iv_rank": s.iv_rank,
        "strategy": s.strategy,
        "entry_time": str(s.entry_time) if s.entry_time else None,
        "exit_time": str(s.exit_time) if s.exit_time else None,
        "exit_reason": s.exit_reason,
        "spot_in": float(s.spot_in) if s.spot_in is not None else None,
        "spot_out": float(s.spot_out) if s.spot_out is not None else None,
        "lots": s.lots,
        "max_profit": float(s.max_profit) if s.max_profit is not None else None,
        "max_loss": float(s.max_loss) if s.max_loss is not None else None,
        "pnl": float(s.pnl) if s.pnl is not None else 0.0,
        "pnl_pct": float(s.pnl_pct) if s.pnl_pct is not None else 0.0,
        "wl": s.wl,
        "ema5": float(s.ema5) if s.ema5 is not None else None,
        "ema20": float(s.ema20) if s.ema20 is not None else None,
        "rsi14": float(s.rsi14) if s.rsi14 is not None else None,
        "created_at": str(s.created_at) if s.created_at else None,
    }
    if full:
        d["legs"] = s.legs or []
        d["min_data"] = s.min_data or []
    return d


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.post("/backtest/run")
async def run_backtest(req: RunBacktestRequest, db: AsyncSession = Depends(get_db)):
    try:
        start = date.fromisoformat(req.startDate)
        end = date.fromisoformat(req.endDate)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if end < start:
        raise HTTPException(status_code=400, detail="End date must be ≥ start date.")

    instrument = req.instrument.strip().upper()
    if instrument not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(status_code=400, detail="instrument must be NIFTY or BANKNIFTY.")

    if not (50_000 <= req.capital <= 10_000_000):
        raise HTTPException(status_code=400, detail="Capital must be ₹50,000 – ₹10,000,000.")

    days = _trading_days(start, end)
    if not days:
        raise HTTPException(status_code=400, detail="No trading days in selected range.")
    if len(days) > 60:
        raise HTTPException(status_code=400, detail="Maximum 60 trading days per run.")

    sessions = []
    for td in days:
        result = run_day_simulation(td, instrument, req.capital)
        obj = BacktestSession(
            instrument=result["instrument"],
            session_date=result["session_date"],
            capital=result["capital"],
            regime=result["regime"],
            iv_rank=result["iv_rank"],
            strategy=result["strategy"],
            entry_time=result["entry_time"],
            exit_time=result["exit_time"],
            exit_reason=result["exit_reason"],
            spot_in=result["spot_in"],
            spot_out=result["spot_out"],
            lots=result["lots"],
            max_profit=result["max_profit"],
            max_loss=result["max_loss"],
            pnl=result["pnl"],
            pnl_pct=result["pnl_pct"],
            wl=result["wl"],
            ema5=result["ema5"],
            ema20=result["ema20"],
            rsi14=result["rsi14"],
            legs=result["legs"],
            min_data=result["min_data"],
        )
        db.add(obj)
        sessions.append(obj)

    await db.commit()
    for s in sessions:
        await db.refresh(s)

    return [_to_dict(s) for s in sessions]


@router.get("/backtest/results")
async def get_results(
    instrument: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestSession).order_by(BacktestSession.session_date.desc())
    if instrument:
        q = q.where(BacktestSession.instrument == instrument.strip().upper())
    q = q.limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [_to_dict(s, full=False) for s in rows]


@router.get("/backtest/results/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    row = (await db.execute(
        select(BacktestSession).where(BacktestSession.id == sid)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found.")
    return _to_dict(row, full=True)


@router.get("/backtest/summary")
async def get_summary(
    instrument: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(BacktestSession)
    if instrument:
        q = q.where(BacktestSession.instrument == instrument.strip().upper())
    rows = (await db.execute(q)).scalars().all()

    if not rows:
        return {"totalPnl": 0, "winRate": 0, "totalTrades": 0,
                "bestDay": None, "worstDay": None, "totalSessions": 0}

    tradeable = [s for s in rows if s.wl != "NO_TRADE"]
    wins = [s for s in tradeable if s.wl == "WIN"]
    total_pnl = sum(float(s.pnl or 0) for s in rows)
    win_rate = round(len(wins) / len(tradeable) * 100) if tradeable else 0

    best = max(tradeable, key=lambda s: float(s.pnl or 0), default=None)
    worst = min(tradeable, key=lambda s: float(s.pnl or 0), default=None)

    return {
        "totalPnl": round(total_pnl, 2),
        "winRate": win_rate,
        "totalTrades": len(tradeable),
        "totalSessions": len(rows),
        "bestDay": _to_dict(best, full=False) if best else None,
        "worstDay": _to_dict(worst, full=False) if worst else None,
    }


@router.delete("/backtest/results")
async def clear_results(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count()).select_from(BacktestSession))
    count = result.scalar() or 0
    await db.execute(delete(BacktestSession))
    await db.commit()
    return {"deleted": count}
