"""
A held session opens nothing new and keeps managing what it has (#499).

The gate sits on the order path, where the unaccounted-order latch (#487) already sits: only an order
that would OPEN is refused. Closing the position, moving its stop and target and cancelling its orders
stay free — what reduces risk must never be what a hold blocks. Both account models, because a close
moves money differently in each.
"""

from datetime import datetime, timezone

import pytest

from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.trading_env.order_guard import OrderGuard
from python.framework.types.trading_env_types.entry_hold_types import (
    EntryHold,
    EntryHoldLift,
    EntryHoldReason,
)
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderSide,
    OrderStatus,
    OrderType,
    RejectionReason,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True])
_AT = datetime(2026, 11, 24, 1, 5, tzinfo=timezone.utc)
_DETAIL = ('2 orders of another owner rest on BTCUSD (OQ5A1, OR7C2) while the account is declared '
           'exclusive — cancel them at the venue, or start attended and lift the hold')


def _held_session(spot_mode: bool):
    """A live session with one open long and a boot-premise hold placed after it."""
    mock = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL, spot_mode=spot_mode)
    executor = mock.create_executor()
    mock.feed_tick(executor)
    api = DecisionTradingApi(executor, required_order_types=[OrderType.MARKET])
    opened = api.send_order('BTCUSD', OrderType.MARKET, OrderSide.BUY, 0.01)
    assert opened.status in (OrderStatus.PENDING, OrderStatus.EXECUTED)
    mock.feed_tick(executor)
    assert api.get_open_positions(), 'the long the session holds before the hold'
    executor.get_entry_holds().hold(EntryHoldReason.BOOT_PREMISE, _AT, _DETAIL)
    return mock, executor, api


class TestTheGuardRefusesEntriesWhileHeld:

    def test_a_standing_hold_refuses_an_entry_and_says_why(self):
        request = OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                                   direction=OrderDirection.LONG, lots=0.01)
        hold = EntryHold(reason=EntryHoldReason.BOOT_PREMISE, held_at=_AT, detail=_DETAIL)
        refusal = OrderGuard().validate(request, _AT, entry_holds=(hold,))
        assert refusal.status is OrderStatus.DENIED
        assert refusal.rejection_reason is RejectionReason.ENTRIES_HELD
        assert _DETAIL in refusal.rejection_message

    def test_a_resting_entry_is_refused_like_a_market_one(self):
        request = OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.LIMIT,
                                   direction=OrderDirection.LONG, lots=0.01, price=40000.0)
        hold = EntryHold(reason=EntryHoldReason.BOOT_PREMISE, held_at=_AT, detail=_DETAIL)
        refusal = OrderGuard().validate(request, _AT, entry_holds=(hold,))
        assert refusal.rejection_reason is RejectionReason.ENTRIES_HELD

    def test_no_hold_refuses_nothing(self):
        request = OpenOrderRequest(symbol='BTCUSD', order_type=OrderType.MARKET,
                                   direction=OrderDirection.LONG, lots=0.01)
        assert OrderGuard().validate(request, _AT) is None


class TestAHeldSessionManagesWhatItHas:

    @ACCOUNT_MODELS
    def test_a_new_entry_is_denied_and_never_submitted(self, spot_mode):
        mock, executor, api = _held_session(spot_mode)
        submitted = executor.get_execution_stats().orders_submitted

        refused = api.send_order('BTCUSD', OrderType.MARKET, OrderSide.BUY, 0.01)

        assert refused.status is OrderStatus.DENIED
        assert refused.rejection_reason is RejectionReason.ENTRIES_HELD
        assert executor.get_execution_stats().orders_submitted == submitted
        assert executor.get_order_history()[-1].rejection_reason is RejectionReason.ENTRIES_HELD

    @ACCOUNT_MODELS
    def test_the_open_position_is_still_closed(self, spot_mode):
        # This venue holds no stop for a position, as at spot, so a logic protects it with its own
        # exits — and they leave through the close below. Moving a venue-held level never reaches
        # the guard at all: the guard is consulted for an order that opens, and nothing else.
        mock, executor, api = _held_session(spot_mode)
        position = api.get_open_positions()[0]

        closed = api.close_position(position.position_id)
        assert closed.rejection_reason is not RejectionReason.ENTRIES_HELD
        mock.feed_tick(executor)
        assert not api.get_open_positions(), 'the close went through while entries were held'

    @ACCOUNT_MODELS
    def test_entries_are_free_again_once_the_hold_is_lifted(self, spot_mode):
        mock, executor, api = _held_session(spot_mode)
        executor.get_entry_holds().lift(EntryHoldReason.BOOT_PREMISE, EntryHoldLift.OBSERVED, _AT)

        reopened = api.send_order('BTCUSD', OrderType.MARKET, OrderSide.BUY, 0.01)

        assert reopened.status in (OrderStatus.PENDING, OrderStatus.EXECUTED)
