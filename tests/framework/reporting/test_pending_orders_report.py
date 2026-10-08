"""
Pending-Orders Report Builder Tests (#391, #362).

Derives each run unit's in-flight counters from its order-event stream: per submission, the first
event that ends its in-flight phase. Tested over hand-built event lists wrapped in RunUnits — the
order of events is the order the executor records them. Units that submitted nothing and hold
nothing are skipped (mirrors the console).
"""

from typing import List, Optional

from python.framework.reporting.builders.pending_orders_report_builder import (
    build_pending_orders_report,
    pending_orders_row,
)
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import PendingOrdersUnitRow
from python.framework.types.trading_env_types.active_orders_snapshot_types import (
    ActiveOrderSnapshot,
    ActiveOrdersSnapshot,
)
from python.framework.types.trading_env_types.order_event_types import (
    IN_FLIGHT_ENDING_BY_EVENT,
    InFlightEnding,
    OrderEvent,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderEndReason,
    OrderType,
)

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


class _Stream:
    """Builds one unit's events the way the executor numbers them."""

    def __init__(self) -> None:
        self.events: List[OrderEvent] = []

    def add(self, event_type: OrderEventType, order_id: str = 'pos_eurusd_1',
            submitted_seq: Optional[int] = None, **fields) -> int:
        """
        Append one event.

        Args:
            event_type: What happened
            order_id: The order
            submitted_seq: The submission it belongs to
            **fields: Further OrderEvent fields

        Returns:
            Its seq
        """
        seq = len(self.events) + 1
        self.events.append(OrderEvent(
            seq=seq, event_type=event_type, order_id=order_id,
            submitted_seq=seq if event_type is OrderEventType.SUBMITTED else submitted_seq,
            **fields))
        return seq

    def submit(self, order_id: str = 'pos_eurusd_1') -> int:
        """Append a submission and return its seq."""
        return self.add(OrderEventType.SUBMITTED, order_id)


def _active(order_id: str = 'L1', order_type: OrderType = OrderType.LIMIT) -> ActiveOrderSnapshot:
    return ActiveOrderSnapshot(
        order_id=order_id, order_type=order_type, symbol='EURUSD',
        direction=OrderDirection.LONG, lots=0.1, entry_price=1.1000,
        stop_loss=1.0980, take_profit=1.1040)


def _row(stream: _Stream, active: Optional[ActiveOrdersSnapshot] = None) -> PendingOrdersUnitRow:
    return pending_orders_row('s1', 'EURUSD', stream.events, active)


class TestTheDeclaration:
    """The map, the categories and the row's counters, held to each other in both directions."""

    def test_every_event_type_is_counted_or_declared_uncounted(self):
        assert set(IN_FLIGHT_ENDING_BY_EVENT) == set(OrderEventType)

    def test_every_category_ends_something(self):
        assert set(IN_FLIGHT_ENDING_BY_EVENT.values()) - {None} == set(InFlightEnding)

    def test_every_category_has_its_counter_on_the_row(self):
        fields = set(PendingOrdersUnitRow.model_fields)
        assert {f'total_{ending.value}' for ending in InFlightEnding} <= fields


class TestEachEnding:
    """One submission each way — the first word from the venue decides."""

    def test_accepted_then_filled_is_one_acceptance(self):
        stream = _Stream()
        sub = stream.submit()
        stream.add(OrderEventType.ACCEPTED, submitted_seq=sub, in_flight_ms=40.0)
        stream.add(OrderEventType.FILLED, submitted_seq=sub)

        row = _row(stream)

        assert (row.total_submitted, row.total_accepted) == (1, 1)

    def test_a_refusal(self):
        stream = _Stream()
        sub = stream.submit()
        stream.add(OrderEventType.REJECTED, submitted_seq=sub, in_flight_ms=25.0)

        assert _row(stream).total_rejected == 1

    def test_undelivered_and_unaccounted_are_never_confirmed(self):
        stream = _Stream()
        first = stream.submit('pos_eurusd_1')
        stream.add(OrderEventType.UNRESOLVED, 'pos_eurusd_1', first)
        stream.add(OrderEventType.UNDELIVERED, 'pos_eurusd_1', first)
        second = stream.submit('pos_eurusd_2')
        stream.add(OrderEventType.UNACCOUNTED, 'pos_eurusd_2', second,
                   end_reason=OrderEndReason.RESOLUTION_CEILING)

        row = _row(stream)

        assert row.total_never_confirmed == 2
        assert [(o.order_id, o.event_type, o.end_reason) for o in row.never_confirmed_orders] == [
            ('pos_eurusd_1', OrderEventType.UNDELIVERED, None),
            ('pos_eurusd_2', OrderEventType.UNACCOUNTED, OrderEndReason.RESOLUTION_CEILING)]

    def test_the_data_end_on_the_way_is_expired(self):
        stream = _Stream()
        sub = stream.submit()
        stream.add(OrderEventType.EXPIRED, submitted_seq=sub)

        row = _row(stream)

        assert row.total_expired == 1
        assert row.never_confirmed_orders == [], 'the end of the data is no anomaly'

    def test_only_the_first_ending_counts(self):
        """A resting order the venue took and that expires at the data end was accepted."""
        stream = _Stream()
        sub = stream.submit()
        stream.add(OrderEventType.ACCEPTED, submitted_seq=sub)
        stream.add(OrderEventType.EXPIRED, submitted_seq=sub)

        row = _row(stream)

        assert (row.total_accepted, row.total_expired) == (1, 0)

    def test_the_counters_add_up_to_the_submissions(self):
        stream = _Stream()
        for number, ending in enumerate((OrderEventType.ACCEPTED, OrderEventType.REJECTED,
                                         OrderEventType.UNACCOUNTED, OrderEventType.EXPIRED)):
            order_id = f'pos_eurusd_{number}'
            stream.add(ending, order_id, stream.submit(order_id))

        row = _row(stream)

        assert row.total_submitted == (row.total_accepted + row.total_rejected
                                       + row.total_never_confirmed + row.total_expired) == 4


class TestWhatIsNotASubmission:
    def test_an_adopted_order_is_not_counted(self):
        """A previous session sent it, and accepted it there."""
        stream = _Stream()
        adopted = stream.add(OrderEventType.ADOPTED)
        stream.add(OrderEventType.FILLED, submitted_seq=adopted)

        assert _row(stream) is None, 'nothing was submitted and nothing rests'

    def test_a_denial_is_not_counted(self):
        stream = _Stream()
        stream.add(OrderEventType.DENIED)
        sub = stream.submit()
        stream.add(OrderEventType.ACCEPTED, submitted_seq=sub)

        row = _row(stream)

        assert (row.total_submitted, row.total_accepted) == (1, 1)


class TestAnswerDurations:
    def test_only_answers_carry_a_duration(self):
        """A late acceptance learned by asking has none — that span is the asking."""
        stream = _Stream()
        answered = stream.submit('pos_eurusd_1')
        stream.add(OrderEventType.ACCEPTED, 'pos_eurusd_1', answered, in_flight_ms=40.0)
        refused = stream.submit('pos_eurusd_2')
        stream.add(OrderEventType.REJECTED, 'pos_eurusd_2', refused, in_flight_ms=60.0)
        asked = stream.submit('pos_eurusd_3')
        stream.add(OrderEventType.UNRESOLVED, 'pos_eurusd_3', asked)
        stream.add(OrderEventType.RESOLVED, 'pos_eurusd_3', asked)
        stream.add(OrderEventType.ACCEPTED, 'pos_eurusd_3', asked)

        row = _row(stream)

        assert row.total_accepted == 2
        assert (row.avg_in_flight_ms, row.min_in_flight_ms, row.max_in_flight_ms,
                row.in_flight_count) == (50.0, 40.0, 60.0, 2)

    def test_no_answer_timed_is_none(self):
        stream = _Stream()
        sub = stream.submit()
        stream.add(OrderEventType.UNACCOUNTED, submitted_seq=sub)

        row = _row(stream)

        assert (row.avg_in_flight_ms, row.in_flight_count) == (None, 0)


class TestBuild:
    def test_active_orders_mapped(self):
        active = ActiveOrdersSnapshot(active_limit_orders=[_active()])
        report = build_pending_orders_report(
            _RUN_ID, [RunUnit(name='s1', symbol='EURUSD', active_orders=active)])
        rows = report.units[0].active_limit_orders
        assert len(rows) == 1
        a = rows[0]
        assert a.order_id == 'L1' and a.order_type is OrderType.LIMIT
        assert a.direction is OrderDirection.LONG
        assert a.entry_price == 1.1000 and a.stop_loss == 1.0980 and a.take_profit == 1.1040

    def test_skips_units_without_activity(self):
        stream = _Stream()
        sub = stream.submit()
        stream.add(OrderEventType.ACCEPTED, submitted_seq=sub)
        report = build_pending_orders_report(_RUN_ID, [
            RunUnit(name='none', symbol='EURUSD'),
            RunUnit(name='empty', symbol='EURUSD', active_orders=ActiveOrdersSnapshot()),
            RunUnit(name='real', symbol='EURUSD', order_events=stream.events),
        ])
        assert [u.name for u in report.units] == ['real']
