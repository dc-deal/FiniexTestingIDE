"""
The safety report's fold table (#557) — one trading day's risk record per row.

The deepest day is picked once, here, rather than by each renderer: a thirty-day run cannot put
thirty rows on a console, and if a renderer chooses, the console and the API answer the same
question differently. It names the DAY beside the number, because a loss means nothing without
the baseline it was measured against, which sits in that day's row.
"""

from typing import Any

from python.framework.types.aggregation_types import FigureDeclaration, FoldTable, Reduction
from python.framework.types.api.report_types import SafetyReport
from python.framework.types.autotrader_types.safety_session_types import SafetyDayRecord


def daily_limit_hit(row: Any) -> int:
    """
    Whether a day tripped its own daily limit — one count each.

    Args:
        row: A safety day record

    Returns:
        1 when it did, else 0
    """
    return 1 if row.limit_hit else 0


SAFETY_DAYS_TABLE = FoldTable(
    name='safety_days',
    figures={
        'days_limit_hit': FigureDeclaration(Reduction.SUM, source=daily_limit_hit, empty_value=0),
        # The deepest day and its own figures — the first among equals; over no day, nothing named
        'worst_day_loss_abs': FigureDeclaration(Reduction.MAX, source='worst_loss_abs',
                                                empty_value=0.0),
        'worst_day': FigureDeclaration(Reduction.COMPANION, companion_of='worst_day_loss_abs',
                                       source='day', empty_value=''),
        'worst_day_loss_pct': FigureDeclaration(Reduction.COMPANION,
                                                companion_of='worst_day_loss_abs',
                                                source='worst_loss_pct', empty_value=0.0),
    },
    row_model=SafetyDayRecord,
    served_as=SafetyReport,
    serves_whole=False,
)
