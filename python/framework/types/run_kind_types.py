"""
FiniexTestingIDE - Run Kind Types

The two axes that tell the kinds of run apart: where the ticks came from and where the orders went.
Together with `run_type` they say which kind of run a record describes
(`docs/introduction_to_the_ide.md`). Kept apart from the API types because records below them —
the carry-over among them — state a run's kind too, and must not import the API layer to do it.
"""

from enum import StrEnum


class TicksFrom(StrEnum):
    """
    Where a run's ticks came from — one of the two axes that tell the kinds of run apart.

    ARCHIVE: replayed from the tick archive — every backtest, and a mock session
    VENUE: read from the venue as they happened — a dry run or a real-money session
    """
    ARCHIVE = 'archive'
    VENUE = 'venue'


class OrdersTo(StrEnum):
    """
    Where a run's orders went — the other axis. Together with `run_type` the two say which kind
    of run this is (`docs/introduction_to_the_ide.md`), so no reader has to derive it.

    SIMULATED: filled by a simulator — every backtest, a mock session, and a dry run
    VENUE: placed at the venue — a real-money session, and only that
    """
    SIMULATED = 'simulated'
    VENUE = 'venue'
