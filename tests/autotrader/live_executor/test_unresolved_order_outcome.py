"""
A transport fault is not a venue rejection (#473).

Before this, every exception on the order path became `BrokerOrderStatus.REJECTED` with
the transport error as the rejection reason. The executor then removed the order from its
books, recorded `BROKER_ERROR` and told the algo the venue had refused it.

If the request actually reached the venue and only the answer was lost, that order is
resting at the broker and we have forgotten it — an orphan, which reconciliation later
reports as a divergence. We manufactured the divergence #349 exists to resolve, out of our
own error handling.

The answer is not to re-send. A write is never retried (a retry after a lost answer is how
one intent becomes two positions); the order stays ours, marked in flight, and the QUERY
path resolves it — which is what FIX has done with an Order Status Request since 1992.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    TimeoutConfig,
)
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)


@pytest.fixture
def processor(logger) -> LiveRequestProcessor:
    """Processor with the standard broker-REST ladder."""
    return LiveRequestProcessor(logger=logger, timeout_config=TimeoutConfig())


class TestFailureClassification:
    """A failed broker call becomes the response that says what actually happened."""

    def test_transport_fault_is_unresolved(self, processor):
        response = processor._failure_response(
            ConnectionAttemptFailedError('HTTP 502 from /0/private/AddOrder'),
            broker_ref='',
            timestamp=datetime.now(timezone.utc),
            operation='submit',
        )
        assert response.status is BrokerOrderStatus.UNRESOLVED
        assert response.is_unresolved is True

    def test_venue_answer_stays_a_rejection(self, processor):
        # Kraken reports an order-level refusal as a plain ConnectionError carrying its own
        # message. That IS the venue speaking — retrying "Insufficient funds" forever would
        # report their outage for our order.
        response = processor._failure_response(
            ConnectionError('Kraken API error: [EOrder:Insufficient funds]'),
            broker_ref='',
            timestamp=datetime.now(timezone.utc),
            operation='submit',
        )
        assert response.status is BrokerOrderStatus.REJECTED

    def test_refused_credential_is_a_rejection_not_a_blip(self, processor):
        response = processor._failure_response(
            ConnectionAttemptFailedError('HTTP 401', terminal=True),
            broker_ref='X1',
            timestamp=datetime.now(timezone.utc),
            operation='submit',
        )
        assert response.status is BrokerOrderStatus.REJECTED

    def test_unresolved_is_not_terminal(self, processor):
        # The absence of an answer is precisely the state in which something still has to
        # happen — asking again.
        response = processor._failure_response(
            ConnectionAttemptFailedError('timeout'),
            broker_ref='',
            timestamp=datetime.now(timezone.utc),
            operation='submit',
        )
        assert response.is_terminal is False


class TestPendingSurvives:
    """The order stays in our books, because the venue may hold it."""

    def _register(self, processor) -> str:
        return processor.register_pending_open(
            order_id='ORD-U1',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref=None,
        )

    def test_unresolved_keeps_the_pending_order(self, processor, logger):
        from python.framework.types.live_types.live_request_types import SubmitResponse
        from python.framework.types.trading_env_types.latency_simulator_types import (
            PendingOrderAction,
        )
        from python.framework.types.trading_env_types.order_types import OrderType

        order_id = self._register(processor)
        response = processor._failure_response(
            ConnectionAttemptFailedError('HTTP 502'),
            broker_ref='',
            timestamp=datetime.now(timezone.utc),
            operation='submit',
        )

        processor._handle_submit_response(SubmitResponse(
            order_id=order_id,
            action=PendingOrderAction.OPEN,
            order_type=OrderType.MARKET,
            broker_response=response,
        ))

        assert processor.has_pending_orders(), 'a forgotten order is an orphan at the venue'
        pending = processor.get_pending_orders()[0]
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT

    def test_unresolved_does_not_notify_the_algo(self, processor):
        from python.framework.types.live_types.live_request_types import SubmitResponse
        from python.framework.types.trading_env_types.latency_simulator_types import (
            PendingOrderAction,
        )
        from python.framework.types.trading_env_types.order_types import OrderType

        notified = []
        processor.set_executor_hooks(
            fill_open=lambda p, price: None,
            fill_close=lambda p, price: None,
            on_rejection=lambda d, r: notified.append(r),
        )

        order_id = self._register(processor)
        processor._handle_submit_response(SubmitResponse(
            order_id=order_id,
            action=PendingOrderAction.OPEN,
            order_type=OrderType.MARKET,
            broker_response=processor._failure_response(
                ConnectionAttemptFailedError('HTTP 502'),
                broker_ref='',
                timestamp=datetime.now(timezone.utc), operation='submit'),
        ))

        assert notified == [], 'the venue never spoke; the algo must not be told it did'

    def test_unresolved_does_not_overwrite_the_broker_ref(self, processor):
        # The failure carries an empty ref. Writing it over a ref we already had would
        # destroy the only handle a later query could use.
        from python.framework.types.live_types.live_request_types import SubmitResponse
        from python.framework.types.trading_env_types.latency_simulator_types import (
            PendingOrderAction,
        )
        from python.framework.types.trading_env_types.order_types import OrderType

        order_id = processor.register_pending_open(
            order_id='ORD-U2',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.001,
            broker_ref='TX-KNOWN',
        )
        processor._handle_submit_response(SubmitResponse(
            order_id=order_id,
            action=PendingOrderAction.OPEN,
            order_type=OrderType.MARKET,
            broker_response=processor._failure_response(
                ConnectionAttemptFailedError('HTTP 502'),
                broker_ref='',
                timestamp=datetime.now(timezone.utc), operation='submit'),
        ))

        assert processor.get_pending_orders()[0].broker_ref == 'TX-KNOWN'

    def test_rejection_still_removes_the_pending_order(self, processor):
        # The other half of the contract: a real refusal must still clear our books.
        from python.framework.types.live_types.live_request_types import SubmitResponse
        from python.framework.types.trading_env_types.latency_simulator_types import (
            PendingOrderAction,
        )
        from python.framework.types.trading_env_types.order_types import OrderType

        order_id = self._register(processor)
        processor._handle_submit_response(SubmitResponse(
            order_id=order_id,
            action=PendingOrderAction.OPEN,
            order_type=OrderType.MARKET,
            broker_response=processor._failure_response(
                ConnectionError('Kraken API error: [EOrder:Insufficient funds]'),
                broker_ref='',
                timestamp=datetime.now(timezone.utc), operation='submit'),
        ))

        assert not processor.has_pending_orders()


class TestTimeoutTellsTheTruth:
    """An unresolved order that runs out its clock is not 'the broker rejected it'."""

    def test_unresolved_timeout_reason_names_the_transport(self, logger):
        from python.framework.types.trading_env_types.order_types import RejectionReason

        # The distinction matters on the record, not only in prose: BROKER_ERROR would put
        # our own transport fault on the venue's account, and the venue may still be
        # holding the order.
        assert RejectionReason.BROKER_UNREACHABLE.value == 'broker_unreachable'
        assert RejectionReason.BROKER_UNREACHABLE is not RejectionReason.BROKER_ERROR


class TestATimedOutUnresolvedOrderLeavesTheTracker:
    """
    An order whose write was never answered has no broker reference — and removal used to go
    through that reference alone.

    `_handle_timeout` called `mark_rejected(broker_ref=pending.broker_ref)`, which popped the
    reference index with `None`, found nothing, logged `unknown broker_ref` and returned
    BEFORE `remove_order`. `check_timeouts` deliberately does not remove, so the same order
    was returned again on every heartbeat and every tick for the rest of the session:
    `on_order_rejected` repeated at the algo, `has_pending_orders()` permanently true, and
    for a CLOSE `is_pending_close` permanently true — that position could never be closed
    again.
    """

    def _expired_unresolved_open(self, executor) -> str:
        """
        Register an unanswered submit whose timeout has already passed.

        Args:
            executor: Live executor under test

        Returns:
            The pending order's id
        """
        processor = executor.get_request_processor()
        order_id = processor.register_pending_open(
            order_id='ORD-STUCK',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.01,
            broker_ref=None,
        )
        pending = processor.get_pending_orders()[0]
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
        pending.timing.timeout_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        return order_id

    def test_the_pending_is_gone_after_its_timeout(self, executor_timeout):
        self._expired_unresolved_open(executor_timeout)

        executor_timeout.heartbeat()

        assert not executor_timeout.get_request_processor().has_pending_orders()

    def test_the_timeout_fires_once_not_on_every_heartbeat(self, executor_timeout):
        processor = executor_timeout.get_request_processor()
        self._expired_unresolved_open(executor_timeout)
        assert len(processor.check_timeouts()) == 1, 'the order must time out at all'

        executor_timeout.heartbeat()

        assert processor.check_timeouts() == [], (
            'a timeout that keeps firing repeats the rejection at the algo for the rest of '
            'the session'
        )

    def test_a_close_whose_write_was_lost_can_be_closed_again(self, executor_timeout):
        # The expensive half: is_pending_close gates every further close attempt, so a
        # position stuck behind it is unclosable for the rest of the session.
        processor = executor_timeout.get_request_processor()
        processor.register_pending_close(position_id='pos_btcusd_1', broker_ref=None)
        pending = processor.get_pending_orders()[0]
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
        pending.timing.timeout_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert executor_timeout.is_pending_close('pos_btcusd_1')

        executor_timeout.heartbeat()

        assert not executor_timeout.is_pending_close('pos_btcusd_1')

    def test_an_answered_order_still_leaves_its_reference_index(self, executor_timeout):
        # The removal must stay correct for the ordinary case too — a stale index entry
        # would let a later answer resolve an order that is already gone.
        processor = executor_timeout.get_request_processor()
        processor.register_pending_open(
            order_id='ORD-ANSWERED',
            symbol='BTCUSD',
            direction=OrderDirection.LONG,
            lots=0.01,
            broker_ref='TX-42',
        )
        pending = processor.get_pending_orders()[0]
        pending.timing.timeout_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        executor_timeout.heartbeat()

        assert not processor.has_pending_orders()
        assert processor.mark_filled(broker_ref='TX-42', fill_price=1.0,
                                     filled_lots=0.01) is None


class LevelRecorder:
    """Logger stand-in that only remembers which level each line was written at."""

    def __init__(self):
        self.levels = []

    def verbose(self, message): self.levels.append('verbose')

    def debug(self, message): self.levels.append('debug')

    def info(self, message): self.levels.append('info')

    def warning(self, message): self.levels.append('warning')

    def error(self, message): self.levels.append('error')


class TestLogLevelMatchesConsequence:
    """A self-healing re-poll must not grade a thirty-day run FINISHED_WITH_ERRORS."""

    def test_failed_submit_reaches_the_error_pot(self):
        # A submit whose fate we do not know is exactly what the pot is for (§35).
        recorder = LevelRecorder()
        processor = LiveRequestProcessor(logger=recorder, timeout_config=TimeoutConfig())
        processor._failure_response(
            ConnectionAttemptFailedError('HTTP 502'),
            broker_ref='', timestamp=datetime.now(timezone.utc), operation='submit')
        assert recorder.levels == ['error']

    def test_failed_status_poll_is_only_a_warning(self):
        # It retries on the next throttle cycle. One 502 on a re-poll must not decide the
        # run outcome — that would make the exit-code contract (#372) useless exactly where
        # it is supposed to earn its keep.
        recorder = LevelRecorder()
        processor = LiveRequestProcessor(logger=recorder, timeout_config=TimeoutConfig())
        processor._failure_response(
            ConnectionAttemptFailedError('HTTP 502'),
            broker_ref='TX-1', timestamp=datetime.now(timezone.utc),
            operation='status query', self_healing=True)
        assert recorder.levels == ['warning']

    def test_a_venue_answer_is_logged_by_neither(self):
        # A rejection is the venue speaking; the executor records it in order history.
        # Duplicating it here would double-count it in the summary.
        recorder = LevelRecorder()
        processor = LiveRequestProcessor(logger=recorder, timeout_config=TimeoutConfig())
        processor._failure_response(
            ConnectionError('Kraken API error: [EOrder:Insufficient funds]'),
            broker_ref='', timestamp=datetime.now(timezone.utc), operation='submit')
        assert recorder.levels == []


class TestAStuckUnresolvedRestingOrderIsReported:
    """
    Keeping an unresolved order is right. Saying nothing about it forever is not.

    #473 keeps a resting order whose submit answer was lost, and leaves its broker_ref None
    so a later query can still claim it. But then nothing picks it up: the poll loop skips
    every order without a reference — correctly, there is nothing to poll WITH — and
    check_timeouts iterates only the processor's dict, which resting orders never enter.

    So the order sits in the shadow for the rest of the session while `has_pending_orders()`
    stays true and blocks a single-position algo. Until #487 can ASK the venue by our own
    client order id, the least the operator is owed is being told.
    """

    def _stuck_resting_order(self, mock, executor):
        """A resting order kept in flight with no broker reference. Returns: the order id."""
        mock.feed_tick(executor, bid=49999.0, ask=50001.0)
        result = executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.01, price=45000.0))
        pending = executor.get_active_orders()[0]
        pending.broker_ref = None
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
        pending.timing.submitted_at = (
            datetime.now(timezone.utc) - timedelta(seconds=3600))
        return result.order_id

    def test_it_is_reported_as_an_error(self, mock_timeout, executor_timeout, capsys):
        self._stuck_resting_order(mock_timeout, executor_timeout)
        capsys.readouterr()

        executor_timeout.heartbeat()

        printed = capsys.readouterr().out
        assert 'UNRESOLVED' in printed, 'a stuck resting order must be reported'
        assert 'ERROR' in printed, 'it blocks the algo for the rest of the session'
        assert '#487' in printed, 'the message must name what will resolve it'

    def test_it_is_said_once_not_every_tick(self, mock_timeout, executor_timeout, capsys):
        """
        A standing condition, not an event.

        Repeating it per poll cycle would bury the channel it needs to reach — the same
        reasoning as the reconciler's first-fingerprint rule.
        """
        self._stuck_resting_order(mock_timeout, executor_timeout)
        capsys.readouterr()

        for _ in range(4):
            executor_timeout.heartbeat()

        assert capsys.readouterr().out.count('UNRESOLVED') == 1

    def test_the_order_is_kept_not_dropped(self, mock_timeout, executor_timeout):
        """Reporting must not become deleting — that is what #473 exists to prevent."""
        order_id = self._stuck_resting_order(mock_timeout, executor_timeout)

        executor_timeout.heartbeat()

        assert [p for p in executor_timeout.get_active_orders()
                if p.pending_order_id == order_id], 'the order must stay ours'

    def test_a_young_order_is_not_reported_yet(self, mock_timeout, executor_timeout, capsys):
        """An answer that has not arrived YET is not the same as one that never will."""
        mock_timeout.feed_tick(executor_timeout, bid=49999.0, ask=50001.0)
        executor_timeout.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=0.01, price=45000.0))
        pending = executor_timeout.get_active_orders()[0]
        pending.broker_ref = None
        pending.execution_state.in_flight_operation = PendingOperation.PENDING_SUBMIT
        capsys.readouterr()

        executor_timeout.heartbeat()

        assert 'UNRESOLVED' not in capsys.readouterr().out
