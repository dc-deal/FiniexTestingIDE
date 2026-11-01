"""
A session's stop settings, from the profile to what the session does with them (#630).

One mock session, with every setting at a value of its own. The threads a replayed session has
already finished with by the time it stops are stood in for at the moment the stop begins, so
each wait of the stop is read where it is spent. The session's own check at the start runs for real.
"""

import contextlib
import io
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.operator_alerts.loop_progress import LoopProgress
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.config_types.autotrader_defaults_config_types import ShutdownDefaults
from python.framework.types.log_level import LogLevel
from python.framework.types.run_outcome_types import RunOutcome
from python.system.ui.autotrader_live_display import AutoTraderLiveDisplay
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

_PROFILE = 'configs/autotrader_profiles/mock/session_stop_test.json'

_SETTINGS = ShutdownDefaults(
    order_wait_s=1.5,
    tick_thread_join_s=2.5,
    signal_transport_join_s=0.5,
    display_join_s=0.75,
    request_worker_join_s=1.25,
    alert_flush_s=30.0,
    # Room for one call to the venue at the end, so the end read is asked and the grade stays
    supervisor_kill_after_s=20.0,
    reserve_for_records_s=0.25,
)
# What those settings can spend at most, far beyond the kill they declare
_SETTINGS_NEED = '36.75 s'


class _RecordingThread:
    """A thread the stop still has to wait for, recording how long it may."""

    def __init__(self) -> None:
        self.join_timeouts: List[float] = []

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float) -> None:
        self.join_timeouts.append(timeout)


class _RecordingTransport:
    """A signal transport for a session that has none: gone again once it has been stopped."""

    def __init__(self, trader: AutotraderMain) -> None:
        self.join_timeouts: List[float] = []
        self._trader = trader

    def stop(self, join_timeout_s: float) -> None:
        self.join_timeouts.append(join_timeout_s)
        self._trader._signal_transport = None


@dataclass
class _StoppedSession:
    """
    One finished mock session, and what watched its stop.

    Args:
        result: The session's result
        run_dir: Its run directory, removed after the module
        console: What it printed
        tick_thread: What stood in for the tick thread
        transport: What stood in for the signal transport
        stop_worker: The request processor's stop, watched
        alerts: The operator alerts, watched
    """
    result: AutoTraderResult
    run_dir: Path
    console: str
    tick_thread: _RecordingThread
    transport: _RecordingTransport
    stop_worker: Optional[MagicMock]
    alerts: Optional[MagicMock]


@pytest.fixture(scope='module')
def stopped_session():
    """
    One mock session whose stop is watched from the moment it begins.

    Yields:
        The finished session
    """
    config = load_autotrader_config(_PROFILE)
    config.shutdown = _SETTINGS
    trader = AutotraderMain(config)
    tick_thread = _RecordingThread()
    transport = _RecordingTransport(trader)
    watched = {}
    original_shutdown = AutotraderMain._shutdown

    def watching_shutdown(instance, *args):
        instance._tick_thread = tick_thread
        instance._signal_transport = transport
        processor = instance._executor._request_processor
        watched['stop_worker'] = MagicMock(wraps=processor.stop_worker)
        processor.stop_worker = watched['stop_worker']
        watched['alerts'] = MagicMock(wraps=instance._operator_alerts)
        instance._operator_alerts = watched['alerts']
        return original_shutdown(instance, *args)

    console = io.StringIO()
    with pytest.MonkeyPatch.context() as patch, contextlib.redirect_stdout(console):
        patch.setattr(AutotraderMain, '_shutdown', watching_shutdown)
        result = trader.run()
    yield _StoppedSession(
        result, trader._run_dir, console.getvalue(), tick_thread, transport,
        watched.get('stop_worker'), watched.get('alerts'))
    remove_run_dir(trader._run_dir)


class TestTheCheckInASession:

    def test_the_finding_stands_on_the_sessions_record(self, stopped_session):
        findings = [finding for validation in stopped_session.result.session_validation_result
                    for finding in validation.findings if finding.check.startswith('shutdown_')]
        # The kill is the one question a mock session is asked: it has no supervisor to declare
        # and no poll to miss, whatever its order wait says
        assert [finding.check for finding in findings] == ['shutdown_waits_beyond_kill']
        assert findings[0].scope == 'session_stop_test'
        assert _SETTINGS_NEED in findings[0].message

    def test_it_is_said_in_the_session_log_and_on_the_console(self, stopped_session):
        session_log = (stopped_session.run_dir / 'autotrader_session.log').read_text()
        assert _SETTINGS_NEED in session_log
        assert _SETTINGS_NEED in stopped_session.console

    def test_it_enters_the_log_pot_as_no_warning(self, stopped_session):
        warnings = logged_messages(stopped_session.result, LogLevel.WARNING)
        assert not any(_SETTINGS_NEED in message for message in warnings)

    def test_a_stop_with_nothing_to_wait_for_leaves_no_outcome(self, stopped_session):
        assert stopped_session.result.stop_wait is None

    def test_it_refuses_nothing_and_grades_nothing(self, stopped_session):
        assert stopped_session.result.get_outcome() is RunOutcome.SUCCESS
        assert stopped_session.result.get_exit_code() == 0


class TestTheCheckOnALiveStart:

    @pytest.mark.parametrize('attended, checks', [
        (False, ['shutdown_kill_undeclared']),
        (True, []),
    ])
    def test_the_session_asks_with_its_adapter_and_its_broker(self, attended, checks):
        # Nothing starts: the check is asked directly, so no venue is reached
        config = load_autotrader_config(_PROFILE)
        config.adapter_type = 'live'
        trader = AutotraderMain(config)
        trader._session_logger = MagicMock()
        trader._check_stop_budget(attended)
        assert [finding.check for finding in trader._startup_findings] == checks


class TestEachWaitTakesItsSetting:

    def test_the_tick_thread(self, stopped_session):
        assert stopped_session.tick_thread.join_timeouts == [_SETTINGS.tick_thread_join_s]

    def test_the_signal_transport(self, stopped_session):
        assert stopped_session.transport.join_timeouts == [_SETTINGS.signal_transport_join_s]

    def test_the_request_worker(self, stopped_session):
        stopped_session.stop_worker.assert_called_once_with(_SETTINGS.request_worker_join_s)

    def test_the_alert_flush(self, stopped_session):
        stopped_session.alerts.finish.assert_called_once()
        finished_with = stopped_session.alerts.finish.call_args.kwargs
        assert finished_with['flush_timeout_s'] == _SETTINGS.alert_flush_s

    def test_the_display(self):
        config = AutoTraderConfig(shutdown=_SETTINGS)
        display = AutoTraderLiveDisplay(queue.Queue(), MagicMock(), config)
        thread = _RecordingThread()
        display._thread = thread
        display.stop()
        assert thread.join_timeouts == [_SETTINGS.display_join_s]

    def test_the_alert_dispatcher_is_handed_what_the_flush_was_given(self):
        dispatcher = MagicMock()
        OperatorAlerts(dispatcher, None, LoopProgress()).finish(emergency=False, flush_timeout_s=7.5)
        dispatcher.stop.assert_called_once_with(7.5)
