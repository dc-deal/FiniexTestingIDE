"""
FiniexTestingIDE - Field Study Stream Projection Tests (#566)

The field study's order and venue lines are the live core's own record, copied in as the executor
writes it. These tests hold the copy to the record: every event type is either written or declared
silent, a line names the phase that submitted its order, a fill becomes an open, a partial close or
a full close by what its position still holds, a refusal made before sending is a line, and the
venue's reads become the snapshots and alerts the certificate reads.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from python.framework.reporting.field_study_recorder import FieldStudyRecorder
from python.framework.reporting.field_study_stream_projection import (
    FIELD_STUDY_LINE_BY_EVENT,
    FieldStudyStreamProjection,
)
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthPart,
    BrokerTruthReadReason,
    BrokerTruthRecord,
    BrokerTruthSnapshot,
)
from python.framework.types.live_types.reconciliation_types import (
    ReconcileDivergence,
    ReconcileState,
)
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderType,
    RejectionReason,
)


class _StubLogger:
    """The recorder writes a banner, and an error when the file cannot be written."""

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _capture(tmp_path) -> FieldStudyRecorder:
    return FieldStudyRecorder(str(tmp_path / 'field_study.jsonl'), 'prof', 'BTCUSD',
                              ['p1', 'p2'], _StubLogger())


def _lines(recorder: FieldStudyRecorder) -> List[Dict[str, Any]]:
    text = recorder.get_path().read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in text[1:] if line.strip()]


def _event(seq: int, event_type: OrderEventType, **fields: Any) -> OrderEvent:
    base = dict(order_id='pos_btcusd_1', position_id='pos_btcusd_1', action=OrderAction.OPEN,
                order_type=OrderType.MARKET, direction=OrderDirection.LONG)
    base.update(fields)
    return OrderEvent(seq=seq, event_type=event_type, **base)


class TestEveryEventTypeIsDeclared:
    """A new event type cannot reach the projection undeclared — it would raise on a real run."""

    def test_the_map_covers_the_enum(self):
        assert set(FIELD_STUDY_LINE_BY_EVENT) == set(OrderEventType)


# What each written event type becomes, held row by row against the map — a changed value is a
# changed line, and the keys-only test above cannot see it.
_EXPECTED_LINE = {
    OrderEventType.REJECTED: 'order_rejected',
    OrderEventType.DENIED: 'order_rejected',
    OrderEventType.CANCELLED: 'order_cancelled',
    OrderEventType.PARTIALLY_FILLED: 'order_filled',
    OrderEventType.FILLED: 'order_filled',
    OrderEventType.EXPIRED: 'order_cancelled',
    OrderEventType.UNDELIVERED: 'order_rejected',
    OrderEventType.UNACCOUNTED: 'order_unaccounted',
}


class TestEveryLineOfTheTable:
    """Each event type that writes a line writes the line the guide names, with its own status."""

    def test_the_written_types_are_the_expected_ones(self):
        written = {event for event, line in FIELD_STUDY_LINE_BY_EVENT.items() if line is not None}
        assert written == set(_EXPECTED_LINE)

    @pytest.mark.parametrize('event_type', sorted(_EXPECTED_LINE, key=lambda e: e.value))
    def test_the_line_it_becomes(self, tmp_path, event_type):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, event_type, lots=0.01, fill_price=100.0))

        line = _lines(recorder)[-1]
        assert (line['event_type'], line['status']) == (_EXPECTED_LINE[event_type],
                                                       event_type.value)


class TestTheLineNamesThePhaseThatSubmitted:
    """An answer arrives in whatever phase is current; the line belongs to the phase that sent."""

    def test_a_fill_after_its_phase_ended(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        recorder.record_phase_start('p1', 0, 'LONG')
        projection(_event(1, OrderEventType.SUBMITTED, submitted_seq=1, submission_mid=100.0))
        recorder.record_phase_start('p2', 1, None)
        projection(_event(2, OrderEventType.FILLED, submitted_seq=1, lots=0.01, fill_price=100.5))

        fill = next(line for line in _lines(recorder) if line['event_type'] == 'order_filled')
        assert (fill['phase'], fill['phase_index']) == ('p1', 0)

    def test_a_refusal_before_sending_takes_the_phase_in_progress(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        recorder.record_phase_start('p2', 1, 'LONG')
        projection(_event(1, OrderEventType.DENIED,
                          rejection_reason=RejectionReason.INVALID_LOT_SIZE,
                          message='below the minimum'))

        refused = next(line for line in _lines(recorder) if line['event_type'] == 'order_rejected')
        assert refused['phase'] == 'p2'
        assert refused['status'] == 'denied'
        assert refused['extra']['reason'] == RejectionReason.INVALID_LOT_SIZE.value


class TestAnEndingWithoutADirection:
    """A close whose position is gone names no direction — the line omits it, never crashes."""

    def test_a_refused_close(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.REJECTED, action=OrderAction.CLOSE, direction=None,
                          rejection_reason=RejectionReason.BROKER_ERROR))

        refused = next(line for line in _lines(recorder) if line['event_type'] == 'order_rejected')
        assert 'side' not in refused


class TestAnEndingCarriesItsOwnStatus:
    """The venue letting an order run out is `expired`, written as the cancel line it was."""

    def test_a_venue_expiry(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.EXPIRED, order_type=OrderType.LIMIT))

        ended = next(line for line in _lines(recorder) if line['event_type'] == 'order_cancelled')
        assert (ended['status'], ended['side']) == ('expired', 'LONG')


@pytest.mark.parametrize('spot_mode', [False, True])
class TestOpenPartialAndFullClose:
    """What a position still holds decides the line — in both account models."""

    def test_the_three_fills_of_one_position(self, tmp_path, spot_mode):
        mock = MockOrderExecution(
            mode=MockExecutionMode.INSTANT_FILL, spot_mode=spot_mode,
            initial_balances={'USD': 100000.0, 'BTC': 1.0} if spot_mode else None)
        executor = mock.create_executor()
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        executor.add_order_event_listener(projection)
        recorder.record_phase_start('p1', 0, 'LONG')
        mock.feed_tick(executor)
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.02))
        mock.feed_tick(executor)
        position = executor.get_open_positions()[0]
        executor.close_position(position.position_id, lots=0.01)
        mock.feed_tick(executor)
        executor.close_position(position.position_id)
        mock.feed_tick(executor)

        fills = [line for line in _lines(recorder)
                 if line['event_type'] in ('order_filled', 'partial_close')]
        assert [(line['event_type'], line['extra']['action']) for line in fills] == [
            ('order_filled', 'open'), ('partial_close', 'close'), ('order_filled', 'close')]
        assert fills[1]['extra']['remaining_lots'] == pytest.approx(0.01)
        assert all(line['extra'].get('fee') is not None for line in fills)


class TestTheSlippageSources:
    """A fill carries what it is measured against — the value, never the measurement."""

    def test_a_market_fill_against_its_submission_mid(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.SUBMITTED, submitted_seq=1, submission_mid=100.0))
        projection(_event(2, OrderEventType.FILLED, submitted_seq=1, lots=0.01, fill_price=100.5))

        fill = next(line for line in _lines(recorder) if line['event_type'] == 'order_filled')
        assert fill['slippage'] == {'order_type': 'market', 'side': 'buy',
                                    'measured_against': 'submission_mid',
                                    'reference_price': 100.0}

    def test_a_limit_fill_against_its_own_limit(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.SUBMITTED, submitted_seq=1, submission_mid=100.0,
                          order_type=OrderType.LIMIT, limit_price=99.0))
        projection(_event(2, OrderEventType.FILLED, submitted_seq=1, lots=0.01, fill_price=99.0,
                          order_type=OrderType.LIMIT, limit_price=99.0))

        fill = next(line for line in _lines(recorder) if line['event_type'] == 'order_filled')
        assert (fill['slippage']['measured_against'], fill['slippage']['reference_price']) == (
            'limit_price', 99.0)

    def test_a_protective_stop_against_its_trigger_on_the_sell_side(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.FILLED, submitted_seq=1, lots=0.01, fill_price=94.0,
                          action=OrderAction.CLOSE, order_type=OrderType.STOP,
                          trigger_price=95.0))

        fill = next(line for line in _lines(recorder) if line['event_type'] == 'order_filled')
        assert fill['slippage'] == {'order_type': 'stop', 'side': 'sell',
                                    'measured_against': 'trigger_price', 'reference_price': 95.0}


def _truth(seq, reason, snapshot, **fields) -> BrokerTruthRecord:
    return BrokerTruthRecord(seq=seq, read_reason=reason, snapshot=snapshot,
                             ts_init=datetime.now(timezone.utc), **fields)


class TestTheVenueReads:
    """The session's start and end reads are the certificate's snapshots; a changed
    reconciliation is an alert, divergent or clean."""

    def test_start_and_end_are_the_preflight_and_session_end_snapshots(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        sheet = BrokerTruthSnapshot(venue_orders=[], venue_balances={'USD': 100.0, 'BTC': 0.1})
        projection.write_broker_truth(_truth(1, BrokerTruthReadReason.SESSION_START, sheet))
        projection.write_broker_truth(_truth(2, BrokerTruthReadReason.SESSION_END, sheet))

        start, end = [line for line in _lines(recorder) if line['event_type'] == 'broker_snapshot']
        assert (start['phase'], end['phase']) == ('preflight', 'session_end')
        assert end['status'] == 'flat'
        assert end['extra']['order_count'] == 0
        assert end['extra']['balances'] == {'USD': 100.0, 'BTC': 0.1}

    def test_an_unread_order_book_proves_nothing(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        unread = BrokerTruthSnapshot(unread_parts=[BrokerTruthPart.VENUE_ORDERS,
                                                   BrokerTruthPart.VENUE_BALANCES])
        projection.write_broker_truth(_truth(1, BrokerTruthReadReason.SESSION_END, unread))

        end = next(line for line in _lines(recorder) if line['event_type'] == 'broker_snapshot')
        assert end['status'] == 'unread'
        assert 'order_count' not in end['extra']
        assert end['extra']['balances'] is None, 'unread, never an empty sheet'

    def test_a_divergence_and_its_end(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        recorder.record_phase_start('p1', 0, None)
        projection.write_broker_truth(_truth(
            1, BrokerTruthReadReason.RECONCILE, BrokerTruthSnapshot(),
            reconcile_state=ReconcileState.DIVERGENT, divergence=ReconcileDivergence()))
        projection.write_broker_truth(_truth(
            2, BrokerTruthReadReason.RECONCILE, BrokerTruthSnapshot(),
            reconcile_state=ReconcileState.CLEAN))

        alerts = [line for line in _lines(recorder) if line['event_type'] == 'reconcile_alert']
        assert [line['status'] for line in alerts] == ['divergent', 'clean']
        assert alerts[0]['phase'] == 'p1'
        assert alerts[0]['reconcile']['divergence'] is not None


class TestAnOrderThatEndsAfterPartOfItExecuted:
    """The executed part is a fill line; the ending that follows says how the rest ended."""

    def test_part_executed_then_cancelled(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.SUBMITTED, submitted_seq=1, order_type=OrderType.LIMIT,
                          limit_price=99.0))
        projection(_event(2, OrderEventType.PARTIALLY_FILLED, submitted_seq=1, lots=0.0005,
                          fill_price=99.0, order_type=OrderType.LIMIT, limit_price=99.0))
        projection(_event(3, OrderEventType.CANCELLED, submitted_seq=1, lots=None,
                          cum_lots=0.0005, order_type=OrderType.LIMIT,
                          initiator=OrderInitiator.STRATEGY,
                          end_reason=OrderEndReason.CANCEL_REQUESTED))

        fill, ended = _lines(recorder)
        assert (fill['event_type'], fill['status'], fill['lots']) == (
            'order_filled', 'partially_filled', 0.0005)
        assert (ended['event_type'], ended['status']) == ('order_cancelled', 'cancelled')
        assert 'lots' not in ended
        assert ended['extra']['initiator'] == OrderInitiator.STRATEGY.value


class TestTheVenueClosesInTwoSteps:
    """A protective stop the venue executes in two parts: a partial close, then the full one."""

    def test_open_then_two_closing_executions(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.FILLED, submitted_seq=1, lots=0.004,
                          fill_price=100.0))
        stop = dict(submitted_seq=2, action=OrderAction.CLOSE, order_type=OrderType.STOP,
                    trigger_price=95.0, order_id='protect_pos_btcusd_1')
        projection(_event(2, OrderEventType.PARTIALLY_FILLED, lots=0.002, fill_price=94.9, **stop))
        projection(_event(3, OrderEventType.FILLED, lots=0.002, fill_price=94.8, **stop))

        lines = _lines(recorder)
        assert [(line['event_type'], line['extra']['action']) for line in lines] == [
            ('order_filled', 'open'), ('partial_close', 'close'), ('order_filled', 'close')]
        assert lines[1]['extra']['remaining_lots'] == pytest.approx(0.002)


class TestTheUnaccountedEnding:

    def test_it_names_why_it_ended(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        projection(_event(1, OrderEventType.UNACCOUNTED, submitted_seq=1,
                          end_reason=OrderEndReason.ORDER_TIMEOUT))

        line = _lines(recorder)[-1]
        assert line['event_type'] == 'order_unaccounted'
        assert line['extra']['end_reason'] == OrderEndReason.ORDER_TIMEOUT.value


class TestTheShortSide:
    """A short is opened by a sell and closed by a buy — the side the slippage is signed by."""

    def test_open_and_close(self, tmp_path):
        recorder = _capture(tmp_path)
        projection = FieldStudyStreamProjection(recorder, 'prof')
        short = dict(direction=OrderDirection.SHORT)
        projection(_event(1, OrderEventType.FILLED, submitted_seq=1, lots=0.01,
                          fill_price=100.0, **short))
        projection(_event(2, OrderEventType.FILLED, submitted_seq=2, lots=0.01, fill_price=99.0,
                          action=OrderAction.CLOSE, **short))

        opened, closed = _lines(recorder)
        assert (opened['slippage']['side'], closed['slippage']['side']) == ('sell', 'buy')
        assert (opened['side'], closed['side']) == ('SHORT', 'SHORT')
