"""
Execution-stats report builder (#391) — the order-count postprocessor.

Maps each run unit's ExecutionStats (currency-agnostic order counts + SL/TP triggers)
to the canonical ExecutionStatsReport: one row per unit + the summed totals (from the
shared aggregator). Pure + fixture-testable.

Beside the counts, the slippage of the fills (#631), per unit and reference kind and over every
unit's fills — derived from the uncapped order-event stream, not from the capped order history.
Its mean and maximum are reduced here by hand, not by the ledger fold, because fills are not
ledger rows; the run's figure is re-derived over all fills, never combined from unit figures.
"""

from collections import defaultdict
from typing import Dict, List

from python.framework.reporting.builders.fill_slippage import derive_fill_slippage
from python.framework.reporting.builders.report_aggregators import aggregate_execution_totals
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    ExecutionStatsRow,
    FillSlippageRow,
    FillSlippageSummary,
)
from python.framework.types.fill_slippage_types import FillSlippage, SlippageReference
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
    fills_by_unit = [(unit.name, list(derive_fill_slippage(
        unit.order_events, unit.protective_fill_levels).values())) for unit in units]
    return ExecutionStatsReport(
        run_id=run_id, units=rows, totals=aggregate_execution_totals(rows),
        fill_slippage=[FillSlippageRow(name=name, **summary.model_dump())
                       for name, fills in fills_by_unit
                       for summary in _slippage_summaries(fills)],
        fill_slippage_totals=_slippage_summaries(
            [fill for _, fills in fills_by_unit for fill in fills]))


def _slippage_summaries(fills: List[FillSlippage]) -> List[FillSlippageSummary]:
    """
    The slippage of a set of fills, one summary per reference kind present.

    Args:
        fills: The fills, of any kind

    Returns:
        One summary per kind, in the kinds' declared order
    """
    by_kind: Dict[SlippageReference, List[FillSlippage]] = defaultdict(list)
    for fill in fills:
        by_kind[fill.measured_against].append(fill)
    return [_slippage_summary(kind, by_kind[kind]) for kind in SlippageReference
            if kind in by_kind]


def _slippage_summary(kind: SlippageReference, fills: List[FillSlippage]) -> FillSlippageSummary:
    """
    The slippage of the fills of one reference kind — per fill, unweighted.

    Args:
        kind: What the fills are measured against
        fills: The fills of that kind

    Returns:
        Their count, how many were measured, and the mean and maximum in percent
    """
    measured = [fill.slippage_pct for fill in fills if fill.slippage_pct is not None]
    return FillSlippageSummary(
        measured_against=kind, fills=len(fills), fills_measured=len(measured),
        adverse_pct_mean=sum(measured) / len(measured) if measured else None,
        adverse_pct_max=max(measured) if measured else None)


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
