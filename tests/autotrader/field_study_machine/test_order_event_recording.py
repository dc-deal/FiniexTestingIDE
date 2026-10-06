"""
Field Study — what the order hooks write into the record (#362).

The study is the acceptance test of the live core, so a hook that raises on an ordinary
order ending ends a real-money run in an emergency shutdown. Two endings reach the hooks
without a direction: a close the venue refused, and one the framework stopped asking about,
once the position it closed is gone. And a cancel event may carry a venue EXPIRY, which the
record has to name as such.
"""

from unittest.mock import MagicMock

from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy
from python.framework.types.decision_event_types import (
    OrderCancelledEvent,
    OrderRejectedEvent,
    OrderUnaccountedEvent,
)
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderResult,
    OrderStatus,
    RejectionReason,
)


def _bare_field_study() -> LiveFieldStudy:
    """LiveFieldStudy instance with only the attributes its order hooks touch."""
    study = object.__new__(LiveFieldStudy)        # bypass full __init__
    study._recorder = MagicMock()
    study._rejected_flag = False
    study._cancelled_flag = False
    return study


def _row(status: OrderStatus, direction=None) -> OrderResult:
    """The booked row an ending carries."""
    return OrderResult(order_id='pos_ethusd_4', status=status, action=OrderAction.CLOSE,
                       direction=direction)


class TestAnEndingWithoutADirectionIsRecorded:
    """None is a legitimate direction for a close whose position is gone — not a crash."""

    def test_a_refused_close(self):
        study = _bare_field_study()

        study.on_order_rejected(OrderRejectedEvent(
            order_id='pos_ethusd_4', direction=None, reason=RejectionReason.BROKER_ERROR,
            message='refused', result=_row(OrderStatus.REJECTED)))

        assert study._rejected_flag
        assert study._recorder.record_order_event.call_args.kwargs['side'] is None

    def test_an_unaccounted_close(self):
        study = _bare_field_study()

        study.on_order_unaccounted(OrderUnaccountedEvent(
            order_id='pos_ethusd_4', direction=None, end_reason=OrderEndReason.ORDER_TIMEOUT,
            result=_row(OrderStatus.UNACCOUNTED)))

        assert study._rejected_flag, 'the phase still fails on it'
        assert study._recorder.record_order_event.call_args.kwargs['side'] is None


class TestACancelEventRecordsItsOwnStatus:
    """The venue letting an order run out is `expired`, not `cancelled`."""

    def test_a_venue_expiry(self):
        study = _bare_field_study()
        expired = OrderResult(order_id='ord_7', status=OrderStatus.EXPIRED,
                              action=OrderAction.OPEN, direction=OrderDirection.LONG,
                              initiator=OrderInitiator.VENUE,
                              end_reason=OrderEndReason.VENUE_EXPIRED)

        study.on_order_cancelled(OrderCancelledEvent(
            order_id='ord_7', direction=OrderDirection.LONG, result=expired))

        recorded = study._recorder.record_order_event.call_args.kwargs
        assert (recorded['status'], recorded['side']) == ('expired', 'LONG')
