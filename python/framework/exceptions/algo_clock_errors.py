"""
FiniexTestingIDE - Clock Discipline Errors

AlgoClockViolationError (#359): a loaded decision logic or worker reads wall-clock
directly (datetime.now() / datetime.utcnow() / time.time()) instead of the canonical
clock DecisionTradingApi.get_current_time().

ClockNotInjectedError (#365): the canonical clock was requested before the tick loop
injected a time. The clock must NEVER fall back to wall-clock — a missing injected
time is a hard error, not a silent wall-clock substitution. Both break backtest
reproducibility and decouple timing from the tick cadence that gates async resolution.
"""

from python.framework.exceptions.finiex_error import FiniexError


class AlgoClockViolationError(FiniexError):
    """A loaded algo reads wall-clock directly — forbidden in decision-logic / worker code."""
    pass


class ClockNotInjectedError(FiniexError, RuntimeError):
    """
    The canonical clock was requested before a time was injected (tick / heartbeat).

    Inherits RuntimeError so existing `except RuntimeError` keeps catching it. Raised
    instead of ever returning a wall-clock fallback — event timestamps must always come
    from the injected canonical clock.
    """
    pass
