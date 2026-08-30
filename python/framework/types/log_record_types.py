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
    event-time column — happens at the surface that prints it, never here: a buffer that holds a
    rendered line forces every later consumer to take the fact apart again, and the run report
    is such a consumer.

    The two time fields are the pair §9 describes and must not be conflated: `timestamp` is
    OBSERVATION time (when we recorded it, wall-clock, the ts_init analogue), `event_time` is
    EVENT time (the run's own canonical clock, the ts_event analogue). A report that asks
    "when in the run did this happen" reads the second, never the first.

    `event_time` comes from the canonical clock rather than from a tick, because a tick is only
    one of the things that drives a pass: a heartbeat / ghost interval advances the clock with
    no tick at all, and #375 adds timer and resolution events beside it. A tick INDEX would
    describe one of those three and mislabel the other two, so it is deliberately absent.

    Args:
        level: The entry's level
        timestamp: When it was observed (UTC, tz-aware) — observation time, never event time
        scope: The unit it belongs to (scenario name / profile name); '' means run-wide, the
            same meaning the field has on WarningRow
        message: Operator-readable text, unrendered — no prefix, no colour, no timestamp
        event_time: The run's own time when this was logged; None while no clock is attached
    """
    level: LogLevel
    timestamp: datetime
    scope: str
    message: str
    event_time: Optional[datetime] = None
