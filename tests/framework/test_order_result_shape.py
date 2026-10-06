"""
OrderResult shape invariant (#343) — first-class field promotion.

Verifies the promoted order dimensions (symbol, direction, requested_lots,
close_type) exist as typed Optional fields with None defaults, serialize
correctly in to_dict(), and that the rejection helper states them.
The per-construction-site presence (every PENDING/EXECUTED result carries
symbol/direction/action) is exercised end-to-end by the simulation suites
(baseline, partial_close) via the executors' order_history.
"""

from datetime import datetime, timezone

import pytest

from python.framework.types.trading_env_types.order_types import (
    CloseType,
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
    RejectionReason,
    create_rejection_result,
)


class TestOrderResultShape:
    """Promoted first-class fields: defaults, typing, serialization."""

    def test_promoted_fields_default_none(self):
        result = OrderResult(order_id='o1', status=OrderStatus.PENDING)
        assert result.symbol is None
        assert result.direction is None
        assert result.requested_lots is None
        assert result.close_type is None

    def test_promoted_fields_typed_assignment(self):
        result = OrderResult(
            order_id='o1',
            status=OrderStatus.EXECUTED,
            action=OrderAction.CLOSE,
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            requested_lots=0.5,
            close_type=CloseType.PARTIAL,
        )
        assert result.symbol == 'BTCUSD'
        assert result.direction is OrderDirection.LONG
        assert result.requested_lots == 0.5
        assert result.close_type is CloseType.PARTIAL

    def test_to_dict_serializes_promoted_fields(self):
        result = OrderResult(
            order_id='o1',
            status=OrderStatus.EXECUTED,
            action=OrderAction.OPEN,
            symbol='ETHUSD',
            direction=OrderDirection.SHORT,
            requested_lots=1.25,
        )
        d = result.to_dict()
        assert d['action'] == 'open'
        assert d['symbol'] == 'ETHUSD'
        assert d['direction'] == 'short'
        assert d['requested_lots'] == 1.25
        assert d['close_type'] is None

    def test_to_dict_serializes_close_type(self):
        result = OrderResult(
            order_id='o1',
            status=OrderStatus.EXECUTED,
            action=OrderAction.CLOSE,
            close_type=CloseType.FULL,
        )
        assert result.to_dict()['close_type'] == 'full'

    def test_a_rejection_states_what_was_refused_and_when(self):
        # It used to leave the dimensions None, on the reading that they "do not apply" —
        # and every symbol filter then dropped the rejected rows. A refused order has a side,
        # a symbol and a size like any other, and the event stream orders it by its time.
        refused_at = datetime(2026, 1, 24, 14, 20, 50, tzinfo=timezone.utc)
        result = create_rejection_result(
            order_id='o1',
            reason=RejectionReason.INVALID_LOT_SIZE,
            message='lot too small',
            execution_time=refused_at,
            action=OrderAction.OPEN,
            symbol='ETHUSD',
            direction=OrderDirection.LONG,
            requested_lots=0.00001,
        )
        assert result.status is OrderStatus.REJECTED
        assert result.execution_time == refused_at
        assert result.action is OrderAction.OPEN
        assert result.symbol == 'ETHUSD'
        assert result.direction is OrderDirection.LONG
        assert result.requested_lots == 0.00001
        assert result.close_type is None

    def test_a_rejection_cannot_be_built_without_saying_its_side_and_time(self):
        # Keyword-only with no default: forgetting one is a TypeError at the call site,
        # never a silently empty field in the order history.
        with pytest.raises(TypeError):
            create_rejection_result(
                order_id='o1', reason=RejectionReason.INVALID_LOT_SIZE, message='x')
