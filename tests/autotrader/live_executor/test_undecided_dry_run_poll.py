"""
A dry-run poll that cannot decide is REPORTED, not read as "still working" (#505).

The dry-run simulator plays the venue. When it runs out of the facts it needs — no quote to
compare a resting order's price against, or an order type nothing in this project models — it
refuses instead of inventing a fill, and the refusal has to reach a human. That is the whole
point: `dry_run: true` is the shipped default for kraken_spot, so a rehearsal is what an
operator looks at before risking money, and a rehearsal that silently exercises nothing looks
exactly like one that passed.

The refusal cannot be logged where it happens: the adapter has no logger, by design — it is a
transport. So it travels on the response as `undecided_reason`, and the EXECUTOR is what makes
it visible, in the session channel that feeds the error pot and the run summary (§35).

Said ONCE per order, like its unknown-order sibling: the next poll produces the same
non-answer, and one per throttle cycle would bury the channel it has to reach.
"""

import json
from datetime import datetime, timezone

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.live_types.live_request_types import QueryJob, QueryResponse
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)

_TS = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


class _MessageRecorder:
    """Records what reached which channel — the project's spy idiom, plus the text."""

    def __init__(self):
        self.errors = []
        self.warnings = []

    def verbose(self, message): pass

    def debug(self, message): pass

    def info(self, message): pass

    def warning(self, message): self.warnings.append(message)

    def error(self, message): self.errors.append(message)


def _undecided(broker_ref: str) -> BrokerResponse:
    """
    The response shape a refusing dry-run poll produces.

    Args:
        broker_ref: The reference that was polled

    Returns:
        A PENDING BrokerResponse carrying a reason
    """
    return BrokerResponse(
        broker_ref=broker_ref,
        status=BrokerOrderStatus.PENDING,
        undecided_reason='no quote to compare the limit against',
        timestamp=_TS,
    )


class TestTheRefusalReachesTheOperator:
    """A silent PENDING is indistinguishable from an order that is simply waiting."""

    def test_it_is_logged_as_an_error_not_swallowed(self, executor_delayed):
        executor = executor_delayed
        recorder = _MessageRecorder()
        executor.logger = recorder
        executor._request_processor.register_pending_open(
            order_id='ord-1', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.01, broker_ref='DRYRUN-000001', order_kwargs={})
        pending = executor._request_processor.get_by_broker_ref('DRYRUN-000001')

        executor._handle_broker_response(pending, _undecided('DRYRUN-000001'))

        assert len(recorder.errors) == 1, 'the error pot is where this belongs (§35)'
        assert 'cannot decide' in recorder.errors[0]
        assert 'no quote to compare the limit against' in recorder.errors[0]

    def test_the_order_is_neither_booked_nor_dropped(self, executor_delayed):
        executor = executor_delayed
        executor._request_processor.register_pending_open(
            order_id='ord-2', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.01, broker_ref='DRYRUN-000002', order_kwargs={})
        pending = executor._request_processor.get_by_broker_ref('DRYRUN-000002')

        executor._handle_broker_response(pending, _undecided('DRYRUN-000002'))

        assert executor._request_processor.get_by_broker_ref('DRYRUN-000002') is not None, (
            'a refusal is not a rejection — the order is still ours')
        assert executor.portfolio.open_positions == {}, 'and nothing was booked'

    def test_it_is_said_once_however_often_the_poll_repeats(self, executor_delayed):
        """
        With `poll_interval_ms = 5000` a session would otherwise produce a line every five
        seconds for the whole run.
        """
        executor = executor_delayed
        recorder = _MessageRecorder()
        executor.logger = recorder
        executor._request_processor.register_pending_open(
            order_id='ord-3', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.01, broker_ref='DRYRUN-000003', order_kwargs={})
        pending = executor._request_processor.get_by_broker_ref('DRYRUN-000003')

        for _ in range(10):
            executor._handle_broker_response(pending, _undecided('DRYRUN-000003'))

        assert len(recorder.errors) == 1

    def test_a_normal_pending_says_nothing(self, executor_delayed):
        """The guard must not fire on an order that is legitimately waiting."""
        executor = executor_delayed
        recorder = _MessageRecorder()
        executor.logger = recorder
        executor._request_processor.register_pending_open(
            order_id='ord-4', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.01, broker_ref='DRYRUN-000004', order_kwargs={})
        pending = executor._request_processor.get_by_broker_ref('DRYRUN-000004')

        executor._handle_broker_response(pending, BrokerResponse(
            broker_ref='DRYRUN-000004',
            status=BrokerOrderStatus.PENDING,
            timestamp=_TS))

        assert recorder.errors == []


class TestARestingOrderRefusalIsReportedToo:
    """Both poll paths, because a resting order is the case #503 will rehearse."""

    def test_the_resting_path_reports_and_keeps_the_order(self, mock_delayed):
        executor = mock_delayed.create_executor()
        # A tick first: every order path reads the canonical clock, and the resting poll
        # gate needs one too (#355).
        mock_delayed.feed_tick(executor, symbol='BTCUSD', bid=50000.0, ask=50001.0)
        result = executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
            order_type=OrderType.LIMIT, price=1.0))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()
        resting = executor._active_limit_orders
        assert resting, 'the limit order must be in the resting list for this to prove anything'
        pending = resting[0]

        recorder = _MessageRecorder()
        executor.logger = recorder
        executor._handle_query_response(QueryResponse(
            order_id=pending.pending_order_id,
            broker_response=_undecided(pending.broker_ref)))

        assert len(recorder.errors) == 1
        assert 'cannot decide' in recorder.errors[0]
        assert pending in executor._active_limit_orders, 'still resting, not dropped'
        assert result.status is not None


class TestTheQuoteActuallyReachesTheSimulatedVenue:
    """
    The hand-off itself, which nothing covered (#505).

    Stage 3 threads the quote from the executor's tick through QueryJob and the parse layer
    into the simulator. Every step of it could be dropped — a forgotten `market=` at any hand-
    off — and the whole suite would stay green while every dry-run resting order silently
    refused for the rest of the session. So the seam is pinned here, not only its ends.
    """

    def test_the_poll_job_carries_the_tick_the_executor_holds(self, mock_delayed):
        executor = mock_delayed.create_executor()
        tick = mock_delayed.feed_tick(
            executor, symbol='BTCUSD', bid=50000.0, ask=50001.0)
        executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', direction=OrderDirection.LONG, lots=0.01,
            order_type=OrderType.LIMIT, price=1.0))
        executor._request_processor.flush_outbox()
        executor._request_processor.drain_inbox()

        jobs = []
        original = executor._request_processor.submit_query_order_async
        executor._request_processor.submit_query_order_async = (
            lambda **kwargs: (jobs.append(kwargs), original(**kwargs)))
        for pending in executor._active_limit_orders:
            pending.execution_state.last_polled_at_ms = 0.0
            pending.execution_state.in_flight_query = False
        executor._process_active_orders()

        assert jobs, 'the resting order must have been polled for this to prove anything'
        assert jobs[0]['market'] is tick, (
            'the poll carries the quote the executor was holding — drop this and every '
            'dry-run resting order refuses forever, silently')

    def test_the_parse_layer_hands_the_quote_to_the_simulator(self):
        """
        The last hand-off, driven directly against a dry-run Kraken adapter: the answer
        changes with the quote, which is only possible if the quote arrived.
        """
        with open('configs/brokers/kraken/kraken_spot_broker_config.json', encoding='utf-8') as fh:
            adapter = KrakenAdapter(json.load(fh))
        submit = adapter.parse_submit_response(
            {'__dry_run_op__': 'submit', 'lots': 1.0, 'ordertype': 'limit',
             'price': 3900.0, 'price2': None},
            timestamp=_TS, direction=OrderDirection.LONG, order_type=OrderType.LIMIT)
        raw = {'__dry_run_op__': 'query', 'broker_ref': submit.broker_ref}

        away = adapter.parse_query_response(
            raw, submit.broker_ref, _TS,
            market=TickData(timestamp=_TS, symbol='ETHUSD', bid=3999.0, ask=4001.0))
        arrived = adapter.parse_query_response(
            raw, submit.broker_ref, _TS,
            market=TickData(timestamp=_TS, symbol='ETHUSD', bid=3898.0, ask=3900.0))

        assert away.status == BrokerOrderStatus.PENDING, 'the limit was not reachable'
        assert arrived.status == BrokerOrderStatus.FILLED, 'and then it was'
        assert arrived.fill_price == 3900.0

    def test_the_worker_forwards_the_job_s_quote_to_the_parse_layer(self, request_processor):
        """
        The MIDDLE hand-off, and the one a negative control caught as uncovered.

        Dropping `market=job.market` in `_dispatch_query_job` left every other test green
        while every dry-run resting order refused for the rest of the session. Driven against
        the worker handler directly, because that is the step: same order, two jobs, two
        different quotes, two different answers.
        """
        with open('configs/brokers/kraken/kraken_spot_broker_config.json', encoding='utf-8') as fh:
            adapter = KrakenAdapter(json.load(fh))
        submit = adapter.parse_submit_response(
            {'__dry_run_op__': 'submit', 'lots': 1.0, 'ordertype': 'limit',
             'price': 3900.0, 'price2': None},
            timestamp=_TS, direction=OrderDirection.LONG, order_type=OrderType.LIMIT)

        def _poll_with(bid: float, ask: float) -> BrokerResponse:
            request_processor._dispatch_query_job(QueryJob(
                order_id='ord-worker', broker_ref=submit.broker_ref, adapter=adapter,
                market=TickData(timestamp=_TS, symbol='ETHUSD', bid=bid, ask=ask)))
            return request_processor._http_inbox.get_nowait().broker_response

        away = _poll_with(3999.0, 4001.0)
        arrived = _poll_with(3898.0, 3900.0)

        assert away.status == BrokerOrderStatus.PENDING
        assert arrived.status == BrokerOrderStatus.FILLED, (
            'the worker has to carry the job quote through — without it the simulated venue '
            'is blind again and refuses forever')


class TestARefusalGradesTheSession:
    """
    A rehearsal that could not rehearse must not read as a clean run (#505 · finding 192).

    The chain is short and each link is somebody else's rule, which is why it is pinned here
    rather than trusted: the executor logs the refusal to the SESSION channel (§35), a session
    ERROR re-grades an otherwise clean run to FINISHED_WITH_ERRORS (#372), and that outcome
    exits 3. So a dry-run session that refused an order tells a supervisor it proved less than
    it appears to — which is the point.

    Answered by reading the code rather than by a live probe, and pinned so the answer cannot
    drift: nothing about it needs a venue.
    """

    def _result_with_refusal(self) -> AutoTraderResult:
        """
        A finished session carrying exactly one refusal on the session channel.

        Returns:
            An AutoTraderResult shaped like the run the executor would have produced
        """
        return AutoTraderResult(
            shutdown_mode='normal',
            session_logger_buffer=[LogRecord(
                level=LogLevel.ERROR,
                timestamp=_TS,
                scope='dry_run_resting_probe',
                message='🎭 Dry-run cannot decide order ord-1 (broker_ref=DRYRUN-000001): '
                        'no quote to compare the limit against.')],
        )

    def test_a_refusal_grades_the_run_finished_with_errors(self):
        assert self._result_with_refusal().get_outcome() == RunOutcome.FINISHED_WITH_ERRORS

    def test_and_therefore_exits_three(self):
        """
        3 is 'the run completed, but errors were logged' — the honest answer for a rehearsal
        that skipped the path it was started for. 0 would let an unattended supervisor record
        a clean run that exercised nothing.
        """
        assert self._result_with_refusal().get_exit_code() == 3

    def test_a_session_that_refused_nothing_still_exits_zero(self):
        """The guard must not grade every dry-run session as faulty."""
        assert AutoTraderResult(shutdown_mode='normal').get_exit_code() == 0
