"""
Signal Session End — a Ctrl+C or a SIGTERM says why the session ends (#348)

An operator's Ctrl+C and a supervisor's SIGTERM used to stop the loop and nothing more, so the
decision logic's session-end event and the closing message both read "tick source exhausted" —
the answer for a replay that ran out of data. The signal now asks for the end with its own
reason, keeps a request that was already made (a hard stop's EMERGENCY decides the shutdown
mode), reaches an executor built after the signal came, and an end asked for before the loop's
first pass lets no decision run at all.
"""

import queue
import signal
from datetime import datetime, timezone
from unittest.mock import MagicMock

from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.live_clipping_monitor import LiveClippingMonitor
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter, MockExecutionMode
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.parity_fixtures import make_flat_btcusd_ticks


class _Executor:
    """The executor's session-end request, as the abstract executor keeps it."""

    def __init__(self):
        self.requested = False
        self.reason = ''
        self.severity = SessionEndSeverity.NORMAL

    def request_session_end(self, reason: str,
                            severity: SessionEndSeverity = SessionEndSeverity.NORMAL) -> None:
        self.requested, self.reason, self.severity = True, reason, severity

    def is_session_end_requested(self) -> bool:
        return self.requested

    def get_session_end_severity(self) -> SessionEndSeverity:
        return self.severity


def _session(executor=None) -> AutotraderMain:
    """
    A session reduced to what the signal handlers read.

    Built through `__new__`: the handlers touch only the attributes set here, and a real
    session would bring a broker, a tick source and a decision logic without testing any of
    them.
    """
    session = AutotraderMain.__new__(AutotraderMain)
    session._first_interrupt_time = 0.0
    session._signal_end_reason = ''
    session._shutdown_mode = 'normal'
    session._tick_loop = None
    session._global_logger = None
    session._emergency_reason = None
    session._executor = executor
    return session


class TestTheSignalNamesTheEnd:

    def test_a_sigterm_asks_for_a_normal_end_with_its_reason(self):
        executor = _Executor()
        _session(executor)._handle_terminate(signal.SIGTERM, None)

        assert executor.requested
        assert executor.reason == 'termination signal (SIGTERM)'
        assert executor.severity is SessionEndSeverity.NORMAL

    def test_a_ctrl_c_asks_for_a_normal_end_and_keeps_its_shutdown_mode(self):
        executor = _Executor()
        session = _session(executor)
        session._handle_interrupt(signal.SIGINT, None)

        assert executor.reason == 'operator interrupt (Ctrl+C)'
        assert executor.severity is SessionEndSeverity.NORMAL
        assert session._shutdown_mode == 'emergency'

    def test_an_earlier_request_is_kept(self):
        """A hard stop asked first: its EMERGENCY decides the shutdown mode, so it stays."""
        executor = _Executor()
        executor.request_session_end('emergency flatten', SessionEndSeverity.EMERGENCY)
        _session(executor)._handle_terminate(signal.SIGTERM, None)

        assert executor.reason == 'emergency flatten'
        assert executor.severity is SessionEndSeverity.EMERGENCY

    def test_a_sigterm_never_lowers_an_emergency(self):
        """A supervisor's stop while a hard stop or a crash shuts down keeps the failed grade."""
        session = _session(_Executor())
        session._shutdown_mode = 'emergency'
        session._handle_terminate(signal.SIGTERM, None)

        assert session._shutdown_mode == 'emergency'

    def test_an_interrupt_forgives_only_its_own_emergency(self):
        """
        A Ctrl+C is not a failure — unless a hard stop or a crash came first. Pressed during the
        shutdown of either, it used to turn a failed session into a successful one.
        """
        executor = _Executor()
        session = _session(executor)
        session._handle_interrupt(signal.SIGINT, None)
        assert session._interrupted_by_the_operator()

        executor.request_session_end('emergency flatten', SessionEndSeverity.EMERGENCY)
        assert not session._interrupted_by_the_operator()

        crashed = _session(_Executor())
        crashed._handle_interrupt(signal.SIGINT, None)
        crashed._emergency_reason = 'RuntimeError: boom'
        assert not crashed._interrupted_by_the_operator()

    def test_a_signal_before_the_executor_reaches_it_once_it_exists(self):
        """The start is seconds long; a Ctrl+C in it is remembered and handed over."""
        session = _session(executor=None)
        session._handle_interrupt(signal.SIGINT, None)
        executor = _Executor()
        session._executor = executor
        session._request_signal_end()

        assert executor.reason == 'operator interrupt (Ctrl+C)'


def test_an_end_asked_for_before_the_first_pass_lets_no_decision_run():
    """A stop that came while the session started makes no pass — and no order."""
    logger = ScenarioLogger(
        scenario_set_name='signal_session_end', scenario_name='no_first_pass',
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)))
    tick_queue: queue.Queue = queue.Queue()
    for tick in make_flat_btcusd_ticks(count=5):
        tick_queue.put(tick)
    tick_queue.put(None)

    executor = MagicMock()
    executor.is_session_end_requested.return_value = True
    executor.get_session_end_reason.return_value = 'operator interrupt (Ctrl+C)'
    executor.get_current_time_if_set.return_value = None
    decision_logic = MagicMock()
    loop = AutotraderTickLoop(
        config=AutoTraderConfig(
            profile_name='no_first_pass', symbol='BTCUSD', broker_type='kraken_spot',
            adapter_type='mock'),
        tick_queue=tick_queue,
        tick_source=MagicMock(),
        executor=executor,
        bar_controller=BarRenderingController(logger=logger),
        worker_orchestrator=MagicMock(),
        decision_logic=decision_logic,
        clipping_monitor=LiveClippingMonitor(),
        logger=logger,
        trading_model=TradingModel.SPOT,
        run_dir=None,
        display_queue=None,
        dry_run=True,
    )

    assert loop.run() == (0, 0)
    decision_logic.execute_decision.assert_not_called()
    executor.on_tick.assert_not_called()


def test_a_stop_during_the_start_still_ends_cleanly():
    """
    No pass means nothing set the canonical clock, and the shutdown reads it — the disturbance
    episodes the result collects raised, so a Ctrl+C during the start crashed the shutdown.
    """
    logger = ScenarioLogger(
        scenario_set_name='signal_session_end', scenario_name='clock_after_no_pass',
        run_timestamp=datetime.now(tz=timezone.utc),
        run_id=mint_run_id(datetime.now(timezone.utc)))
    executor = LiveTradeExecutor(
        broker_config=BrokerConfig(
            BrokerType.KRAKEN_SPOT, MockBrokerAdapter(mode=MockExecutionMode.INSTANT_FILL)),
        initial_balance=10000.0, account_currency='USD', logger=logger)
    executor.request_session_end('operator interrupt (Ctrl+C)')
    tick_queue: queue.Queue = queue.Queue()
    tick_queue.put(None)
    loop = AutotraderTickLoop(
        config=AutoTraderConfig(
            profile_name='clock_after_no_pass', symbol='BTCUSD', broker_type='kraken_spot',
            adapter_type='mock'),
        tick_queue=tick_queue,
        tick_source=MagicMock(),
        executor=executor,
        bar_controller=BarRenderingController(logger=logger),
        worker_orchestrator=MagicMock(),
        decision_logic=MagicMock(),
        clipping_monitor=LiveClippingMonitor(),
        logger=logger,
        trading_model=TradingModel.MARGIN,
        run_dir=None,
        display_queue=None,
        dry_run=True,
    )

    assert loop.run() == (0, 0)
    assert executor.get_current_time_if_set() is not None
    assert loop.get_disturbance_episodes() == []
