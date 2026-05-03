"""
APScheduler setup for the live paper trading engine.

The scheduler runs inside the FastAPI process and uses the same asyncio event
loop.  It fires one job every weekday at 09:14 IST which calls
live_paper_engine.start_live_session().

Startup recovery: check_and_resume_sessions() is also called at startup to
re-launch any session that was interrupted by a mid-day process restart
(e.g. Railway redeploy).
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
scheduler = AsyncIOScheduler(timezone=IST)


async def _fire_daily_session() -> None:
    """APScheduler async job — runs at 09:14 IST on weekdays."""
    from app.database import AsyncSessionLocal
    from app.services.live_paper_engine import start_live_session

    log.info("Scheduler: firing daily live paper session job.")
    async with AsyncSessionLocal() as db:
        await start_live_session(db)


def init_scheduler() -> None:
    """Register jobs and start the scheduler.  Called once at FastAPI startup."""
    scheduler.add_job(
        _fire_daily_session,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=14, timezone=IST),
        id="daily_live_paper",
        replace_existing=True,
        misfire_grace_time=300,   # allow up to 5 min late if process was slow to start
    )
    scheduler.start()
    log.info("APScheduler started — daily live paper job registered (09:14 IST, weekdays).")


def shutdown_scheduler() -> None:
    """Graceful shutdown — called at FastAPI teardown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("APScheduler shut down.")
