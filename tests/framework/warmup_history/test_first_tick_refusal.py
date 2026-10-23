"""
First-Tick Refusal — a session that cannot close its warmup seam says it refused (#588).

A live session reads its warmup at boot and reads it again at the first tick where a period ended in
between. When the venue has not committed that period yet, or cannot be read, the session stops
before the workers and the decision run on that tick. That is a refusal, not a crash, and a restart
a few seconds later closes the seam. It is not a promise that nothing reached the venue: the seam
closes where the tick's bars are rendered, after the executor's stop and take-profit check of the
same tick — so the block names what the session had sent by then. These tests hold the console, the
session log and the result to saying exactly that — while the grading stays FAILED, so whoever
relaunches sessions relaunches it.
"""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.operator_alerts.operator_alerts_setup import (
    disabled_operator_alerts,
)
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.process.tick_pipeline_core import render_bars_for_tick
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
)
from tests.shared.recording_logger import RecordingLogger

REFUSAL = WarmupHistoryRefusedError(
    'ETHUSD M1: the venue has not yet committed the period before the first tick '
    '(2026-10-13T07:30:00.400000+00:00)')


def _session(tick_loop_started: bool, executor=None) -> AutotraderMain:
    """A session reduced to what the abort report reads and sets; by default it sent nothing."""
    session = AutotraderMain.__new__(AutotraderMain)
    session._tick_loop_started = tick_loop_started
    session._executor = executor or SimpleNamespace(
        get_execution_stats=lambda: SimpleNamespace(orders_submitted=0, sl_tp_triggered=0))
    session._session_logger = RecordingLogger()
    session._global_logger = RecordingLogger()
    session._refused_at_first_tick = False
    session._operator_alerts = disabled_operator_alerts()
    session._abort_alerted = False
    session._emergency_reason = None
    session._emergency_error_type = None
    session._shutdown_mode = 'normal'
    return session


class TestTheAbortIsNamedForWhatItIs:

    def test_a_seam_refusal_in_the_tick_loop_is_a_refusal(self, capsys):
        session = _session(tick_loop_started=True)

        session._report_abort(REFUSAL)

        block = capsys.readouterr().out.strip().splitlines()
        assert block[1] == '  ⛔ REFUSED AT THE FIRST TICK — its workers and decision did not run'
        assert 'not yet committed' in block[2]
        assert block[3] == ('  No order was submitted in this session and no stop or take-profit '
                            'fired.')
        assert 'RUNTIME ERROR' not in '\n'.join(block)
        assert session._refused_at_first_tick
        assert session._shutdown_mode == 'emergency'

    def test_the_refusal_reaches_the_session_log(self, capsys):
        """The session log is the operator's channel; the global log alone would hide it."""
        session = _session(tick_loop_started=True)

        session._report_abort(REFUSAL)

        assert session._session_logger.lines[0][0] == 'error'
        assert session._session_logger.lines[0][1].startswith('⛔ Refused at the first tick')
        assert session._session_logger.lines[1] == (
            'info', 'No order was submitted in this session and no stop or take-profit fired.')
        assert session._global_logger.lines == []

    def test_a_crash_in_the_tick_loop_still_reads_as_one(self, capsys):
        session = _session(tick_loop_started=True)

        session._report_abort(KeyError('position'))

        assert 'RUNTIME ERROR — SESSION ABORTED' in capsys.readouterr().out
        assert not session._refused_at_first_tick

    def test_the_same_refusal_before_the_tick_loop_is_a_startup_failure(self, capsys):
        """The boot refusals (no bars at all, a capacity too small) stay STARTUP FAILED."""
        session = _session(tick_loop_started=False)

        session._report_abort(REFUSAL)

        assert '  ❌ STARTUP FAILED' in capsys.readouterr().out
        assert not session._refused_at_first_tick


class TestWhatReachedTheVenueIsNamed:
    """The executor's part of the first tick runs before the seam — and can send a close."""

    def test_a_take_profit_crossed_by_the_first_tick_is_named(self, capsys):
        """A carried LONG whose take profit the first tick crosses, then the seam refuses."""
        # A session that opened the position and wrote its carry-over
        previous = MockOrderExecution(mode=MockExecutionMode.INSTANT_FILL, spot_mode=True,
                                      initial_balances={'USD': 10000.0, 'BTC': 0.0})
        previous_executor = previous.create_executor()
        previous.feed_tick(previous_executor, symbol='BTCUSD', bid=50000.0, ask=50001.0)
        previous_executor.open_order(OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET, direction=OrderDirection.LONG,
            lots=0.01, take_profit=51000.0))
        previous.feed_tick(previous_executor, symbol='BTCUSD', bid=50000.0, ask=50001.0)
        book = previous_executor.portfolio.get_position_book()

        # This session restores it at boot, as the cold-start adopter does
        executor = MockOrderExecution(
            mode=MockExecutionMode.INSTANT_FILL, spot_mode=True,
            initial_balances={'USD': 9499.99, 'BTC': 0.01}).create_executor()
        executor.portfolio.restore_position_book(book)
        bar_controller = BarRenderingController(logger=RecordingLogger(), max_history=10)

        def refusing_seam(first_tick_time):
            raise REFUSAL

        bar_controller.register_warmup_seam(refusing_seam)

        # The first tick, in the AutoTrader loop's order: the executor, then the bars
        tick = TickData(timestamp=datetime.now(timezone.utc), symbol='BTCUSD',
                        bid=51100.0, ask=51101.0)
        executor.on_tick(tick)
        with pytest.raises(WarmupHistoryRefusedError) as refused:
            render_bars_for_tick(tick, bar_controller)
        session = _session(tick_loop_started=True, executor=executor)
        capsys.readouterr()

        session._report_abort(refused.value)

        block = capsys.readouterr().out.strip().splitlines()
        assert block[3] == ('  ⚠️ Before the refusal: 1 order(s) submitted in this session · '
                            '1 close(s) triggered by a stop or take-profit on this tick — check '
                            'what the venue holds before a restart.')
        assert session._session_logger.lines[1] == ('warning', block[3].strip())


class TestTheGradingStays:

    def test_a_refused_session_is_still_failed_and_exits_2(self):
        """A relaunch is the remedy, and a supervisor relaunches on a failed exit."""
        result = AutoTraderResult(shutdown_mode='emergency', emergency_reason=str(REFUSAL),
                                  refused_at_first_tick=True)
        assert result.get_outcome() is RunOutcome.FAILED
        assert result.get_exit_code() == 2


class _Constructed(Exception):
    """Raised in place of building the result, carrying what it would have been built with."""


def test_the_result_carries_the_refusal(monkeypatch):
    """`_collect_results` hands the flag on — the summary reads it from the result."""
    session = _session(tick_loop_started=True)
    session._report_abort(REFUSAL)
    session._session_start = time.monotonic()
    session._cold_start = SimpleNamespace(situation=None, verdict=None)
    session._first_interrupt_time = 0
    session._session_end = None
    # What the result's reach is read from: a session refused at its first tick opened its order
    # record and entered its loop, but this double built none of them
    session._order_event_stream = None
    session._study_recorder = None
    session._tick_source = None
    session._tick_loop = None

    def construct(**fields):
        raise _Constructed(fields)

    monkeypatch.setattr(autotrader_main_module, 'AutoTraderResult', construct)
    with pytest.raises(_Constructed) as built:
        session._collect_results(1, 0)

    assert built.value.args[0]['refused_at_first_tick'] is True
    assert built.value.args[0]['emergency_error_type'] == 'WarmupHistoryRefusedError'
