"""
FiniexTestingIDE - Algo Clock Violation Error (#359)

Raised when a loaded decision logic or worker reads wall-clock directly
(datetime.now() / datetime.utcnow() / time.time()) instead of the canonical
clock DecisionTradingApi.get_current_time(). A wall-clock read breaks
backtest reproducibility and decouples timing from the tick cadence that
gates async resolution.
"""


class AlgoClockViolationError(Exception):
    """A loaded algo reads wall-clock directly — forbidden in decision-logic / worker code."""
    pass
