"""
The order counts of a unit or a run as console text — one wording for every summary.

Several summaries print an orders line and each used to spell its own: `11 sent, 9 executed,
2 rejected` in one, `Rej: 2` in another. Since #362 a run counts every way an order can end,
and spelling the endings out in each summary is the copy that drifts.
"""

from typing import Union

from python.framework.types.api.report_types import (
    AggregatedPortfolioRow,
    ExecutionStatsRow,
    RunSummary,
)
from python.framework.types.trading_env_types.order_types import (
    FAILED_ORDER_STATUSES,
    OrderStatus,
)
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_STATS_FIELD_BY_STATUS,
    ExecutionStats,
)
from python.framework.utils.console_renderer import ConsoleRenderer

OrderCounts = Union[ExecutionStats, ExecutionStatsRow, RunSummary, AggregatedPortfolioRow]


def order_endings_text(counts: OrderCounts, renderer: ConsoleRenderer) -> str:
    """
    Every ending other than a fill that occurred, as one clause — a failed one in yellow.

    Args:
        counts: Anything carrying the order counts
        renderer: Colours the failed endings

    Returns:
        e.g. '1 denied · 2 cancelled', or '' when every order filled
    """
    parts = []
    for status, field_name in EXECUTION_STATS_FIELD_BY_STATUS.items():
        if field_name is None or status is OrderStatus.EXECUTED:
            continue
        count = getattr(counts, field_name)
        if not count:
            continue
        text = f'{count} {status.value}'
        parts.append(renderer.yellow(text) if status in FAILED_ORDER_STATUSES else text)
    return ' · '.join(parts)
