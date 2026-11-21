"""
What an Algo Receives of an Order's Outcome — a read-only result, and frozen values (#654).

The engine keeps every booked order result in its order history and hands the same result to every
listener of the outcome, so an algo receives a mask in its place: from send_order and close_position,
and as the result of every decision event. The order capabilities and the market data status are
values the engine replaces rather than changes, so they reach the algo frozen.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.types.decision_event_types import OrderFilledEvent
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import (
    OrderCapabilities,
    OrderDirection,
    OrderFeature,
    OrderResult,
    OrderStatus,
    RejectionReason,
)

_AT = datetime(2026, 11, 25, 9, tzinfo=timezone.utc)


def _result(**values) -> OrderResult:
    executed = {'order_id': 'pos_ethusd_1', 'status': OrderStatus.EXECUTED, 'executed_price': 3000.0,
                'executed_lots': 0.05, 'execution_time': _AT, 'commission': 1.2,
                'direction': OrderDirection.LONG}
    return OrderResult(**{**executed, **values})


class TestTheResultMask:

    def test_it_shows_the_outcome(self):
        mask = AlgoOrderResult(_result())
        assert (mask.order_id, mask.status, mask.executed_price, mask.executed_lots) == (
            'pos_ethusd_1', OrderStatus.EXECUTED, 3000.0, 0.05)
        assert mask.is_success and not mask.is_refused

    def test_a_refusal_reads_as_one(self):
        mask = AlgoOrderResult(_result(status=OrderStatus.REJECTED,
                                       rejection_reason=RejectionReason.INSUFFICIENT_MARGIN,
                                       rejection_message='not enough margin'))
        assert mask.is_refused and mask.rejection_reason is RejectionReason.INSUFFICIENT_MARGIN
        assert mask.rejection_message == 'not enough margin'

    def test_a_close_that_joined_another_says_so(self):
        result = _result(status=OrderStatus.PENDING)
        result.metadata['joined_in_flight_close'] = True
        assert AlgoOrderResult(result).joined_in_flight_close
        assert not AlgoOrderResult(_result()).joined_in_flight_close

    def test_the_engines_bookkeeping_is_not_shown(self):
        mask = AlgoOrderResult(_result())
        for name in ('metadata', 'submission', 'order_event_seq'):
            assert name not in AlgoOrderResult.get_mask_fields()
            with pytest.raises(AttributeError):
                getattr(mask, name)

    def test_assigning_changes_nothing(self):
        result = _result()
        mask = AlgoOrderResult(result)
        with pytest.raises(AttributeError, match='read-only'):
            mask.executed_price = 1.0
        assert result.executed_price == 3000.0

    def test_an_event_carries_the_mask(self):
        mask = AlgoOrderResult(_result())
        event = OrderFilledEvent(order_id='pos_ethusd_1', position_id='pos_ethusd_1',
                                 direction=OrderDirection.LONG, fill_price=3000.0, lots=0.05,
                                 result=mask, tick_time=_AT)
        assert event.result.order_id == 'pos_ethusd_1'


class TestFrozenValues:

    def test_the_order_capabilities_cannot_be_changed(self):
        capabilities = OrderCapabilities(offered_order_features=frozenset({OrderFeature.STOP_LOSS}))
        with pytest.raises(dataclasses.FrozenInstanceError):
            capabilities.stop_orders = True

    def test_the_market_data_status_cannot_be_changed(self):
        status = MarketDataStatus(is_stale=True, stale_since=_AT, seconds_since_last_tick=90.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            status.is_stale = False
