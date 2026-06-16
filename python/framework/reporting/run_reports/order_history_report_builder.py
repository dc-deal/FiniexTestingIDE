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
from python.framework.types.trading_env_types.order_types import OrderResult


def build_order_history_report(
    orders: List[OrderResult],
    symbol: Optional[str] = None,
    status: Optional[str] = None,
) -> OrderHistoryReport:
    """
    Build the canonical order-history report from order records.

    Args:
        orders: Order records (sim: aggregated across scenarios; live: the session)
        symbol: Keep only this symbol (None = all)
        status: Keep only this OrderStatus value, e.g. 'rejected' (None = all)

    Returns:
        OrderHistoryReport with the filtered, mapped rows + distinct symbols
    """
    rows: List[OrderHistoryRow] = []
    for order in orders:
        if symbol is not None and (order.symbol or '') != symbol:
            continue
        if status is not None and order.status.value != status:
            continue
        rows.append(_to_row(order))

    symbols = sorted({row.symbol for row in rows if row.symbol})
    return OrderHistoryReport(orders=rows, count=len(rows), symbols=symbols)


def _to_row(order: OrderResult) -> OrderHistoryRow:
    """Map one OrderResult to a renderable row (None-safe for optional fields)."""
    return OrderHistoryRow(
        order_id=order.order_id,
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
