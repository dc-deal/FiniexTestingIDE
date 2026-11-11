"""
FiniexTestingIDE - Order Math (#557)

Order formulas — one definition each, for every model, fold and message that uses them: the
aggregated portfolio folds its execution rate with it, the run summary derives its own, and a
refused order says how much of it the free funds covered.
"""

from typing import Optional


def execution_rate_pct(executed: int, submitted: int, adopted: int) -> float:
    """
    The share of the handled orders that executed.

    The orders whose endings are counted are the ones sent and the ones taken over at boot — an
    adopted order that fills is executed without having been submitted here.

    Args:
        executed: The orders that executed
        submitted: The orders sent
        adopted: The orders taken over at boot

    Returns:
        Executed over sent plus adopted, in percent; 0.0 when none was handled
    """
    handled = submitted + adopted
    return (executed / handled * 100.0) if handled > 0 else 0.0


def covered_share_pct(available: float, required: float) -> Optional[float]:
    """
    The share of what an order needs that the account could have spent on it.

    A shortfall of a hair and one of nearly everything read alike as two figures; the share tells
    them apart. Available below zero — claims of unfilled orders above the balance — covers
    nothing.

    Args:
        available: What the account may spend
        required: What the order needs

    Returns:
        Available over required, in percent, never below 0.0; None when the order needs nothing
    """
    if required <= 0:
        return None
    return max(available, 0.0) / required * 100.0
