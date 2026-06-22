"""
Order-history report builder (#391) — postprocessor twin of the trade-history
builder, for the order-lifecycle list (resting / filled / rejected orders).

Consumes the run's `RunUnit` list (#391 Phase 2): each order row is tagged with its
run unit name. Pure, off the hot loop, fixture-testable. Optional filters (symbol /
status) live here so console, CSV, and API share one filter path.
"""

from typing import List, Optional

from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import OrderHistoryReport, OrderHistoryRow
from python.framework.types.trading_env_types.order_types import OrderResult


def build_order_history_report(
    units: List[RunUnit],
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """
    Build the canonical order-history report from the run's units.

    Args:
        units: The run's units (sim: scenarios; live: the session)
        symbol / status: Optional filters

    Returns:
        OrderHistoryReport with the filtered, mapped rows + distinct symbols
    """
    rows = [_to_row(order, unit.name) for unit in units for order in unit.order_history]
    return _assemble(rows, symbol, status)


def _assemble(
    rows: List[OrderHistoryRow], symbol: Optional[str], status: Optional[str]) -> OrderHistoryReport:
    """Apply the shared row filter and assemble the report (the one filter path)."""
    filtered = []
    for row in rows:
        if symbol is not None and row.symbol != symbol:
            continue
        if status is not None and row.status != status:
            continue
        filtered.append(row)
    symbols = sorted({row.symbol for row in filtered if row.symbol})
    return OrderHistoryReport(orders=filtered, count=len(filtered), symbols=symbols)


def _to_row(order: OrderResult, scenario_name: str = '') -> OrderHistoryRow:
    """Map one OrderResult to a renderable row (None-safe for optional fields)."""
    return OrderHistoryRow(
        order_id=order.order_id,
        scenario_name=scenario_name,
        position_id=order.position_id or '',
        symbol=order.symbol or '',
        direction=order.direction.value if order.direction else '',
        action=order.action.value if order.action else '',
        status=order.status.value,
        requested_lots=order.requested_lots or 0.0,
        executed_lots=order.executed_lots or 0.0,
        executed_price=order.executed_price or 0.0,
        execution_time=order.execution_time.isoformat() if order.execution_time else '',
        commission=order.commission,
        swap=order.swap,
        slippage_points=order.slippage_points,
        rejection_reason=order.rejection_reason.value if order.rejection_reason else '',
        rejection_message=order.rejection_message,
    )
