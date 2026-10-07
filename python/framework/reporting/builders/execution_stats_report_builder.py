"""
Execution-stats report builder (#391) — the order-count postprocessor.

Maps each run unit's ExecutionStats (currency-agnostic order counts + SL/TP triggers)
to the canonical ExecutionStatsReport: one row per unit + the summed totals (from the
shared aggregator). Pure + fixture-testable.
"""

from typing import List

from python.framework.reporting.builders.report_aggregators import aggregate_execution_totals
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import ExecutionStatsReport, ExecutionStatsRow
from python.framework.types.trading_env_types.order_types import FAILED_ORDER_STATUSES
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_COUNT_FIELDS,
    EXECUTION_STATS_FIELD_BY_STATUS,
    ExecutionStats,
)

# The counts the declared failure category is made of — one per status it names
_FAILED_COUNT_FIELDS = tuple(sorted(
    EXECUTION_STATS_FIELD_BY_STATUS[status] for status in FAILED_ORDER_STATUSES))


def build_execution_stats_report(run_id: str, units: List[RunUnit]) -> ExecutionStatsReport:
    """
    Build the report from the run's units — one row per unit + summed totals.

    Args:
        run_id: The run this report belongs to
        units: The run's units (sim: scenarios; live: the session)

    Returns:
        ExecutionStatsReport with one row per unit (with stats) and the summed totals
    """
    rows = [
        _to_row(unit.name, unit.symbol, unit.execution_stats)
        for unit in units if unit.execution_stats is not None
    ]
    return ExecutionStatsReport(run_id=run_id, units=rows, totals=aggregate_execution_totals(rows))


def failed_order_count(stats: ExecutionStats) -> int:
    """
    The rows that ended as a failure, as `FAILED_ORDER_STATUSES` declares one.

    Taken from the counts, never from the order history: the history is capped, the counts
    are not.

    Args:
        stats: One unit's counts

    Returns:
        Denied + rejected + undelivered + unaccounted
    """
    return sum(getattr(stats, field_name) for field_name in _FAILED_COUNT_FIELDS)


def _to_row(name: str, symbol: str, stats: ExecutionStats) -> ExecutionStatsRow:
    """
    Map one unit's ExecutionStats to a renderable row — every count, by its own name.

    Args:
        name: The unit's name
        symbol: The unit's symbol
        stats: The unit's counts

    Returns:
        The row
    """
    return ExecutionStatsRow(
        name=name,
        symbol=symbol,
        orders_failed=failed_order_count(stats),
        **{field_name: getattr(stats, field_name) for field_name in EXECUTION_COUNT_FIELDS},
    )
