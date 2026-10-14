"""
FiniexTestingIDE - Operator Alert Types

What an AutoTrader session tells its operator out of band (#235): how urgent a moment is, which
kind of moment it is, and the message. "Alert" alone already names two other things in this
project — a display severity (`AwarenessLevel.ALERT`) and a reconciliation mode (`alert_only`) —
so this one is always the OPERATOR alert.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OperatorAlertLevel(Enum):
    """How urgent an operator alert is — it sets the message's mark, nothing else."""
    INFO = 'info'
    WARNING = 'warning'
    CRITICAL = 'critical'


class OperatorAlertKind(Enum):
    """
    The moments a session reports out of band.

    Symptoms, not causes: each one either needs the operator or ends a state that did. A single
    reconnect is not on the list — when it matters, the market data goes stale, and that is.
    """
    SESSION_STARTED = 'session_started'
    STARTUP_FAILED = 'startup_failed'
    STARTUP_REFUSED = 'startup_refused'
    REFUSED_AT_FIRST_TICK = 'refused_at_first_tick'
    RUNTIME_ERROR = 'runtime_error'
    EMERGENCY_STOP = 'emergency_stop'
    SAFETY_BLOCKED = 'safety_blocked'
    SAFETY_CLEARED = 'safety_cleared'
    MARKET_DATA_STALE = 'market_data_stale'
    MARKET_DATA_RECOVERED = 'market_data_recovered'
    SIGNAL_STALE = 'signal_stale'
    SIGNAL_RECOVERED = 'signal_recovered'
    TICK_LOOP_STALLED = 'tick_loop_stalled'
    TICK_LOOP_RESUMED = 'tick_loop_resumed'
    LIVENESS_PING_REFUSED = 'liveness_ping_refused'
    DAY_CLOSED = 'day_closed'
    SESSION_ENDED = 'session_ended'
    SHUTDOWN_CRASHED = 'shutdown_crashed'
    CHECK_MESSAGE = 'check_message'


@dataclass(frozen=True)
class OperatorAlert:
    """
    One message to the operator.

    Args:
        kind: Which moment it reports
        level: How urgent it is
        title: The one line a notification preview shows
        detail: Further lines, empty when the title says it all
        raised_at: When the session raised it, UTC — a provenance stamp: the message may
            arrive later when the channel had to wait, and this says when it happened
    """
    kind: OperatorAlertKind
    level: OperatorAlertLevel
    title: str
    detail: str
    raised_at: datetime
