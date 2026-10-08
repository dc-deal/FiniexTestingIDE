"""
Field Study — what the order hooks tell the phase machine (#362, #566).

The hooks record nothing any more: the capture's order lines are the live core's own record,
copied in by the stream projection. What the hooks still do is the machine's input — a flag per
kind of outcome since the last submit — and two endings reach them without a direction: a close
the venue refused, and one the framework stopped asking about, once the position it closed is gone.
None of that may raise, because the study is the acceptance test of a real-money run.
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
    study._unaccounted_flag = False
    study._cancelled_flag = False
    return study


def _row(status: OrderStatus, direction=None) -> OrderResult:
    """The booked row an ending carries."""
    return OrderResult(order_id='pos_ethusd_4', status=status, action=OrderAction.CLOSE,
                       direction=direction)


class TestAnEndingWithoutADirection:
    """None is a legitimate direction for a close whose position is gone — not a crash."""

    def test_a_refused_close_is_a_rejection(self):
        study = _bare_field_study()

        study.on_order_rejected(OrderRejectedEvent(
            order_id='pos_ethusd_4', direction=None, reason=RejectionReason.BROKER_ERROR,
            message='refused', result=_row(OrderStatus.REJECTED)))

        assert study._rejected_flag
        assert not study._unaccounted_flag

    def test_an_unaccounted_close_has_its_own_word(self):
        study = _bare_field_study()

        study.on_order_unaccounted(OrderUnaccountedEvent(
            order_id='pos_ethusd_4', direction=None, end_reason=OrderEndReason.ORDER_TIMEOUT,
            result=_row(OrderStatus.UNACCOUNTED)))

        assert study._unaccounted_flag
        assert not study._rejected_flag, 'never read as the refusal a phase may be waiting for'


class TestAnEndingIsObservedNotRecorded:
    """The order lines come from the core's record; a hook writing one would write it twice."""

    def test_a_venue_expiry(self):
        study = _bare_field_study()
        expired = OrderResult(order_id='ord_7', status=OrderStatus.EXPIRED,
                              action=OrderAction.OPEN, direction=OrderDirection.LONG,
                              initiator=OrderInitiator.VENUE,
                              end_reason=OrderEndReason.VENUE_EXPIRED)

        study.on_order_cancelled(OrderCancelledEvent(
            order_id='ord_7', direction=OrderDirection.LONG, result=expired))

        assert study._cancelled_flag
        assert study._recorder.method_calls == []


class TestTheFlagsResetTogether:
    """A new submit or a new phase starts every observation afresh, the unaccounted one too."""

    def test_a_reset_clears_the_unaccounted_flag(self):
        study = _bare_field_study()
        study._filled_flag = True
        study._unaccounted_flag = True

        study._reset_flags()

        assert not study._unaccounted_flag
        assert not study._filled_flag
