"""
FiniexTestingIDE - Log Record Types
The buffered log entry — the fact, not its rendering.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from python.framework.types.log_level import LogLevel


@dataclass
class LogRecord:
    """
    One buffered log entry.

    Carries the FACT. Rendering — the timestamp format, the level column, the colours, the
    tick prefix — happens at the surface that prints it, never here: a buffer that holds a
    rendered line forces every later consumer to take the fact apart again, and the run report
    is such a consumer.

    The two time fields are the pair §9 describes and must not be conflated: `timestamp` is
    OBSERVATION time (when we recorded it, wall-clock, the ts_init analogue), `tick_time` is
    EVENT time (the tick's own canonical time, the ts_event analogue). A report that asks
    "at which tick did this happen" reads the second, never the first.

    Args:
        level: The entry's level
        timestamp: When it was observed (UTC, tz-aware) — observation time, never event time
        scope: The unit it belongs to (scenario name / profile name); '' means run-wide, the
            same meaning the field has on WarningRow
        message: Operator-readable text, unrendered — no prefix, no colour, no timestamp
        tick_index: The tick being processed when this was logged; None outside the tick loop
        tick_time: That tick's own time (canonical clock); None outside the tick loop
    """
    level: LogLevel
    timestamp: datetime
    scope: str
    message: str
    tick_index: Optional[int] = None
    tick_time: Optional[datetime] = None
