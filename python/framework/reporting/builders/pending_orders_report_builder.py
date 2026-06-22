"""
Pending-orders report builder (#391) — the order-pipeline postprocessor.

Maps each run unit's PendingOrderStats (resolved-lifecycle breakdown + latency + active
orders at run end) to the canonical PendingOrdersReport. Sim-populated (the live
AutoTraderResult carries no pending stats → live units contribute nothing). A unit with no
pending activity is skipped (mirrors the console). Pure + fixture-testable.
"""

from typing import List

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    ActiveOrderRow, PendingOrdersReport, PendingOrdersUnitRow)
from python.framework.types.trading_env_types.pending_order_stats_types import (
    ActiveOrderSnapshot, PendingOrderStats)


def build_pending_orders_report(units: List[RunUnit]) -> PendingOrdersReport:
    """
    Build the report from the run's units — one row per unit with pending activity.

    Args:
        units: The run's units (sim: scenarios; live: the session)

    Returns:
        PendingOrdersReport with one row per unit that resolved or holds active orders
    """
    rows: List[PendingOrdersUnitRow] = []
    for unit in units:
        stats = unit.pending_stats
        if stats is None:
            continue
        has_resolved = stats.total_resolved > 0
        has_active = stats.active_limit_orders or stats.active_stop_orders
        if not has_resolved and not has_active:
            continue
        rows.append(_to_row(unit.name, unit.symbol, stats))
    return PendingOrdersReport(units=rows)


def _to_row(name: str, symbol: str, stats: PendingOrderStats) -> PendingOrdersUnitRow:
    """Map one unit's PendingOrderStats to a renderable row."""
    has_latency = stats.min_latency_ms is not None
    return PendingOrdersUnitRow(
        name=name,
        symbol=symbol,
        total_resolved=stats.total_resolved,
        total_filled=stats.total_filled,
        total_rejected=stats.total_rejected,
        total_timed_out=stats.total_timed_out,
        total_force_closed=stats.total_force_closed,
        avg_latency_ms=stats.avg_latency_ms if has_latency else None,
        min_latency_ms=stats.min_latency_ms,
        max_latency_ms=stats.max_latency_ms,
        latency_count=stats.get_latency_count(),
        active_limit_orders=_active_rows(stats.active_limit_orders),
        active_stop_orders=_active_rows(stats.active_stop_orders),
    )


def _active_rows(snapshots: List[ActiveOrderSnapshot]) -> List[ActiveOrderRow]:
    """Map active-order snapshots to renderable rows."""
    return [
        ActiveOrderRow(
            order_id=s.order_id,
            order_type=s.order_type.value if s.order_type else '',
            direction=s.direction.value if s.direction else '',
            lots=s.lots,
            entry_price=s.entry_price,
            limit_price=s.limit_price,
            stop_loss=s.stop_loss,
            take_profit=s.take_profit,
        )
        for s in snapshots
    ]
