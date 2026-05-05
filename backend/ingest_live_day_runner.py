"""
One-shot runner: pull today's Zerodha candle data into warehouse tables.

Run inside the backend container:
  docker exec adaptive_options_api python /app/ingest_live_day_runner.py

Uses the Zerodha token already stored in broker_tokens for today.
Inserts NIFTY spot, India VIX, and NIFTY options (3 expiries) into
spot_candles, vix_candles, options_candles, and marks trading_days
backtest_ready=True.
"""
import asyncio
import logging
import os
import sys
from datetime import date

# Add /app to path so app.* imports work
sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("live_ingest_runner")


async def main() -> None:
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.broker_token import BrokerToken
    from app.core.security import decrypt_token
    from app.services.live_ingestion import ingest_live_day

    force = "--force" in sys.argv
    trade_date = date.today()
    log.info("=== Live day ingestion for %s  force=%s ===", trade_date, force)

    async with AsyncSessionLocal() as db:
        # Read and decrypt the stored Zerodha token
        row = (await db.execute(
            select(BrokerToken).where(BrokerToken.broker == "ZERODHA")
        )).scalar_one_or_none()

        if row is None:
            log.error("No Zerodha token found in broker_tokens. POST /api/auth/zerodha/session first.")
            return

        if row.token_date and row.token_date < trade_date:
            log.error(
                "Zerodha token is from %s — expired (tokens expire at 6 AM IST daily).",
                row.token_date,
            )
            return

        access_token = decrypt_token(row.encrypted_token)
        log.info("Decrypted token for user_id=%s  date=%s", row.user_id, row.token_date)

    # Run ingestion in its own session (commit happens inside)
    async with AsyncSessionLocal() as db:
        result = await ingest_live_day(db, access_token, trade_date, force=force)

    log.info("=== Done ===")
    log.info("trade_date : %s", result["trade_date"])
    log.info("status     : %s", result["status"])
    log.info("spot_rows  : %s", result["spot_rows"])
    log.info("vix_rows   : %s", result["vix_rows"])
    log.info("options_rows: %s", result["options_rows"])
    log.info("contracts  : %s", result["option_contracts"])
    if result.get("notes"):
        log.warning("notes: %s", result["notes"])


if __name__ == "__main__":
    asyncio.run(main())
