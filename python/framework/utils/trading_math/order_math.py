"""
FiniexTestingIDE - Order Math (#557)

Order statistics formulas — one definition each, for every model and fold that serves them: the
aggregated portfolio folds its execution rate with it, and the run summary derives its own.
"""


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
