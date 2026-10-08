"""
What the order history and the trade records state about each order and trade after a backtest
with partial closes.

Every row names the type the order was asked as, refusals included; a `pending` row carries the
moment of its submission; a close row says whether it closed all or part of the position and
how much it asked for. Every trade record carries the position's size when it opened — which on
a partially closed position is more than the slice the record closed.
"""
from typing import List

from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.order_types import (
    CloseType,
    OrderAction,
    OrderResult,
    OrderStatus,
    OrderType,
)


class TestOrderRows:
    """The order history describes each order, not only what happened to it."""

    def test_every_row_states_the_order_type(self, order_history: List[OrderResult]):
        assert order_history
        assert all(order.order_type is not None for order in order_history)

    def test_a_pending_row_carries_its_submission_time(self, order_history: List[OrderResult]):
        pending = [order for order in order_history if order.status is OrderStatus.PENDING]
        assert pending
        assert all(order.execution_time is not None for order in pending)

    def test_a_close_row_states_its_close_type_and_size(self, order_history: List[OrderResult]):
        closes = [order for order in order_history
                  if order.action is OrderAction.CLOSE and order.status is OrderStatus.EXECUTED]
        assert closes
        for order in closes:
            assert order.close_type in (CloseType.FULL, CloseType.PARTIAL)
            assert order.order_type is OrderType.MARKET
            assert order.requested_lots == order.executed_lots


class TestTradeRecords:
    """A trade record carries what belongs to the position, beside what this record closed."""

    def test_every_record_carries_the_size_at_entry(self, trade_history: List[TradeRecord]):
        assert trade_history
        for trade in trade_history:
            assert trade.entry_lots is not None
            assert trade.entry_lots >= trade.lots

    def test_a_partial_close_is_a_slice_of_the_size_at_entry(
            self, trade_history: List[TradeRecord]):
        partials = [trade for trade in trade_history if trade.close_type is CloseType.PARTIAL]
        assert partials
        assert all(trade.entry_lots > trade.lots for trade in partials)
