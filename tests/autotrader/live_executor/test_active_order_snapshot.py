"""
FiniexTestingIDE - Active Order Snapshot (#586)

The ORDERS panel's partial-fill row reads what an order asked for and what has executed of
it. Both fields were declared on the snapshot and set by nothing, so the row could never
appear.
"""

from datetime import datetime, timezone

from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.latency_simulator_types import (
    PendingOrder,
    PendingOrderAction,
)
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderSide,
    OrderType,
)


def _execution(volume: float) -> BrokerTrade:
    """
    One venue execution of the order.

    Args:
        volume: Executed lots

    Returns:
        The execution
    """
    return BrokerTrade(
        trade_id='T-1', parent_broker_ref='OREF-1', order_id='pos_btcusd_11', volume=volume,
        price=49800.0, fee=0.02, fee_currency='USD',
        timestamp=datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc), side=OrderSide.BUY,
        is_maker=True)


def test_an_order_partly_filled_says_how_far_it_got():
    order = PendingOrder(
        pending_order_id='pos_btcusd_11', order_action=PendingOrderAction.OPEN,
        order_type=OrderType.LIMIT, symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.10,
        entry_price=49800.0)
    order.fills.append_trade(_execution(0.04))

    row = AbstractTradeExecutor._order_snapshot(order)

    assert (row.requested_lots, row.cumulative_filled_lots) == (0.10, 0.04)


def test_an_order_nothing_has_executed_of():
    order = PendingOrder(
        pending_order_id='pos_btcusd_12', order_action=PendingOrderAction.OPEN,
        order_type=OrderType.LIMIT, symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.10,
        entry_price=49800.0)

    row = AbstractTradeExecutor._order_snapshot(order)

    assert (row.requested_lots, row.cumulative_filled_lots) == (0.10, 0.0)
