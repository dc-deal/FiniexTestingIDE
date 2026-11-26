"""
The Live Request Worker Survives a Job That Raises (#669)

The live request processor sends every write and read on one worker thread. A job that raised
outside its dispatcher's `try` ended that thread without a word: every later order waited in the
outbox, unsent, and the session ended SUCCESS while the venue held a position the book did not.
And what the `try` caught was classified by the REST ladder alone, so an answer we could not
read, after the venue executed, was booked as the venue's refusal.

    a write raises after the venue acted   → UNRESOLVED, the resolution asks, the fill is booked
    the venue refused                      → REJECTED, as before
    a write whose request was never built  → REJECTED, nothing sent
    a read raises                          → one failed read answer
    a job raises at all                    → one ERROR, and the worker takes the next job

Driven against the real LiveTradeExecutor on a venue that executes first and answers after
(`AcknowledgingVenueMock`), in both account models where a fill is booked.
"""

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

import python.framework.trading_env.live.live_request_processor as processor_module
from python.framework.exceptions.live_execution_errors import UnreadableVenueAnswerError
from python.framework.logging.global_logger import GlobalLogger
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_request_processor import LiveRequestProcessor
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus, TimeoutConfig
from python.framework.types.live_types.live_request_types import (
    CancelResponse,
    EditResponse,
    OrderResolveResponse,
    QueryResponse,
    SubmitResponse,
    TradesQueryResponse,
)
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.latency_simulator_types import PendingOperation
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderCapabilities,
    OrderDirection,
    OrderStatus,
    OrderType,
)
from tests.autotrader.live_executor.conftest import AcknowledgingVenueMock
from tests.shared.recording_logger import RecordingLogger

_SYMBOL = 'BTCUSD'
_LOTS = 0.01
_BID, _ASK = 50000.0, 50001.0

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


class _RecordingLogger(GlobalLogger):
    """
    A global logger that also keeps every ERROR it is given — the executor needs a whole logger,
    which the shared `RecordingLogger` stand-in is not.
    """

    def __init__(self):
        super().__init__(name='CrashedWorkerJob')
        self.errors: List[str] = []

    def error(self, message: str):
        self.errors.append(message)
        super().error(message)


def _executor(venue: AcknowledgingVenueMock, spot_mode: bool,
              logger: _RecordingLogger) -> LiveTradeExecutor:
    """
    A live executor on the acknowledging venue.

    Args:
        venue: The venue mock
        spot_mode: Which account model the portfolio keeps
        logger: The logger the executor and its processor share

    Returns:
        The executor under test
    """
    return LiveTradeExecutor(
        broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, venue),
        initial_balance=100000.0,
        account_currency='USD',
        logger=logger,
        timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
        spot_mode=spot_mode,
        initial_balances={'USD': 100000.0, 'BTC': 0.0} if spot_mode else None,
        session_key='cwj',
    )


def _tick(executor: LiveTradeExecutor) -> None:
    """Deliver one tick, after the worker has answered everything queued."""
    executor.get_request_processor().flush_outbox()
    executor.on_tick(TickData(
        timestamp=datetime.now(timezone.utc), symbol=_SYMBOL, bid=_BID, ask=_ASK))


def _heartbeat(executor: LiveTradeExecutor) -> None:
    """One heartbeat as the loop runs it: answers absorbed first, the clock set, then due work."""
    executor.get_request_processor().flush_outbox()
    executor.set_current_time(datetime.now(timezone.utc))
    executor.heartbeat()


def _buy(executor: LiveTradeExecutor) -> str:
    """
    Send a market LONG.

    Args:
        executor: The executor under test

    Returns:
        The order id
    """
    _tick(executor)
    return executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.MARKET, direction=OrderDirection.LONG,
        lots=_LOTS)).order_id


def _resolve(executor: LiveTradeExecutor, order_id: str) -> None:
    """
    Let the #487 resolution ask about an unresolved order now, and book what it learns.

    Args:
        executor: The executor under test
        order_id: The unresolved order
    """
    _heartbeat(executor)
    pending = executor.get_request_processor().get_order(order_id)
    assert pending is not None, 'the crashed write was dropped instead of kept for asking'
    assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_SUBMIT
    pending.execution_state.resolution_next_at = (
        executor.get_current_time() - timedelta(seconds=1))
    for _ in range(5):
        _heartbeat(executor)


def _rejections(executor: LiveTradeExecutor) -> List:
    """Every rejection row in the order history."""
    return [row for row in executor.get_order_history() if row.status == OrderStatus.REJECTED]


def _failing_once(answer_type: type) -> type:
    """
    A stand-in for an answer type whose first construction raises, as a bug in building it would.

    A subclass, not a function: the inbox is drained by type, so the stand-in has to stay one.

    Args:
        answer_type: The real answer type

    Returns:
        A subclass that raises once as it is built, then builds as the real type does
    """
    class _FailsOnce(answer_type):
        failed = False

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if not _FailsOnce.failed:
                _FailsOnce.failed = True
                raise RuntimeError('answer object could not be built')
            super().__init__(*args, **kwargs)

    return _FailsOnce


@ACCOUNT_MODELS
class TestAWriteThatRaisesAfterTheVenueExecuted:
    """The venue filled the order; the answer is lost to our own fault. It is asked about, and booked."""

    def test_an_answer_that_cannot_be_parsed_is_resolved_and_its_fill_booked(self, spot_mode):
        venue = AcknowledgingVenueMock()
        venue.parse_submit_fault = ValueError('garbled answer')
        logger = _RecordingLogger()
        executor = _executor(venue, spot_mode, logger)

        order_id = _buy(executor)
        _resolve(executor, order_id)

        assert len(executor.get_open_positions()) == 1, 'the venue filled it and nothing booked it'
        assert not _rejections(executor), 'an answer we could not read was booked as a refusal'
        assert len([e for e in logger.errors if 'could not be read' in e]) == 1

    def test_an_answer_object_that_fails_as_it_is_built_is_resolved_and_its_fill_booked(
            self, spot_mode, monkeypatch):
        monkeypatch.setattr(processor_module, 'SubmitResponse', _failing_once(SubmitResponse))
        venue = AcknowledgingVenueMock()
        logger = _RecordingLogger()
        executor = _executor(venue, spot_mode, logger)

        order_id = _buy(executor)
        _resolve(executor, order_id)

        assert len(executor.get_open_positions()) == 1
        assert not _rejections(executor)
        assert len([e for e in logger.errors if 'request worker crashed on a SubmitJob' in e]) == 1

    def test_the_worker_sends_the_next_order(self, spot_mode, monkeypatch):
        monkeypatch.setattr(processor_module, 'SubmitResponse', _failing_once(SubmitResponse))
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, spot_mode, _RecordingLogger())

        _buy(executor)
        _buy(executor)
        executor.get_request_processor().flush_outbox()

        assert len(venue.submits) == 2, 'the second order waited in the outbox, unsent'

    def test_an_unreadable_answer_the_call_raises_is_unresolved_too(self, spot_mode):
        class _UnreadableVenue(AcknowledgingVenueMock):
            def do_request_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                super().do_request_submit(payload)
                raise UnreadableVenueAnswerError('AddOrder answered HTTP 200 with a body that is not JSON')

        logger = _RecordingLogger()
        executor = _executor(_UnreadableVenue(), spot_mode, logger)

        order_id = _buy(executor)
        _resolve(executor, order_id)

        assert len(executor.get_open_positions()) == 1
        assert not _rejections(executor)


def _resting_limit(executor: LiveTradeExecutor) -> Any:
    """
    Rest a buy limit at the venue and return the pending the executor tracks for it.

    Args:
        executor: The executor under test

    Returns:
        The resting order, with the reference the venue gave it
    """
    _tick(executor)
    executor.open_order(OpenOrderRequest(
        symbol=_SYMBOL, order_type=OrderType.LIMIT, direction=OrderDirection.LONG,
        lots=_LOTS, price=40000.0))
    _heartbeat(executor)
    pending = executor.get_active_orders()[0]
    assert pending.broker_ref, 'fixture: the venue acknowledged the limit'
    return pending


class TestAnAmendOrCancelThatCrashes:
    """Not a refusal and not a success: the order stays as it was, and is asked about (#487)."""

    def test_a_crashed_amend_keeps_the_order_unchanged_and_in_flight(self, monkeypatch):
        venue = AcknowledgingVenueMock()
        logger = _RecordingLogger()
        executor = _executor(venue, False, logger)
        pending = _resting_limit(executor)
        monkeypatch.setattr(processor_module, 'EditResponse', _failing_once(EditResponse))

        assert executor.modify_limit_order(pending.pending_order_id, new_price=39000.0).success
        _heartbeat(executor)

        assert pending in executor.get_active_orders()
        assert pending.entry_price == pytest.approx(40000.0), 'a price the venue may never have taken'
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_MODIFY
        assert pending.execution_state.resolution_next_at is not None, 'the resolution was not armed'
        assert not _rejections(executor)
        assert len([e for e in logger.errors if 'crashed on a EditJob' in e]) == 1

    def test_a_crashed_cancel_keeps_the_order_and_asks_about_it(self, monkeypatch):
        venue = AcknowledgingVenueMock()
        executor = _executor(venue, False, _RecordingLogger())
        pending = _resting_limit(executor)
        monkeypatch.setattr(processor_module, 'CancelResponse', _failing_once(CancelResponse))

        assert executor.cancel_limit_order(pending.pending_order_id)
        _heartbeat(executor)

        assert pending in executor.get_active_orders(), 'a cancel nobody heard dropped the order'
        assert pending.execution_state.in_flight_operation is PendingOperation.PENDING_CANCEL
        assert pending.execution_state.resolution_next_at is not None

    def test_an_unreadable_answer_to_a_synchronous_cancel_is_unresolved(self, request_processor):
        class _UnreadableCancel(AcknowledgingVenueMock):
            def do_request_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                raise UnreadableVenueAnswerError('CancelOrder answered HTTP 200 with a body that is not JSON')

        answer = request_processor.cancel_order_sync('VENUE-0009', _UnreadableCancel())

        assert answer.status is BrokerOrderStatus.UNRESOLVED, (
            'the session-end cancel booked an answer it could not read as the venue refusing')


class _NativeLevelsVenue(AcknowledgingVenueMock):
    """A venue that holds a position's levels itself, whose answer to a level change cannot be read."""

    def get_order_capabilities(self) -> OrderCapabilities:
        return replace(super().get_order_capabilities(), native_position_sl_tp=True)

    def build_position_modify_payload(self, position_id: str, symbol: str,
                                      new_stop_loss: Any, new_take_profit: Any) -> Dict[str, Any]:
        return {'position_id': position_id}

    def do_request_position_modify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {'accepted': True}

    def parse_position_modify_response(self, raw: Dict[str, Any], position_id: str,
                                       ts_init: datetime) -> Any:
        raise ValueError('garbled answer')


class TestAPositionModifyThatCrashes:
    """
    A position carries no order id and no wire key, so nothing can ask about it: the levels are
    not applied, the tracker is dropped so the next change is not refused, and an ERROR says so.
    Margin only, where a venue holds a position's levels.
    """

    def test_its_levels_are_not_applied_and_the_next_change_is_taken(self):
        logger = _RecordingLogger()
        executor = _executor(_NativeLevelsVenue(), False, logger)
        _buy(executor)
        for _ in range(5):
            _heartbeat(executor)
        position, = executor.get_open_positions()

        assert executor.modify_position(position.position_id, new_stop_loss=49000.0).success
        _heartbeat(executor)

        assert executor.get_open_positions()[0].stop_loss is None, 'a protection we may not have'
        assert any('Position modify for' in e and 'UNRESOLVED' in e for e in logger.errors)
        assert executor.modify_position(position.position_id, new_stop_loss=49000.0).success, (
            'the dropped tracker still refused the next change as busy')


class TestOnlyTheVenuesRefusalIsARefusal:

    def test_a_refusal_the_call_raises_stays_rejected(self):
        class _RefusingVenue(AcknowledgingVenueMock):
            def do_request_submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                raise ConnectionError('EOrder:Insufficient funds')

        executor = _executor(_RefusingVenue(), False, _RecordingLogger())

        order_id = _buy(executor)
        _heartbeat(executor)

        assert [row.order_id for row in _rejections(executor)] == [order_id]
        assert executor.get_request_processor().get_order(order_id) is None
        assert executor.get_open_positions() == []


class TestAWriteThatWasNeverSent:

    def test_a_cancel_whose_request_cannot_be_built_is_refused_and_sends_nothing(
            self, request_processor):
        class _UnbuildableCancel(AcknowledgingVenueMock):
            def build_cancel_payload(self, broker_ref: str) -> Dict[str, Any]:
                raise KeyError('symbol')

        venue = _UnbuildableCancel()
        answers: List[Any] = []
        request_processor.set_executor_hooks(
            fill_open=lambda p, fp, ff=None: None, fill_close=lambda p, fp, ff=None: None,
            on_rejection=lambda pending, reason, message, venue_reason=None: None,
            cancel_response=lambda order_id, response: answers.append(response))
        request_processor.start_worker()
        request_processor.submit_cancel_order_async(
            order_id='ORD-1', broker_ref='VENUE-0001', adapter=venue)
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker(timeout=2.0)

        answer, = answers
        assert answer.status is BrokerOrderStatus.REJECTED
        assert answer.rejection_reason.startswith('not sent:')
        assert venue.cancels == [], 'a request that could not be built was sent'


class TestAReadThatRaises:

    def test_a_trades_query_whose_parse_raises_is_one_failed_read_with_no_trades(
            self, request_processor):
        class _UnparsableTrades(AcknowledgingVenueMock):
            def parse_trades_query_response(self, raw: Any, broker_ref: str, order_id: str) -> Any:
                raise ValueError('garbled trades')

        answers: List[TradesQueryResponse] = []
        request_processor.set_executor_hooks(
            fill_open=lambda p, fp, ff=None: None, fill_close=lambda p, fp, ff=None: None,
            on_rejection=lambda pending, reason, message, venue_reason=None: None,
            trades_response=lambda response: answers.append(response))
        request_processor.start_worker()
        request_processor.submit_trades_query_async(
            order_id='ORD-2', broker_ref='VENUE-0002', adapter=_UnparsableTrades())
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker(timeout=2.0)

        answer, = answers
        assert answer.success is False
        assert answer.trades is None, 'a failed read found nothing out — no list stands for it'

    def test_a_resolve_whose_answer_fails_as_it_is_built_is_answered_once(
            self, request_processor, monkeypatch):
        monkeypatch.setattr(processor_module, 'OrderResolveResponse',
                            _failing_once(OrderResolveResponse))
        answers: List[OrderResolveResponse] = []
        request_processor.set_executor_hooks(
            fill_open=lambda p, fp, ff=None: None, fill_close=lambda p, fp, ff=None: None,
            on_rejection=lambda pending, reason, message, venue_reason=None: None,
            order_resolve=lambda response: answers.append(response))
        request_processor.start_worker()
        request_processor.submit_order_resolve_async(
            order_id='ORD-3', broker_ref=None, client_order_id='cwj-1', adapter=AcknowledgingVenueMock(),
            range_start=datetime.now(timezone.utc) - timedelta(minutes=5),
            range_end=datetime.now(timezone.utc))
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker(timeout=2.0)

        answer, = answers
        assert answer.success is False
        assert answer.filled_lots is None

    def test_a_status_query_that_crashes_is_one_unresolved_answer_and_the_worker_goes_on(
            self, request_processor, monkeypatch):
        monkeypatch.setattr(processor_module, 'QueryResponse', _failing_once(QueryResponse))
        answers: List[QueryResponse] = []
        request_processor.set_executor_hooks(
            fill_open=lambda p, fp, ff=None: None, fill_close=lambda p, fp, ff=None: None,
            on_rejection=lambda pending, reason, message, venue_reason=None: None,
            query_response=lambda response: answers.append(response))
        venue = AcknowledgingVenueMock()
        request_processor.start_worker()
        request_processor.submit_query_order_async(
            order_id='ORD-4', broker_ref='VENUE-0004', adapter=venue)
        request_processor.submit_query_order_async(
            order_id='ORD-5', broker_ref='VENUE-0005', adapter=venue)
        request_processor.flush_outbox()
        request_processor.drain_inbox()
        request_processor.stop_worker(timeout=2.0)

        assert [answer.order_id for answer in answers] == ['ORD-4', 'ORD-5']
        assert answers[0].broker_response.status is BrokerOrderStatus.UNRESOLVED
        assert venue.queries == ['VENUE-0004', 'VENUE-0005'], 'the second read was never sent'


class _WorkingVenue(AcknowledgingVenueMock):
    """A venue that takes a market order and keeps it working, as one that has not filled yet."""

    def __init__(self):
        super().__init__()
        self.fill_market_orders = False


def _working_buy_past_its_timeout(executor: LiveTradeExecutor) -> str:
    """
    A market buy the venue acknowledged and keeps working, its fill timeout already past.

    Args:
        executor: The executor under test

    Returns:
        The order id
    """
    order_id = _buy(executor)
    processor = executor.get_request_processor()
    processor.flush_outbox()
    processor.drain_inbox()
    pending = processor.get_order(order_id)
    assert pending.broker_ref, 'fixture: the venue acknowledged the order'
    pending.timing.order_timeout_deadline_monotonic = time.monotonic() - 1.0
    return order_id


class TestTheLoopsOwnCallsToTheVenue:
    """
    The fill timeout asks, cancels and asks again on the loop's own thread, not on the worker. An
    answer it cannot read there ended the pass with an exception — and with it a live session. It
    is an unknown outcome there too, and the order goes to the resolution.
    """

    @ACCOUNT_MODELS
    def test_a_cancel_answer_that_cannot_be_read_at_the_timeout_hands_the_order_on(
            self, spot_mode):
        class _UnreadableCancel(_WorkingVenue):
            def parse_cancel_response(self, raw: Any, broker_ref: str, ts_init: datetime) -> Any:
                raise KeyError('count')

        executor = _executor(_UnreadableCancel(), spot_mode, _RecordingLogger())
        order_id = _working_buy_past_its_timeout(executor)

        _heartbeat(executor)

        pending = executor.get_request_processor().get_order(order_id)
        assert pending is not None, 'the order was dropped although nobody knows its fate'
        assert pending.execution_state.timeout_handed_to_resolution
        assert not _rejections(executor)

    @ACCOUNT_MODELS
    def test_a_status_answer_that_cannot_be_read_at_the_timeout_hands_the_order_on(
            self, spot_mode):
        class _UnreadableStatus(_WorkingVenue):
            def parse_query_response(self, raw: Any, broker_ref: str, ts_init: datetime,
                                     market: Any = None) -> Any:
                raise ValueError('garbled status')

        logger = _RecordingLogger()
        executor = _executor(_UnreadableStatus(), spot_mode, logger)
        order_id = _working_buy_past_its_timeout(executor)

        _heartbeat(executor)

        pending = executor.get_request_processor().get_order(order_id)
        assert pending is not None
        assert pending.execution_state.timeout_handed_to_resolution
        assert any('garbled status' in e for e in logger.errors)

    def test_each_synchronous_write_answers_an_unreadable_answer_unresolved(self, request_processor):
        class _AnswersUnreadably:
            """The venue takes every request and answers each with something nobody can read."""

            def build_cancel_payload(self, broker_ref: str) -> Dict[str, Any]:
                return {'broker_ref': broker_ref}

            def do_request_cancel(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                return {'answered': True}

            def parse_cancel_response(self, raw: Any, broker_ref: str, ts_init: datetime) -> Any:
                raise KeyError('count')

            def build_modify_payload(self, **kwargs: Any) -> Dict[str, Any]:
                return dict(kwargs)

            def do_request_modify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
                return {'answered': True}

            def parse_modify_response(self, raw: Any, original_broker_ref: str,
                                      ts_init: datetime) -> Any:
                raise KeyError('txid')

        venue = _AnswersUnreadably()
        cancel = request_processor.cancel_order_sync('VENUE-0010', venue)
        modify = request_processor.modify_order_sync(
            broker_ref='VENUE-0010', symbol=_SYMBOL, order_type=OrderType.LIMIT,
            new_price=39000.0, new_stop_loss=None, new_take_profit=None, adapter=venue)

        assert cancel.status is BrokerOrderStatus.UNRESOLVED
        assert modify.status is BrokerOrderStatus.UNRESOLVED


class TestAPositionModifyWithNoAdapterBehindIt:

    def test_it_is_refused_as_not_supported_before_anything_is_sent(self):
        logger = RecordingLogger()
        processor = LiveRequestProcessor(logger=logger, timeout_config=TimeoutConfig())
        answers: List[Any] = []
        processor.set_executor_hooks(
            fill_open=lambda p, fp, ff=None: None, fill_close=lambda p, fp, ff=None: None,
            on_rejection=lambda pending, reason, message, venue_reason=None: None,
            position_modify_response=lambda position_id, response: answers.append(response))
        processor.start_worker()
        processor.submit_modify_position_async(
            position_id='POS-1', symbol=_SYMBOL, new_stop_loss=49000.0, new_take_profit=None,
            adapter=AcknowledgingVenueMock())
        processor.flush_outbox()
        processor.drain_inbox()
        processor.stop_worker(timeout=2.0)

        answer, = answers
        assert answer.status is BrokerOrderStatus.REJECTED
        assert answer.rejection_reason.startswith('not supported:')
        assert len([message for level, message in logger.lines
                    if level == 'error' and 'implements no position modify' in message]) == 1
