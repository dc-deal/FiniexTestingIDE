"""
FiniexTestingIDE - Warmup History Errors (#588)
What can go wrong between a warmup source and the renderer's history.
"""

from python.framework.exceptions.finiex_error import FiniexError


class WarmupHistoryDefectError(FiniexError, RuntimeError):
    """
    A warmup history that breaks the rule every path selects by.

    Raised for a bar that is not complete at its reference instant, for opening instants that
    do not strictly increase, and for a history whose newest bar overlaps the first bar the
    session renders itself. Each of them is a defect in the code that selected the bars, never
    a data condition: no source can deliver such a history to a correct selection. A backtest
    lets it crash its scenario and a session refuses to start — trading on it is a run that
    reads its own future.
    """
    pass


class WarmupHistoryRefusedError(FiniexError, RuntimeError):
    """
    A session cannot start from the warmup history it could obtain.

    Raised for a timeframe with no bars at all, for a capacity below what the workers require,
    and for a seam the venue could not close at the first tick. Distinct from the defect above:
    the code is right, the history is not there.
    """
    pass


class WarmupRequirementMismatchError(FiniexError, ValueError):
    """
    A worker answers "how many bars do I need" differently depending on who asks.

    The batch sizes a backtest's bar load with the classmethod calculate_requirements before any
    worker exists; an AutoTrader session loads by the instance's get_warmup_requirements. A
    worker whose two answers differ warms up on one history in a backtest and on another in a
    session of the same strategy, so it is refused when it is created.
    """
    pass
