"""
The execution counts' fold tables (#557) — one run unit's order counts per row.

Every count adds up across the units, whatever their currency: an order is an order. The set of
counts is the execution statistics' own field list, so a new count joins the totals the moment it
joins the statistics. The execution totals read them, and so does the aggregated portfolio's
execution part, which adds the share of handled orders that executed.
"""

from typing import Any, Dict, Sequence

from python.framework.types.aggregation_types import FigureDeclaration, FoldTable, Reduction
from python.framework.types.api.report_types import (
    AggregatedPortfolioRow,
    ExecutionStatsRow,
    ExecutionStatsTotals,
)
from python.framework.types.trading_env_types.trading_env_stats_types import EXECUTION_COUNT_FIELDS
from python.framework.utils.trading_math.order_math import execution_rate_pct

# One declaration per count, shared by both tables. Over no unit a count is 0.
EXECUTION_COUNT_FIGURES: Dict[str, FigureDeclaration] = {
    field_name: FigureDeclaration(Reduction.SUM, empty_value=0)
    for field_name in EXECUTION_COUNT_FIELDS}

EXECUTION_TOTALS_TABLE = FoldTable(
    name='execution_totals',
    figures={
        **EXECUTION_COUNT_FIGURES,
        'orders_failed': FigureDeclaration(Reduction.SUM, empty_value=0),
    },
    row_model=ExecutionStatsRow,
    served_as=ExecutionStatsTotals,
)


def execution_rate(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The share of the handled orders that executed.

    The orders whose endings the units count are the ones they sent and the ones they took over at
    boot — an adopted order that fills is executed without having been submitted here.

    Args:
        combined: The figures being built, the counts already folded
        rows: The units' execution rows

    Returns:
        Executed over sent plus adopted, in percent; 0.0 when none was handled
    """
    return execution_rate_pct(combined['orders_executed'], combined['orders_submitted'],
                              combined['orders_adopted'])


AGGREGATED_EXECUTION_TABLE = FoldTable(
    name='aggregated_portfolio_execution',
    figures={
        **EXECUTION_COUNT_FIGURES,
        'execution_rate_pct': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'execution_rate_pct': execution_rate},
    row_model=ExecutionStatsRow,
    served_as=AggregatedPortfolioRow,
    serves_whole=False,
)
