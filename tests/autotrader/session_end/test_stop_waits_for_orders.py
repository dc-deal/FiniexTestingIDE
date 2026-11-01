"""
A normal stop waits for the orders on their way (#630).

The loop's `run` ends, and before the session-end event a bounded wait lets what the session sent a
moment ago settle. Every failure is provoked from outside, on a mock venue: the answer to a submit
is held back after the venue acted, so the order is executed while its answer is on the wire. Two
answer shapes, the two the project's mocks have: a venue shaped like Kraken answers with a reference
alone and shows the fill to a later status read, and the stock mock carries the fill in its answer.

Most of it moves money, and that part runs in both account models.
"""

import contextlib
import io
import queue
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set
from unittest.mock import MagicMock

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.autotrader.shutdown_status_holder import ShutdownStatusHolder
from python.framework.autotrader.stop_budget import StopBudget
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.decision_event_dispatcher import DecisionEventDispatcher
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.autotrader_types.shutdown_status_types import StopWaitOutcome
from python.framework.types.config_types.autotrader_defaults_config_types import (
    AutotraderExecutionDefaults,
    ShutdownDefaults,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.decision_event_types import DecisionEventType, SessionEndSeverity
from python.framework.types.live_types.broker_truth_types import (
    BrokerTruthPart,
    BrokerTruthReadReason,
)
from python.framework.types.live_types.live_execution_types import TimeoutConfig
from python.framework.types.log_level import LogLevel
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderAction,
    OrderDirection,
    OrderEndReason,
    OrderInitiator,
    OrderResult,
    OrderStatus,
    OrderType,
)
from python.framework.utils.connection_ladder import ConnectionLadder
from python.framework.utils.run_id_utils import mint_run_id
from tests.autotrader.cold_start.conftest import RecordingLogger
from tests.autotrader.live_executor.conftest import AcknowledgingVenueMock
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])
ANSWER_SHAPES = pytest.mark.parametrize(
    'shape', ['acknowledging', 'fill_in_answer'])

_SPOT_PROFILE = 'configs/autotrader_profiles/mock/session_stop_awaited_order.json'
_MARGIN_PROFILE = 'configs/autotrader_profiles/mock/session_stop_awaited_order_margin.json'

_START_USD = 100000.0
# What the two whole-session profiles start with, by the symbol each trades
_SESSION_START = {'BTCUSD': 10000.0, 'USDJPY': 100000.0}
_BID = 59999.0
_ASK = 60001.0
_LOTS = 0.01
_MARKET_TIME = datetime(2026, 1, 25, 12, 0, 0, tzinfo=timezone.utc)
# kraken_spot is a crypto market: its trading day flips at midnight UTC
_MIDNIGHT = datetime(2026, 1, 26, 0, 0, 0, tzinfo=timezone.utc)
# Long enough for any answer these tests let travel, and the wait ends with the answer
_PATIENT = 20.0
_SIGTERM_REASON = 'termination signal (SIGTERM)'


def _settings(**overrides: Any) -> ShutdownDefaults:
    """
    Stop settings with a patient order wait and joins that do not hold a test up.

    Args:
        overrides: The settings a test sets itself

    Returns:
        The settings
    """
    values: Dict[str, Any] = {'order_wait_s': _PATIENT, 'request_worker_join_s': 0.05}
    return ShutdownDefaults(**{**values, **overrides})


class _HeldAnswer:
    """Holds the venue's answer to a submit, after the venue acted, until it is released."""

    def __init__(self, adapter: Any) -> None:
        self._gate = threading.Event()
        self._answer = adapter.do_request_submit
        self.entered = threading.Event()
        adapter.do_request_submit = self._held

    def _held(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = self._answer(payload)
        self.entered.set()
        self._gate.wait(timeout=30)
        return raw

    def release(self) -> None:
        """Let the answer travel."""
        self._gate.set()

    def release_during_the_wait(self) -> None:
        """Let the answer travel once the wait has begun."""
        threading.Timer(0.05, self.release).start()


class _HearingLogic:
    """What a decision logic hears, in the order it hears it."""

    def __init__(self, on_filled: Optional[Callable[[], None]] = None) -> None:
        self.heard: List[str] = []
        self._on_filled = on_filled

    @classmethod
    def get_subscribed_events(cls) -> Set[DecisionEventType]:
        return set(DecisionEventType)

    def on_order_filled(self, event: Any) -> None:
        self.heard.append(f'filled:{event.order_id}')
        if self._on_filled is not None:
            on_filled, self._on_filled = self._on_filled, None
            on_filled()

    def on_order_rejected(self, event: Any) -> None:
        self.heard.append(f'rejected:{event.order_id}')

    def on_order_unaccounted(self, event: Any) -> None:
        self.heard.append(f'unaccounted:{event.order_id}')

    def on_order_cancelled(self, event: Any) -> None:
        self.heard.append(f'cancelled:{event.order_id}')

    def on_partial_close(self, event: Any) -> None:
        self.heard.append('partial_close')

    def on_position_closed(self, event: Any) -> None:
        self.heard.append(f'closed:{event.position_id}')

    def on_session_end(self, event: Any) -> None:
        self.heard.append('session_end')


class _Stage:
    """
    One executor over a mock venue, one tick fed, and the loop that stops it.

    Args:
        shape: 'acknowledging' or 'fill_in_answer'
        spot_mode: Which account model the portfolio keeps
        settings: The stop settings
        at: The instant of the tick fed
        connection: The venue's ladder policy, for a test of what a stop can afford
    """

    def __init__(self, shape: str, spot_mode: bool, settings: ShutdownDefaults, at: datetime,
                 connection: Optional[ConnectionPolicy]) -> None:
        self.venue = (AcknowledgingVenueMock() if shape == 'acknowledging'
                      else MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL))
        self.spot_mode = spot_mode
        self.settings = settings
        self.logger = ScenarioLogger(
            scenario_set_name='stop_waits', scenario_name='for_orders',
            run_timestamp=datetime.now(tz=timezone.utc),
            run_id=mint_run_id(datetime.now(timezone.utc)))
        self.executor = LiveTradeExecutor(
            broker_config=BrokerConfig(BrokerType.KRAKEN_SPOT, self.venue),
            initial_balance=_START_USD,
            account_currency='USD',
            logger=self.logger,
            timeout_config=TimeoutConfig(order_timeout_seconds=30.0),
            session_key='test',
            spot_mode=spot_mode,
            initial_balances={'USD': _START_USD, 'BTC': 0.0} if spot_mode else None,
            poll_interval_ms=10,
            shutdown_config=settings,
            rest_ladder=(ConnectionLadder('broker_rest', connection, self.logger)
                         if connection is not None else None),
        )
        MockOrderExecution().feed_tick(self.executor, bid=_BID, ask=_ASK, timestamp=at)
        self.alerts = MagicMock()
        self.holds: List[_HeldAnswer] = []

    def hold_answers(self) -> _HeldAnswer:
        """
        Hold the venue's next answer back.

        Returns:
            The hold
        """
        held = _HeldAnswer(self.venue)
        self.holds.append(held)
        return held

    def buy_at_market(self) -> OrderResult:
        """
        Send one market buy.

        Returns:
            What the send returned
        """
        return self.executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=_LOTS))

    def rest_a_limit(self) -> OrderResult:
        """
        Send one limit buy below the market.

        Returns:
            What the send returned
        """
        return self.executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.LIMIT,
            direction=OrderDirection.LONG, lots=_LOTS, price=50000.0))

    def open_a_position(self) -> str:
        """
        Open one position and let its fill be booked.

        Returns:
            The position's id
        """
        self.buy_at_market()
        deadline = time.monotonic() + 5.0
        while not self.executor.get_open_positions():
            assert time.monotonic() < deadline, 'fixture opened no position'
            self.executor.heartbeat()
            time.sleep(0.005)
        return self.executor.get_open_positions()[0].position_id

    def loop(self, logic: Optional[_HearingLogic] = None, **options: Any) -> AutotraderTickLoop:
        """
        The tick loop of this session, with no tick to come.

        Args:
            logic: What hears the decision events; nothing hears them when None
            options: Further collaborators the session would hand the loop, by name

        Returns:
            The loop, not yet run
        """
        tick_queue: queue.Queue = queue.Queue()
        tick_queue.put(None)
        config = AutoTraderConfig(
            profile_name='stop_waits', symbol='BTCUSD', broker_type='kraken_spot',
            adapter_type='mock', shutdown=self.settings,
            execution=AutotraderExecutionDefaults(heartbeat_interval_ms=5))
        return AutotraderTickLoop(
            config=config,
            tick_queue=tick_queue,
            tick_source=MagicMock(),
            executor=self.executor,
            bar_controller=BarRenderingController(logger=self.logger),
            worker_orchestrator=options.pop('worker_orchestrator', MagicMock()),
            decision_logic=options.pop('decision_logic', MagicMock()),
            clipping_monitor=LiveClippingMonitor(),
            logger=self.logger,
            trading_model=TradingModel.SPOT if self.spot_mode else TradingModel.MARGIN,
            run_dir=None,
            display_queue=None,
            dry_run=True,
            operator_alerts=self.alerts,
            decision_event_dispatcher=(
                DecisionEventDispatcher(logic, self.executor, self.logger)
                if logic is not None else None),
            **options,
        )

    def stop(self, logic: Optional[_HearingLogic] = None,
             severity: SessionEndSeverity = SessionEndSeverity.NORMAL,
             **options: Any) -> AutotraderTickLoop:
        """
        A stop asked for before any pass, so the loop goes straight to what follows its last one.

        Args:
            logic: What hears the decision events
            severity: The severity of the end
            options: Further collaborators, by name

        Returns:
            The loop, after its run
        """
        self.executor.request_session_end(_SIGTERM_REASON, severity)
        loop = self.loop(logic, **options)
        loop.run()
        return loop

    def errors(self) -> List[str]:
        """
        What reached the session channel as an error.

        Returns:
            The messages
        """
        return [record.message for record in self.logger.get_records(LogLevel.ERROR)]

    def info(self) -> List[str]:
        """
        What reached the session channel as information.

        Returns:
            The messages
        """
        return [record.message for record in self.logger.get_records(LogLevel.INFO)]

    def endings(self) -> List[OrderResult]:
        """
        Every row of the order history that is not a submission.

        Returns:
            The rows
        """
        return [row for row in self.executor.get_order_history()
                if row.status is not OrderStatus.PENDING]


@pytest.fixture
def stage():
    """
    Builds a stage, and leaves no answer held and no worker running behind it.

    Yields:
        A function taking the shape, the account model, the settings and the tick's instant
    """
    built: List[_Stage] = []

    def build(shape: str = 'acknowledging', spot_mode: bool = False,
              settings: Optional[ShutdownDefaults] = None, at: datetime = _MARKET_TIME,
              connection: Optional[ConnectionPolicy] = None) -> _Stage:
        built.append(_Stage(shape, spot_mode, settings or _settings(), at, connection))
        return built[-1]

    yield build
    for each in built:
        for held in each.holds:
            held.release()
        each.executor.get_request_processor().stop_worker(0.0)


def _assert_filled_book(built: _Stage, row: OrderResult) -> None:
    """
    The book after one market buy was filled, in the stage's account model.

    Args:
        built: The stage
        row: The fill's row
    """
    assert [position.lots for position in built.executor.get_open_positions()] == [_LOTS]
    assert row.commission > 0
    if built.spot_mode:
        balances = built.executor.portfolio.get_balances()
        assert balances['BTC'] == pytest.approx(_LOTS)
        assert balances['USD'] == pytest.approx(_START_USD - _LOTS * _ASK - row.commission)
    else:
        # A margin account pays the entry fee when the trade closes
        assert built.executor.get_balance() == pytest.approx(_START_USD)


def _assert_untouched_book(built: _Stage) -> None:
    """
    The book of a session that booked no fill, in the stage's account model.

    Args:
        built: The stage
    """
    assert built.executor.get_open_positions() == []
    if built.spot_mode:
        balances = built.executor.portfolio.get_balances()
        assert balances == {'USD': pytest.approx(_START_USD), 'BTC': pytest.approx(0.0)}
    else:
        assert built.executor.get_balance() == pytest.approx(_START_USD)


class TestAnAwaitedOpenIsBookedByTheWait:

    @ACCOUNT_MODELS
    @ANSWER_SHAPES
    def test_its_fill_reaches_the_book_and_the_logic(self, stage, shape, spot_mode):
        built = stage(shape, spot_mode)
        held = built.hold_answers()
        order_id = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        logic = _HearingLogic()

        built.stop(logic)

        filled = built.endings()
        assert [row.status for row in filled] == [OrderStatus.EXECUTED]
        assert filled[0].executed_lots == pytest.approx(_LOTS)
        _assert_filled_book(built, filled[0])
        assert logic.heard == [f'filled:{order_id}', 'session_end']
        assert built.errors() == []
        assert built.executor.get_awaited_orders() == []
        built.alerts.send.assert_not_called()

    @ACCOUNT_MODELS
    @ANSWER_SHAPES
    def test_an_answer_already_waiting_is_read(self, stage, shape, spot_mode):
        """The session ends a moment after the answer came: nothing read it, today nor later."""
        built = stage(shape, spot_mode)
        built.buy_at_market()
        processor = built.executor.get_request_processor()
        assert processor.flush_outbox(5.0)

        built.stop()

        filled = built.endings()
        assert [row.status for row in filled] == [OrderStatus.EXECUTED]
        _assert_filled_book(built, filled[0])
        assert built.errors() == []


class TestAnAwaitedMarketClose:

    @ACCOUNT_MODELS
    @ANSWER_SHAPES
    def test_its_fill_closes_the_position(self, stage, shape, spot_mode):
        built = stage(shape, spot_mode)
        position_id = built.open_a_position()
        held = built.hold_answers()
        built.executor.close_position(position_id)
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        logic = _HearingLogic()

        built.stop(logic)

        assert built.executor.get_open_positions() == []
        trades = built.executor.get_trade_history()
        assert [trade.position_id for trade in trades] == [position_id]
        assert logic.heard[-1] == 'session_end'
        assert f'closed:{position_id}' in logic.heard
        assert built.errors() == []
        if spot_mode:
            balances = built.executor.portfolio.get_balances()
            assert balances['BTC'] == pytest.approx(0.0)
            assert balances['USD'] == pytest.approx(_START_USD + trades[0].net_pnl)
        else:
            assert built.executor.get_balance() == pytest.approx(_START_USD + trades[0].net_pnl)

    @ACCOUNT_MODELS
    def test_a_close_with_no_answer_leaves_the_position_open(self, stage, spot_mode):
        """Nobody knows whether it sold: the position stays what the session last knew."""
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.05))
        position_id = built.open_a_position()
        held = built.hold_answers()
        built.executor.close_position(position_id)
        assert held.entered.wait(5), 'the worker never reached the venue'
        logic = _HearingLogic()

        built.stop(logic)

        assert [position.position_id for position in built.executor.get_open_positions()] == [
            position_id]
        assert built.executor.get_trade_history() == []
        assert logic.heard == [f'unaccounted:{position_id}', 'session_end']
        row = built.endings()[-1]
        assert (row.status, row.action) == (OrderStatus.UNACCOUNTED, OrderAction.CLOSE)
        assert any(position_id in message for message in built.errors())


class TestARestingOrderWithNoReferenceYet:

    def test_the_wait_lasts_until_its_reference_came(self, stage):
        built = stage()
        held = built.hold_answers()
        order_id = built.rest_a_limit().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        assert [row.order_id for row in built.executor.get_awaited_orders()] == [order_id]

        built.stop()

        resting = built.executor.get_active_orders()
        assert [order.pending_order_id for order in resting] == [order_id]
        assert resting[0].broker_ref in built.venue.submits
        assert built.executor.get_awaited_orders() == []
        assert built.errors() == []

    def test_the_cleanup_then_cancels_it_with_its_reference(self, stage):
        """Without the wait it had no reference to cancel with, and stayed at the venue."""
        built = stage()
        held = built.hold_answers()
        built.rest_a_limit()
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()

        built.stop()
        built.executor.finish_remaining_orders()

        assert built.venue.cancels == built.venue.submits
        row = built.endings()[-1]
        assert (row.status, row.initiator, row.end_reason) == (
            OrderStatus.CANCELLED, OrderInitiator.FRAMEWORK, OrderEndReason.SESSION_END)
        assert built.errors() == []


class TestNothingAwaitedCostsNothing:

    def _stop_watching_the_heartbeat(self, built: _Stage) -> MagicMock:
        watched = MagicMock(wraps=built.executor.heartbeat)
        built.executor.heartbeat = watched
        built.stop()
        return watched

    @ACCOUNT_MODELS
    def test_no_pass_and_no_line(self, stage, spot_mode):
        built = stage('acknowledging', spot_mode)

        watched = self._stop_watching_the_heartbeat(built)

        watched.assert_not_called()
        assert not any('⏳' in message for message in built.info())

    def test_a_resting_order_the_venue_confirmed_is_not_on_its_way(self, stage):
        built = stage()
        built.rest_a_limit()
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        assert built.executor.get_active_orders()[0].broker_ref

        watched = self._stop_watching_the_heartbeat(built)

        watched.assert_not_called()

    def test_what_a_pass_does_not_touch(self, stage):
        """A stop's wait resolves orders. The decisions have stopped."""
        built = stage()
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        decision_logic = MagicMock()
        worker_orchestrator = MagicMock()
        reconciler = MagicMock()
        state_frames = MagicMock()
        signal_inbox = MagicMock()

        built.stop(decision_logic=decision_logic, worker_orchestrator=worker_orchestrator,
                   reconciler=reconciler, state_frames=state_frames, signal_inbox=signal_inbox)

        assert built.alerts.get_loop_progress.return_value.stamp.called
        decision_logic.execute_decision.assert_not_called()
        worker_orchestrator.process_heartbeat.assert_not_called()
        reconciler.reconcile_if_due.assert_not_called()
        state_frames.on_pass.assert_not_called()
        signal_inbox.drain.assert_not_called()


class TestTheBoundRunsOut:

    @ACCOUNT_MODELS
    @ANSWER_SHAPES
    def test_unaccounted_an_error_and_no_fill_invented(self, stage, shape, spot_mode):
        built = stage(shape, spot_mode, _settings(order_wait_s=0.05))
        held = built.hold_answers()
        order_id = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'

        built.stop()

        row = built.endings()[-1]
        assert row.status is OrderStatus.UNACCOUNTED
        assert (row.initiator, row.end_reason) == (
            OrderInitiator.FRAMEWORK, OrderEndReason.SESSION_END)
        assert row.executed_price in (None, 0.0)
        assert not [each for each in built.endings() if each.status is OrderStatus.REJECTED]
        _assert_untouched_book(built)
        errors = [message for message in built.errors() if order_id in message]
        assert len(errors) == 1
        assert 'UNACCOUNTED' in errors[0]

    @ACCOUNT_MODELS
    def test_the_logic_hears_it_before_the_session_end(self, stage, spot_mode):
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.05))
        held = built.hold_answers()
        order_id = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'
        logic = _HearingLogic()

        built.stop(logic)

        assert logic.heard == [f'unaccounted:{order_id}', 'session_end']

    def test_the_operator_hears_it_once_with_the_order_named(self, stage):
        built = stage('acknowledging', False, _settings(order_wait_s=0.05))
        held = built.hold_answers()
        order_id = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'

        built.stop()

        built.alerts.send.assert_called_once()
        kind, level, title, detail = built.alerts.send.call_args.args
        assert kind is OperatorAlertKind.ORDERS_UNANSWERED_AT_STOP
        assert level is OperatorAlertLevel.WARNING
        assert 'no answer for an order' in title
        assert order_id in detail
        assert 'market BUY 0.01 BTCUSD' in detail

    def test_the_worker_is_stopped_before_anything_is_booked(self, stage):
        """What was queued behind the unanswered order stays unsent."""
        built = stage('acknowledging', False, _settings(order_wait_s=0.05))
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        built.buy_at_market()
        worker = built.executor.get_request_processor()._worker_thread

        built.stop()
        held.release()
        worker.join(timeout=5)

        assert len(built.venue.submits) == 1
        assert not worker.is_alive()

    def test_a_late_answer_changes_nothing_booked(self, stage):
        """An answer read after the order was given up finds no order, and invents no fill."""
        built = stage('fill_in_answer', True, _settings(order_wait_s=0.05))
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        worker = built.executor.get_request_processor()._worker_thread

        built.stop()
        held.release()
        worker.join(timeout=5)
        built.executor.finish_remaining_orders()

        assert [row.status for row in built.endings()] == [OrderStatus.UNACCOUNTED]
        _assert_untouched_book(built)

    @ACCOUNT_MODELS
    def test_a_last_read_settles_what_the_passes_did_not_reach(self, stage, spot_mode):
        """The venue filled it, and a bound of nothing left no pass to read that with."""
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.0))
        order_id = built.buy_at_market().order_id
        assert built.executor.get_request_processor().flush_outbox(5.0)
        logic = _HearingLogic()

        built.stop(logic)

        filled = built.endings()
        assert [row.status for row in filled] == [OrderStatus.EXECUTED]
        _assert_filled_book(built, filled[0])
        assert logic.heard == [f'filled:{order_id}', 'session_end']
        assert built.errors() == []
        built.alerts.send.assert_not_called()

    @ACCOUNT_MODELS
    def test_an_order_the_venue_keeps_working_is_cancelled_and_says_so(self, stage, spot_mode):
        """Its ending is known once the venue confirmed the cancel, so nothing is lost here."""
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.0))
        built.venue.fill_market_orders = False
        order_id = built.buy_at_market().order_id
        assert built.executor.get_request_processor().flush_outbox(5.0)
        logic = _HearingLogic()

        built.stop(logic)

        assert built.venue.cancels == built.venue.submits
        row = built.endings()[-1]
        assert (row.status, row.initiator, row.end_reason) == (
            OrderStatus.CANCELLED, OrderInitiator.FRAMEWORK, OrderEndReason.SESSION_END)
        assert logic.heard == [f'cancelled:{order_id}', 'session_end']
        _assert_untouched_book(built)
        assert built.errors() == []
        built.alerts.send.assert_not_called()

    @ACCOUNT_MODELS
    def test_what_executed_before_the_cancel_is_booked(self, stage, spot_mode):
        """The cancel ends the order, and a part it had executed is real money."""
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.0))
        built.venue.fill_market_orders = False
        built.buy_at_market()
        assert built.executor.get_request_processor().flush_outbox(5.0)
        built.venue.set_venue_status(
            built.venue.submits[0], 'PENDING', filled_lots=0.004, fill_price=_ASK)

        built.stop()

        assert [position.lots for position in built.executor.get_open_positions()] == [0.004]
        assert built.executor.get_order_history()[-1].status is not OrderStatus.UNACCOUNTED
        assert built.errors() == []

    @ACCOUNT_MODELS
    def test_a_venue_that_stopped_answering_makes_nothing_up(self, stage, spot_mode):
        """An unknown outcome stays unknown: not a fill, and never a refusal."""
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.0))
        built.venue.query_fault = ConnectionAttemptFailedError('read timed out', terminal=False)
        built.buy_at_market()
        assert built.executor.get_request_processor().flush_outbox(5.0)

        built.stop()

        assert [row.status for row in built.endings()] == [OrderStatus.UNACCOUNTED]
        _assert_untouched_book(built)
        assert built.errors()


class TestAFillTimeoutInsideTheWait:
    """It counts from the send, so it falls inside the wait; the wait's bound decides all the same."""

    @ACCOUNT_MODELS
    def test_the_bound_books_it_with_its_error_and_nothing_queued_goes_out(self, stage, spot_mode):
        built = stage('acknowledging', spot_mode, _settings(order_wait_s=0.3))
        held = built.hold_answers()
        first = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'
        second = built.buy_at_market().order_id
        processor = built.executor.get_request_processor()
        for order_id in (first, second):
            processor.get_order(order_id).timing.order_timeout_deadline_monotonic = (
                time.monotonic() + 0.05)
        # What the session does when its stop begins; this stage's loop holds a budget of its own
        built.executor.set_stop_budget(StopBudget(None, 15.0, time.monotonic()))

        built.stop(_HearingLogic())

        endings = built.endings()
        assert {row.order_id for row in endings} == {first, second}
        assert all(row.status is OrderStatus.UNACCOUNTED for row in endings)
        assert all(row.end_reason is OrderEndReason.SESSION_END for row in endings)
        assert len(built.errors()) == 2
        built.alerts.send.assert_called()
        held.release()
        time.sleep(0.2)
        assert len(built.venue.submits) == 1


class TestTheEndsThatWait:

    @pytest.mark.parametrize('handler, signal_number', [
        ('_handle_terminate', signal.SIGTERM),
        ('_handle_interrupt', signal.SIGINT),
    ], ids=['sigterm', 'first_ctrl_c'])
    def test_a_signal_waits_as_a_strategys_own_end_does(self, stage, handler, signal_number):
        """A first Ctrl+C sets the emergency shutdown mode, and it still asks for a normal end."""
        built = stage()
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        session = _session_around(built.executor)
        getattr(session, handler)(signal_number, None)

        built.loop().run()

        assert [row.status for row in built.endings()] == [OrderStatus.EXECUTED]
        assert built.errors() == []


class TestTheEndsThatDoNotWait:

    def test_a_hard_stop_goes_straight_on(self, stage):
        built = stage()
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        watched = MagicMock(wraps=built.executor.heartbeat)
        built.executor.heartbeat = watched
        logic = _HearingLogic()

        built.stop(logic, severity=SessionEndSeverity.EMERGENCY)

        watched.assert_not_called()
        assert logic.heard == ['session_end']

    def test_input_that_ran_out_goes_straight_on(self, stage):
        built = stage()
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        watched = MagicMock(wraps=built.executor.heartbeat)
        built.executor.heartbeat = watched
        logic = _HearingLogic()
        loop = built.loop(logic)

        loop.run()

        watched.assert_not_called()
        assert logic.heard == ['session_end']

    def test_a_hook_that_turns_it_into_an_emergency_ends_the_wait(self, stage):
        built = stage('acknowledging', False)
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        # A second order, unanswered, keeps the wait going once the first was heard
        logic = _HearingLogic(on_filled=lambda: (
            built.buy_at_market(),
            built.executor.request_session_end('hard stop', SessionEndSeverity.EMERGENCY)))
        started = time.monotonic()

        built.stop(logic)

        assert time.monotonic() - started < _PATIENT / 2
        assert logic.heard[-1] == 'session_end'

    @ACCOUNT_MODELS
    def test_what_they_leave_is_an_error_on_the_session_channel(self, stage, spot_mode):
        """It used to be a warning and exit 0, for an order the venue may have filled."""
        built = stage('acknowledging', spot_mode)
        held = built.hold_answers()
        order_id = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'

        built.executor.finish_remaining_orders()

        assert any(order_id in message for message in built.errors())
        row = built.endings()[-1]
        assert (row.status, row.end_reason) == (OrderStatus.UNACCOUNTED, OrderEndReason.SESSION_END)
        _assert_untouched_book(built)


class TestABoundaryCrossedDuringTheWait:

    @ACCOUNT_MODELS
    def test_the_fill_is_booked_into_the_new_day(self, stage, spot_mode):
        # The last event stands just before midnight, so the wait's own clock crosses it
        built = stage('acknowledging', spot_mode, at=_MIDNIGHT - timedelta(milliseconds=30))
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        threading.Timer(0.2, held.release).start()

        loop = built.stop()

        filled = built.endings()[-1]
        assert filled.status is OrderStatus.EXECUTED
        assert filled.execution_time >= _MIDNIGHT
        periods = loop.get_booking_periods()
        assert periods[0].closed_at == _MIDNIGHT
        # Before the midnight nothing executed, and the day it opened holds the fill
        assert periods[0].execution.orders_executed == 0
        assert periods[-1].execution.orders_executed == 1


class TestAHookThatSendsAnOrderDuringTheWait:

    @ACCOUNT_MODELS
    def test_the_same_bound_covers_it(self, stage, spot_mode):
        built = stage('acknowledging', spot_mode)
        held = built.hold_answers()
        first = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        sent: List[str] = []
        logic = _HearingLogic(on_filled=lambda: sent.append(built.buy_at_market().order_id))

        built.stop(logic)

        assert logic.heard == [f'filled:{first}', f'filled:{sent[0]}', 'session_end']
        assert [row.status for row in built.endings()] == [OrderStatus.EXECUTED] * 2
        assert built.executor.get_awaited_orders() == []


class TestTheRowsTheStopReads:

    def test_an_open_with_no_answer(self, stage):
        built = stage()
        held = built.hold_answers()
        order_id = built.buy_at_market().order_id
        assert held.entered.wait(5), 'the worker never reached the venue'

        row = built.executor.get_awaited_orders()[0]

        assert (row.order_id, row.action, row.order_type) == (
            order_id, OrderAction.OPEN, OrderType.MARKET)
        assert (row.direction, row.lots, row.symbol) == (OrderDirection.LONG, _LOTS, 'BTCUSD')
        assert row.sent_at_monotonic_s is not None
        assert row.sent_at_monotonic_s <= time.monotonic()
        assert row.waits_for == 'venue has not answered'
        assert row.describe() == 'market BUY 0.01 BTCUSD'

    def test_a_close_names_the_position_it_closes(self, stage):
        built = stage()
        position_id = built.open_a_position()
        held = built.hold_answers()
        built.executor.close_position(position_id)
        assert held.entered.wait(5), 'the worker never reached the venue'

        row = built.executor.get_awaited_orders()[0]

        assert (row.order_id, row.action, row.order_type) == (
            position_id, OrderAction.CLOSE, OrderType.MARKET)
        assert (row.direction, row.lots, row.symbol) == (OrderDirection.LONG, _LOTS, 'BTCUSD')
        assert row.describe() == 'market close of LONG 0.01 BTCUSD'

    def test_an_acknowledged_order_waits_for_its_fill(self, stage):
        built = stage()
        built.venue.fill_market_orders = False
        built.buy_at_market()
        assert built.executor.get_request_processor().flush_outbox(5.0)
        built.executor.get_request_processor().drain_inbox()

        row = built.executor.get_awaited_orders()[0]

        assert row.waits_for == 'the venue took it, no fill has shown yet'

    def test_a_resting_order_says_which_answer_it_waits_for(self, stage):
        built = stage()
        order_id = built.rest_a_limit().order_id
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        built.executor.get_request_processor().stop_worker(0.05)
        built.executor.cancel_limit_order(order_id)

        row = built.executor.get_awaited_orders()[0]

        assert (row.order_id, row.order_type) == (order_id, OrderType.LIMIT)
        assert row.waits_for == 'its cancel has not been answered'


@pytest.fixture(scope='module', params=[_SPOT_PROFILE, _MARGIN_PROFILE], ids=['spot', 'margin'])
def session_with_awaited_order(request):
    """
    One mock session whose logic sends an order and asks for the end on the same tick.

    Yields:
        The finished session and its result
    """
    trader, result = _run_session(request.param, _settings(), answer_after_s=0.2)
    yield trader, result
    remove_run_dir(trader._run_dir)


@pytest.fixture(scope='module', params=[_SPOT_PROFILE, _MARGIN_PROFILE], ids=['spot', 'margin'])
def session_run_out(request):
    """
    One mock session whose order stays unanswered past the bound.

    Yields:
        The finished session and its result
    """
    trader, result = _run_session(
        request.param, _settings(order_wait_s=0.1), answer_after_s=1.0, bot_suffix='ro')
    yield trader, result
    remove_run_dir(trader._run_dir)


@pytest.fixture(scope='module', params=[_SPOT_PROFILE, _MARGIN_PROFILE], ids=['spot', 'margin'])
def session_venue_gone(request):
    """
    One mock session whose venue reads fail, under a kill with no room for a read.

    Yields:
        The finished session and its result
    """
    trader, result = _run_session(
        request.param, _settings(supervisor_kill_after_s=1.0, reserve_for_records_s=0.0),
        answer_after_s=0.0, venue_stops_answering=True, bot_suffix='un')
    yield trader, result
    remove_run_dir(trader._run_dir)


class TestAWholeSessionWithAnAwaitedOrder:
    """A replayed session ends with its clock on its last tick, and its records say so."""

    def test_the_order_was_sent_on_the_way_out_and_booked(self, session_with_awaited_order):
        _, result = session_with_awaited_order
        statuses = [row.status for row in result.order_history
                    if row.status is not OrderStatus.PENDING]
        assert statuses == [OrderStatus.EXECUTED]
        assert len(result.open_positions) == 1

    def test_the_position_it_booked_is_valued(self, session_with_awaited_order):
        """
        No tick follows the fill, so nothing else marks the position. Unmarked, a margin account
        read it as worth nothing, and its final equity as if it held nothing.
        """
        trader, result = session_with_awaited_order
        position = result.open_positions[0]
        assert position.current_price > 0
        assert position.unrealized_pnl < 0
        assert trader._executor.portfolio.get_account_value() == pytest.approx(
            _SESSION_START[position.symbol] + position.unrealized_pnl)

    def test_no_phantom_period_up_to_today(self, session_with_awaited_order):
        _, result = session_with_awaited_order
        assert len(result.booking_periods) == 1
        assert result.booking_periods[0].closed_at - result.last_tick_time < timedelta(minutes=1)

    def test_the_fill_is_stamped_near_the_last_event(self, session_with_awaited_order):
        _, result = session_with_awaited_order
        filled = next(row for row in result.order_history if row.status is OrderStatus.EXECUTED)
        assert filled.execution_time - result.last_tick_time < timedelta(minutes=1)

    def test_the_logic_heard_the_fill_before_the_end(self, session_with_awaited_order):
        trader, _ = session_with_awaited_order
        assert trader._decision_logic.get_received_event_log() == [
            'order_filled', 'session_end']

    def test_it_is_a_clean_end(self, session_with_awaited_order):
        _, result = session_with_awaited_order
        assert logged_messages(result, LogLevel.ERROR) == []
        assert result.get_outcome() is RunOutcome.SUCCESS
        assert result.get_exit_code() == 0


class TestAWholeSessionWhoseBoundRunsOut:

    def test_it_exits_three_on_the_error(self, session_run_out):
        _, result = session_run_out
        assert any('UNACCOUNTED' in message for message in logged_messages(result, LogLevel.ERROR))
        assert result.get_outcome() is RunOutcome.FINISHED_WITH_ERRORS
        assert result.get_exit_code() == 3

    def test_the_order_is_on_the_record_and_no_position(self, session_run_out):
        _, result = session_run_out
        ending = [row for row in result.order_history
                  if row.status is not OrderStatus.PENDING]
        assert [row.status for row in ending] == [OrderStatus.UNACCOUNTED]
        assert ending[0].end_reason is OrderEndReason.SESSION_END
        assert result.open_positions == []

    def test_the_logic_heard_it_before_the_end(self, session_run_out):
        trader, _ = session_run_out
        assert trader._decision_logic.get_received_event_log() == [
            'order_unaccounted', 'session_end']


class TestTheCleanup:

    def test_the_worker_stops_first_and_a_queued_submit_does_not_go_out(self, stage):
        built = stage()
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        built.buy_at_market()
        worker = built.executor.get_request_processor()._worker_thread

        built.executor.finish_remaining_orders()
        held.release()
        worker.join(timeout=5)

        assert len(built.venue.submits) == 1
        assert [row.status for row in built.endings()] == [OrderStatus.UNACCOUNTED] * 2
        assert len(built.errors()) == 2

    @ACCOUNT_MODELS
    def test_an_answer_that_came_is_read_before_anything_is_booked(self, stage, spot_mode):
        """A crash leaves no wait, and the fill the session already holds is still booked."""
        built = stage('fill_in_answer', spot_mode)
        built.buy_at_market()
        assert built.executor.get_request_processor().flush_outbox(5.0)

        built.executor.finish_remaining_orders()

        filled = built.endings()
        assert [row.status for row in filled] == [OrderStatus.EXECUTED]
        _assert_filled_book(built, filled[0])
        assert built.errors() == []

    def test_a_read_that_fails_still_cancels_the_resting_orders(self, stage):
        built = stage()
        built.rest_a_limit()
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        real_drain = built.executor.get_request_processor().drain_inbox
        built.executor.get_request_processor().drain_inbox = MagicMock(
            side_effect=RuntimeError('a hook failed'))

        built.executor.finish_remaining_orders()

        built.executor.get_request_processor().drain_inbox = real_drain
        assert built.venue.cancels == built.venue.submits
        assert any('reading the last answers failed' in message for message in built.errors())


class _RecordingNote:
    """A carry-over store that keeps what each write says the open book is."""

    def __init__(self) -> None:
        self.books: List[Optional[List[Any]]] = []

    def save(self, **fields: Any) -> None:
        self.books.append(fields['open_positions'])


class TestTheNoteAfterTheCleanup:

    @ACCOUNT_MODELS
    def test_a_fill_found_there_reaches_it_at_spot(self, stage, monkeypatch, spot_mode):
        """
        The cancel is refused because the order has filled, and the read after it books the fill.
        The note written before the cleanup is flat. Margin writes no book into it (#209), which
        is what the other half of this pins: that fill does not reach the next session.
        """
        built = stage('acknowledging', spot_mode)
        order_id = built.rest_a_limit().order_id
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        broker_ref = next(order.broker_ref for order in built.executor.get_active_orders()
                          if order.pending_order_id == order_id)
        built.venue.set_venue_status(broker_ref, 'FILLED', filled_lots=_LOTS, fill_price=50000.0)
        note = _RecordingNote()
        session = _session_around(built.executor)
        session._cold_start = SimpleNamespace(
            persist=True, store=note, persist_venue_claims=True, keys_in_use=None)
        monkeypatch.setattr(session, '_collect_results', lambda ticks, clipped: AutoTraderResult())
        monkeypatch.setattr(session, '_generate_reports', lambda result: None)

        session._shutdown(0, 0)

        assert len(built.executor.get_open_positions()) == 1, 'the cleanup booked no fill'
        if spot_mode:
            assert note.books[0] == []
            assert [position.lots for position in note.books[-1]] == [_LOTS]
        else:
            assert note.books == [None, None]


class TestTheBudget:

    def _no_time_left(self) -> StopBudget:
        return StopBudget(0.01, 0.0, time.monotonic() - 1.0)

    def test_a_declared_kill_shortens_the_wait(self, stage):
        settings = _settings(
            order_wait_s=_PATIENT, supervisor_kill_after_s=0.2, reserve_for_records_s=0.0,
            tick_thread_join_s=0.0, signal_transport_join_s=0.0, display_join_s=0.0,
            request_worker_join_s=0.05, alert_flush_s=0.0)
        built = stage('acknowledging', False, settings)
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        started = time.monotonic()

        built.stop()

        assert time.monotonic() - started < _PATIENT / 2
        assert built.endings()[-1].status is OrderStatus.UNACCOUNTED
        assert 'kill in about' in built.alerts.send.call_args.args[3]

    def test_a_kill_with_no_room_left_gives_up_at_once_without_a_pass(self, stage):
        settings = _settings(supervisor_kill_after_s=1.0, reserve_for_records_s=2.0)
        built = stage('acknowledging', False, settings)
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        watched = MagicMock(wraps=built.executor.heartbeat)
        built.executor.heartbeat = watched

        built.stop()

        watched.assert_not_called()
        assert built.endings()[-1].status is OrderStatus.UNACCOUNTED

    def test_the_give_up_makes_no_call_with_no_time_left(self, stage):
        built = stage('acknowledging', False, _settings(order_wait_s=0.0))
        built.buy_at_market()
        assert built.executor.get_request_processor().flush_outbox(5.0)
        built.executor.set_stop_budget(self._no_time_left())

        built.stop()

        assert built.venue.queries == []
        assert built.venue.cancels == []
        assert built.endings()[-1].status is OrderStatus.UNACCOUNTED
        assert any('no time left' in message for message in built.errors())

    def test_no_cancel_goes_out_with_no_time_left(self, stage):
        built = stage()
        built.rest_a_limit()
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        built.executor.set_stop_budget(self._no_time_left())

        built.executor.finish_remaining_orders()

        assert built.venue.cancels == []
        assert built.endings()[-1].status is OrderStatus.UNACCOUNTED
        assert any('no cancel was sent' in message and 'no time left' in message
                   for message in built.errors())

    def test_a_cancel_on_its_way_is_not_read_with_no_time_left(self, stage):
        built = stage()
        order_id = built.rest_a_limit().order_id
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        built.executor.get_request_processor().stop_worker(0.05)
        built.executor.cancel_limit_order(order_id)
        built.executor.set_stop_budget(self._no_time_left())

        built.executor.finish_remaining_orders(cancel_orders=False)

        assert built.venue.queries == []

    def test_a_refused_cancel_is_not_read_when_the_time_ran_out_meanwhile(self, stage):
        built = stage()
        order_id = built.rest_a_limit().order_id
        MockOrderExecution().await_submit_confirmation(built.executor)
        built.executor.get_request_processor().drain_inbox()
        broker_ref = next(order.broker_ref for order in built.executor.get_active_orders()
                          if order.pending_order_id == order_id)
        built.venue.set_venue_status(broker_ref, 'FILLED', filled_lots=_LOTS, fill_price=50000.0)
        budget = MagicMock(spec=StopBudget)
        budget.has_time_for.side_effect = [True, False, False]
        built.executor.set_stop_budget(budget)

        built.executor.finish_remaining_orders()

        assert built.venue.cancels == [broker_ref]
        assert built.venue.queries == []
        assert built.endings()[-1].status is OrderStatus.UNACCOUNTED
        assert any('no read followed' in message for message in built.errors())

    def test_a_stop_counts_from_the_signal(self, stage):
        built = stage()
        session = _session_around(built.executor)
        session._config.shutdown = _settings(supervisor_kill_after_s=80.0)
        session._handle_terminate(signal.SIGTERM, None)
        asked_at_s = time.monotonic() - 40.0
        session._stop_asked_monotonic_s = asked_at_s

        session._handle_terminate(signal.SIGTERM, None)
        budget = session._open_stop_budget()

        assert session._stop_asked_monotonic_s == asked_at_s
        assert budget.elapsed_s() >= 40.0
        assert budget.until_kill_s() <= 40.0
        assert session._open_stop_budget() is budget
        assert built.executor._stop_budget is budget

    def test_a_stop_nobody_signalled_counts_from_its_first_step(self):
        session = AutotraderMain(AutoTraderConfig(
            profile_name='stop_waits', symbol='BTCUSD', broker_type='kraken_spot',
            shutdown=_settings(supervisor_kill_after_s=80.0)))

        budget = session._open_stop_budget()

        assert budget.elapsed_s() < 5.0


class TestTheEndReadUnderTheBudget:

    # One attempt costs 1 s, the ladder's three and the waits between them up to 4.5 s
    _CONNECTION = ConnectionPolicy(
        attempt_budget=3, request_timeout_s=1.0, initial_delay_s=0.5, max_delay_s=1.0,
        jitter=False, on_give_up=GiveUpAction.ABORT)

    def _account_reads(self, built: _Stage, fault: Optional[Exception] = None) -> List[str]:
        """
        Count the venue's account reads, and make them fail when a fault is given.

        Args:
            built: The stage
            fault: What each read raises, or None to answer

        Returns:
            The list the reads are written to
        """
        asked: List[str] = []
        for name in ('get_broker_orders', 'get_broker_balances', 'get_broker_positions'):
            def read(name: str = name, real: Callable[[], Any] = getattr(built.venue, name)) -> Any:
                asked.append(name)
                if fault is not None:
                    raise fault
                return real()
            setattr(built.venue, name, read)
        return asked

    @staticmethod
    def _parts(spot_mode: bool) -> List[BrokerTruthPart]:
        parts = [BrokerTruthPart.VENUE_ORDERS, BrokerTruthPart.VENUE_BALANCES]
        return parts if spot_mode else parts + [BrokerTruthPart.VENUE_POSITIONS]

    @ACCOUNT_MODELS
    def test_with_no_time_left_each_part_stays_unread_and_named(self, stage, spot_mode):
        built = stage('acknowledging', spot_mode, connection=self._CONNECTION)
        asked = self._account_reads(built)
        built.executor.set_stop_budget(StopBudget(0.01, 0.0, time.monotonic() - 1.0))

        built.executor.record_session_truth(BrokerTruthReadReason.SESSION_END)

        assert asked == []
        for part in self._parts(spot_mode):
            assert any(part.value in message and 'left unread' in message
                       for message in built.errors())

    @ACCOUNT_MODELS
    def test_with_time_for_one_attempt_each_part_gets_one(self, stage, spot_mode):
        built = stage('acknowledging', spot_mode, connection=self._CONNECTION)
        asked = self._account_reads(
            built, ConnectionAttemptFailedError('read timed out', terminal=False))
        # Room for one attempt and not for the ladder
        built.executor.set_stop_budget(StopBudget(4.0, 0.0, time.monotonic()))

        snapshot = built.executor.read_broker_truth(built.executor._stop_budget)

        assert snapshot.unread_parts == self._parts(spot_mode)
        assert len(asked) == len(self._parts(spot_mode))
        assert len([message for message in built.errors() if 'broker_rest' in message]) == len(asked)

    def test_with_time_for_the_ladder_a_healthy_venue_answers_each_part(self, stage):
        built = stage('acknowledging', True, connection=self._CONNECTION)
        asked = self._account_reads(built)
        built.executor.set_stop_budget(StopBudget(100.0, 0.0, time.monotonic()))

        snapshot = built.executor.read_broker_truth(built.executor._stop_budget)

        assert snapshot.unread_parts == []
        assert len(asked) == 2
        assert built.errors() == []

    def test_the_start_read_is_untouched(self, stage):
        built = stage('acknowledging', True, connection=self._CONNECTION)
        asked = self._account_reads(built)
        built.executor.set_stop_budget(StopBudget(0.01, 0.0, time.monotonic() - 1.0))

        built.executor.record_session_truth(BrokerTruthReadReason.SESSION_START)

        assert len(asked) == 2
        assert built.errors() == []


class TestAWholeSessionWithAVenueThatStoppedAnswering:

    def test_the_parts_stay_unread_and_named(self, session_venue_gone):
        _, result = session_venue_gone
        parts = result.broker_truth[-1].snapshot.unread_parts
        assert BrokerTruthPart.VENUE_ORDERS in parts
        assert BrokerTruthPart.VENUE_BALANCES in parts
        errors = logged_messages(result, LogLevel.ERROR)
        for part in parts:
            assert any(part.value in message and 'left unread' in message for message in errors)

    def test_the_session_is_graded_on_it(self, session_venue_gone):
        _, result = session_venue_gone
        assert result.get_exit_code() == 3

    def test_the_ledger_row_is_written(self, session_venue_gone):
        trader, _ = session_venue_gone
        rows = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path())).read_rows()
        assert trader._run_id in {row.run_id for row in rows}


def _session_around(executor: LiveTradeExecutor) -> AutotraderMain:
    """
    A session reduced to what its signal handlers and its shutdown touch.

    Args:
        executor: The executor it stops

    Returns:
        The session
    """
    session = AutotraderMain(AutoTraderConfig(
        profile_name='stop_waits', symbol='BTCUSD', broker_type='kraken_spot'))
    session._global_logger = RecordingLogger()
    session._session_logger = RecordingLogger()
    session._executor = executor
    return session


def _run_session(profile: str, settings: ShutdownDefaults, answer_after_s: float,
                 venue_stops_answering: bool = False, bot_suffix: str = 'ok') -> Any:
    """
    One whole mock session, with the venue's answers delayed from outside.

    Args:
        profile: The profile
        settings: The stop settings
        answer_after_s: How long the venue takes to answer a submit, after it acted
        venue_stops_answering: Whether the venue's account reads fail when the stop begins
        bot_suffix: Keeps this session's carry-over apart from the other scenarios'

    Returns:
        The session and its result
    """
    config = load_autotrader_config(profile)
    config.shutdown = settings
    config.bot_id = f'{config.bot_id}{bot_suffix}'
    trader = AutotraderMain(config)
    real_submit = MockBrokerAdapter.do_request_submit
    real_shutdown = AutotraderMain._shutdown

    def slow_submit(adapter: MockBrokerAdapter, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = real_submit(adapter, payload)
        time.sleep(answer_after_s)
        return raw

    def shutdown_with_venue_gone(instance: AutotraderMain, *args: Any) -> AutoTraderResult:
        adapter = instance._executor.broker.adapter
        for operation in ('openorders', 'balances', 'positions'):
            adapter.set_transport_fault(operation, 'read timed out', terminal=False)
        return real_shutdown(instance, *args)

    console = io.StringIO()
    with pytest.MonkeyPatch.context() as patch, contextlib.redirect_stdout(console):
        patch.setattr(MockBrokerAdapter, 'do_request_submit', slow_submit)
        if venue_stops_answering:
            patch.setattr(AutotraderMain, '_shutdown', shutdown_with_venue_gone)
        result = trader.run()
    return trader, result


class TestWhatTheWaitCameTo:
    """The closing alert's line, read off the order records of a real executor in both models."""

    @staticmethod
    def _outcome(built: _Stage, holder: ShutdownStatusHolder) -> StopWaitOutcome:
        """
        What the wait of a finished stop came to, as the session's result derives it.

        Args:
            built: The stage
            holder: What the stop wrote to

        Returns:
            The outcome
        """
        status = holder.get()
        return StopWaitOutcome.over_records(
            status.order_wait_ran_s, status.orders_waited_for, built.executor.get_order_history())

    @ACCOUNT_MODELS
    def test_an_open_that_filled(self, stage, spot_mode):
        built = stage(spot_mode=spot_mode)
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        holder = ShutdownStatusHolder(None)

        built.stop(shutdown_status=holder)

        outcome = self._outcome(built, holder)
        assert (outcome.orders_executed, outcome.orders_waited_for()) == (1, 1)
        assert outcome.waited_s > 0.0

    @ACCOUNT_MODELS
    def test_a_close_that_filled(self, stage, spot_mode):
        built = stage(spot_mode=spot_mode)
        position_id = built.open_a_position()
        held = built.hold_answers()
        built.executor.close_position(position_id)
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        holder = ShutdownStatusHolder(None)

        built.stop(shutdown_status=holder)

        outcome = self._outcome(built, holder)
        assert (outcome.orders_executed, outcome.orders_waited_for()) == (1, 1)

    @ACCOUNT_MODELS
    def test_a_bound_that_ran_out(self, stage, spot_mode):
        built = stage(spot_mode=spot_mode, settings=_settings(order_wait_s=0.05))
        held = built.hold_answers()
        built.buy_at_market()
        assert held.entered.wait(5), 'the worker never reached the venue'
        holder = ShutdownStatusHolder(None)

        built.stop(shutdown_status=holder)
        held.release()

        outcome = self._outcome(built, holder)
        assert (outcome.orders_unaccounted, outcome.orders_executed) == (1, 0)

    def test_a_resting_order_the_venue_took_stays_open(self, stage):
        built = stage()
        held = built.hold_answers()
        built.rest_a_limit()
        assert held.entered.wait(5), 'the worker never reached the venue'
        held.release_during_the_wait()
        holder = ShutdownStatusHolder(None)

        built.stop(shutdown_status=holder)

        outcome = self._outcome(built, holder)
        assert (outcome.orders_still_open, outcome.orders_waited_for()) == (1, 1)

    def test_nothing_awaited_writes_no_status(self, stage):
        built = stage()
        holder = ShutdownStatusHolder(None)

        built.stop(shutdown_status=holder)

        assert holder.get() is None


class TestWhatTheStatusAsksTheSession:

    def test_the_count_is_what_the_cleanup_will_cancel(self, stage):
        built = stage()
        built.rest_a_limit()
        built.stop()
        session = _session_around(built.executor)

        # A session that never resolved its policy is the strict case
        assert session._count_orders_to_cancel() == 1
        session._session_end = SimpleNamespace(orders='cancel', positions='leave')
        assert session._count_orders_to_cancel() == 1
        session._session_end = SimpleNamespace(orders='leave', positions='leave')
        assert session._count_orders_to_cancel() == 0

    def test_no_executor_no_count(self):
        session = AutotraderMain(AutoTraderConfig(
            profile_name='stop_waits', symbol='BTCUSD', broker_type='kraken_spot'))

        assert session._count_orders_to_cancel() is None

    @pytest.mark.parametrize('severity, expected', [
        (SessionEndSeverity.NORMAL, "strategy's request"),
        (SessionEndSeverity.EMERGENCY, 'hard stop'),
    ], ids=['normal', 'emergency'])
    def test_a_request_names_what_asked(self, stage, severity, expected):
        built = stage()
        built.executor.request_session_end('the logic is done', severity)
        session = _session_around(built.executor)

        session._open_stop_budget()

        assert session._shutdown_status.get().asked_by == expected

    def test_a_crash_names_what_asked(self, stage):
        session = _session_around(stage().executor)
        session._emergency_reason = 'boom'

        assert session._what_asked_for_the_stop() == 'abort'

    @pytest.mark.parametrize('loop_started, expected', [
        (True, 'end of input'), (False, 'refused start'),
    ], ids=['input_ran_out', 'never_started'])
    def test_no_request_names_how_far_it_got(self, stage, loop_started, expected):
        session = _session_around(stage().executor)
        session._tick_loop_started = loop_started

        assert session._what_asked_for_the_stop() == expected

    def test_a_signal_before_the_first_step_keeps_its_word(self, stage):
        built = stage()
        built.executor.request_session_end('the logic is done', SessionEndSeverity.NORMAL)
        session = _session_around(built.executor)
        session._handle_terminate(signal.SIGTERM, None)

        session._open_stop_budget()

        assert session._shutdown_status.get().asked_by == 'SIGTERM'
