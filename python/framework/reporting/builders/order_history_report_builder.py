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
    run_id: str,
    units: List[RunUnit],
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """
    Build the canonical order-history report from the run's units.

    Args:
        run_id: The run this report belongs to
        units: The run's units (sim: scenarios; live: the session)
        symbol / status: Optional filters

    Returns:
        OrderHistoryReport with the filtered, mapped rows + distinct symbols
    """
    rows = [_to_row(order, unit.name) for unit in units for order in unit.order_history]
    return _assemble(run_id, rows, symbol, status)


def _assemble(
    run_id: str,
    rows: List[OrderHistoryRow], symbol: Optional[str], status: Optional[str]) -> OrderHistoryReport:
    """Apply the shared row filter and assemble the report (the one filter path)."""
    filtered = []
    for row in rows:
        if symbol is not None and row.symbol != symbol:
            continue
        if status is not None and row.status.value != status:
            continue
        filtered.append(row)
    symbols = sorted({row.symbol for row in filtered if row.symbol})
    return OrderHistoryReport(run_id=run_id, orders=filtered, count=len(filtered), symbols=symbols)


def _to_row(order: OrderResult, scenario_name: str = '') -> OrderHistoryRow:
    """
    Map one OrderResult to a renderable row.

    What the record does not have stays None and serializes as null — never '' or 0.0, which
    a reader takes for a value. A zero executed size or price is the record's way of saying
    nothing executed, and is carried as None for the same reason.

    Args:
        order: The order-lifecycle record
        scenario_name: The run unit it belongs to

    Returns:
        The row
    """
    return OrderHistoryRow(
        order_id=order.order_id,
        scenario_name=scenario_name,
        position_id=order.position_id or None,
        symbol=order.symbol or '',
        direction=order.direction,
        action=order.action,
        status=order.status,
        requested_lots=order.requested_lots or None,
        executed_lots=order.executed_lots or None,
        executed_price=order.executed_price or None,
        event_time=order.execution_time.isoformat() if order.execution_time else None,
        commission=order.commission,
        order_type=order.order_type,
        close_type=order.close_type,
        rejection_reason=order.rejection_reason,
        rejection_message=order.rejection_message or None,
        initiator=order.initiator,
        end_reason=order.end_reason,
    )
