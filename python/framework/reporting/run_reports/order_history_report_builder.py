"""
Order-history report builder (#391) — postprocessor twin of the trade-history
builder, for the order-lifecycle list (resting / filled / rejected orders).

Pure function: a list of OrderResults (the input both pipelines already produce via
`get_order_history()`) → the canonical `OrderHistoryReport`. Runs off the hot loop,
source-agnostic, fixture-testable. Optional filters (symbol / status) live here so
console, CSV, and API share one filter path.
"""

from typing import List, Optional

from python.framework.types.api.report_types import OrderHistoryReport, OrderHistoryRow
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.trading_env_types.order_types import OrderResult


def build_order_history_report(
    orders: List[OrderResult],
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """
    Build the canonical order-history report from a flat order list (rows tagged '').

    Args:
        orders: Order records
        symbol / status: Optional filters

    Returns:
        OrderHistoryReport with the filtered, mapped rows + distinct symbols
    """
    return _assemble([_to_row(o) for o in orders], symbol, status)


def build_order_history_report_from_batch(
    batch: BatchExecutionSummary,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """Build from a sim batch — each row tagged with its scenario name."""
    rows: List[OrderHistoryRow] = []
    for result in batch.process_result_list:
        tick_loop = getattr(result, 'tick_loop_results', None)
        if not tick_loop or not tick_loop.order_history:
            continue
        for order in tick_loop.order_history:
            rows.append(_to_row(order, result.scenario_name))
    return _assemble(rows, symbol, status)


def build_order_history_report_from_session(
    session: AutoTraderResult,
    name: str,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """Build from a live session — all rows tagged with the session unit name."""
    rows = [_to_row(o, name) for o in (session.order_history or [])]
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
