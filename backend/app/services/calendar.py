"""
NSE trading-day calendar.

Holidays are sourced from official NSE exchange circulars for 2023-2026.
Each entry is a date on which NSE equity & F&O markets are closed.

Usage:
    from app.services.calendar import classify_date, get_trading_days, TRADING_DAY

    day_type = classify_date(date(2024, 1, 26))   # → "HOLIDAY"
    days = get_trading_days(start, end)            # → [(date, str), ...]
"""
from datetime import date, timedelta
from typing import List, Tuple

# ── NSE official holidays 2023-2026 ──────────────────────────────────────────
# Source: NSE India circulars (equity segment trading holidays)
_NSE_HOLIDAYS: frozenset = frozenset({
    # 2023
    date(2023, 1, 26),   # Republic Day
    date(2023, 3, 7),    # Holi
    date(2023, 3, 30),   # Ram Navami
    date(2023, 4, 4),    # Mahavir Jayanti
    date(2023, 4, 7),    # Good Friday
    date(2023, 4, 14),   # Dr. Ambedkar Jayanti
    date(2023, 5, 1),    # Maharashtra Day
    date(2023, 6, 28),   # Bakri Id
    date(2023, 8, 15),   # Independence Day
    date(2023, 9, 19),   # Ganesh Chaturthi
    date(2023, 10, 2),   # Gandhi Jayanti / Dussehra
    date(2023, 10, 24),  # Diwali-Laxmi Puja
    date(2023, 11, 14),  # Diwali-Balipratipada
    date(2023, 11, 27),  # Gurunanak Jayanti
    date(2023, 12, 25),  # Christmas

    # 2024
    date(2024, 1, 22),   # Ram Mandir consecration (special holiday)
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 8),    # Mahashivratri
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Id-Ul-Fitr (Eid)
    date(2024, 4, 14),   # Dr. Ambedkar Jayanti / Tamil New Year
    date(2024, 4, 17),   # Ram Navami
    date(2024, 4, 21),   # Mahavir Jayanti
    date(2024, 5, 23),   # Buddha Purnima
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Muharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10, 2),   # Gandhi Jayanti
    date(2024, 11, 1),   # Diwali-Laxmi Puja
    date(2024, 11, 15),  # Diwali-Balipratipada / Gurunanak Jayanti
    date(2024, 11, 20),  # Maharashtra Election
    date(2024, 12, 25),  # Christmas

    # 2025
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Eid)
    date(2025, 4, 10),   # Shri Ram Navami
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 6, 7),    # Bakri Id
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Gandhi Jayanti / Dussehra
    date(2025, 10, 20),  # Diwali-Laxmi Puja
    date(2025, 10, 23),  # Diwali-Balipratipada
    date(2025, 11, 5),   # Gurunanak Jayanti
    date(2025, 12, 25),  # Christmas

    # 2026 (provisional — update when NSE publishes official list)
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi (approximate)
    date(2026, 4, 3),    # Good Friday (approximate)
    date(2026, 4, 10),   # Id-Ul-Fitr (approximate)
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 30),  # Diwali (approximate)
    date(2026, 11, 5),   # Gurunanak Jayanti (approximate)
    date(2026, 12, 25),  # Christmas
})

# ── Day classification constants ──────────────────────────────────────────────
TRADING_DAY = "TRADING_DAY"
HOLIDAY     = "HOLIDAY"
WEEKEND     = "WEEKEND"


def classify_date(d: date) -> str:
    """
    Return the trading classification for *d*:
      "WEEKEND"      — Saturday or Sunday
      "HOLIDAY"      — NSE declared holiday (weekday)
      "TRADING_DAY"  — normal trading day
    """
    if d.weekday() >= 5:
        return WEEKEND
    if d in _NSE_HOLIDAYS:
        return HOLIDAY
    return TRADING_DAY


def is_trading_day(d: date) -> bool:
    return classify_date(d) == TRADING_DAY


def get_trading_days(start: date, end: date) -> List[Tuple[date, str]]:
    """
    Return all calendar dates in [start, end] with their classification.
    Includes TRADING_DAY, HOLIDAY, and WEEKEND entries (no dates are skipped).
    """
    result: List[Tuple[date, str]] = []
    current = start
    while current <= end:
        result.append((current, classify_date(current)))
        current += timedelta(days=1)
    return result


def only_trading_days(start: date, end: date) -> List[date]:
    """Return only the TRADING_DAY dates in [start, end]."""
    return [d for d, t in get_trading_days(start, end) if t == TRADING_DAY]
