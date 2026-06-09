"""
FiniexTestingIDE - Persistence Types
Runtime domain types for the algo state persistence layer (#354).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RestoreContext:
    """
    Context passed to an algo when its persisted state is being restored (#354).

    The framework measures the timing values (an algo must never read wall-clock
    itself — §9). `trading_days` is weekend-aware on markets that close on
    weekends (Forex); on 24/7 markets (crypto) it equals the calendar-day count.

    Args:
        saved_at_utc: When the snapshot was written (UTC, from the envelope)
        now_utc: Current time at restore, measured by the framework (UTC)
        age_seconds: Wall-clock age of the snapshot (now_utc - saved_at_utc)
        trading_days: Trading days between save and restore (weekend-aware on
            weekend-closing markets; == calendar days on 24/7 markets)
        weekend_aware: True if the market closes on weekends (Forex), False for 24/7
    """
    saved_at_utc: datetime
    now_utc: datetime
    age_seconds: float
    trading_days: int
    weekend_aware: bool
