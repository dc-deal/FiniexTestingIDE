"""
FiniexTestingIDE - Shutdown Status Tests (#630)

One status every surface of a stop reads. A supervised session has no terminal and its loggers
buffer their console, so its stop stayed silent until the reports, and nobody could tell a stop that
waits from one that hangs. Here: the declared steps, the snapshot, the journal's lines, and a real
SIGTERM delivered to a real mock session.
"""

import contextlib
import dataclasses
import io
import os
import queue
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.operator_alerts.operator_alert_messages import session_ended_text
from python.framework.autotrader.shutdown_status_holder import ShutdownStatusHolder
from python.framework.autotrader.stop_budget import StopBudget
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.autotrader_types.shutdown_status_types import (
    SHUTDOWN_STEP_LABELS,
    AwaitedOrderEnding,
    ShutdownStatus,
    ShutdownStep,
    StopWaitOutcome,
    endings_by_order,
)
from python.framework.types.config_types.autotrader_defaults_config_types import ShutdownDefaults
from python.framework.types.trading_env_types.awaited_order_types import AwaitedOrder
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
)
from python.system.ui.autotrader_live_display import AutoTraderLiveDisplay
from tests.autotrader.cold_start.conftest import RecordingLogger
from tests.shared.fixture_helpers import remove_run_dir

ACCOUNT_MODELS = {
    'spot': 'configs/autotrader_profiles/mock/session_stop_awaited_order.json',
    'margin': 'configs/autotrader_profiles/mock/session_stop_awaited_order_margin.json',
}
_ASKED_AT = datetime(2026, 10, 12, 14, 22, 42, tzinfo=timezone.utc)
# Past every tick the profile replays: only the signal ends these sessions
_NEVER = 900000
_OPEN = OrderAction.OPEN


def _order(order_id: str = 'pos_ethusd_1', sent_at: Optional[float] = 100.0,
           action: OrderAction = OrderAction.OPEN,
           order_type: OrderType = OrderType.MARKET) -> AwaitedOrder:
    """
    One order on its way.

    Args:
        order_id: Its id
        sent_at: When it was sent, on the monotonic clock; None for an adopted one
        action: Whether it opens or closes
        order_type: What it was sent as

    Returns:
        Its row
    """
    return AwaitedOrder(
        order_id=order_id, action=action, order_type=order_type,
        direction=OrderDirection.LONG, lots=0.12, symbol='ETHUSD', sent_at_monotonic_s=sent_at,
        waits_for='venue has not answered')


def _status(**fields: Any) -> ShutdownStatus:
    """
    One moment of a stop.

    Args:
        fields: What differs from a stop just asked for

    Returns:
        The status
    """
    return dataclasses.replace(
        ShutdownStatus(asked_by='SIGTERM', asked_at=_ASKED_AT, as_of_monotonic_s=104.2,
                       kill_signalled=True), **fields)


class TestTheDeclaredSteps:
    """Each member has its words, and a whole session walks them all (`TestASigtermInASession`)."""

    def test_every_step_has_its_label_and_every_label_its_step(self):
        assert set(SHUTDOWN_STEP_LABELS) == set(ShutdownStep)
        assert all(SHUTDOWN_STEP_LABELS.values())

    def test_what_is_still_to_do_is_the_steps_after_the_current_one(self):
        steps = list(ShutdownStep)
        for position, step in enumerate(steps):
            assert step.steps_after() == steps[position + 1:]
        assert ShutdownStep.ALERTS.steps_after() == []

    def test_the_first_step_leaves_the_line_as_it_was_approved(self):
        status = _status(orders_to_cancel=0)

        assert ' · '.join(status.still_to_do()) == (
            'carry-over · 0 orders to cancel · venue read · reports · alerts')


class TestTheStatus:

    def test_the_headline_names_what_asked_and_when(self):
        assert _status().headline() == (
            '🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC · decisions stopped')

    @pytest.mark.parametrize('count, expected', [
        (0, '0 orders to cancel'), (1, '1 order to cancel'), (3, '3 orders to cancel'),
        (None, 'orders to cancel'),
    ])
    def test_the_cleanup_carries_its_count_where_it_is_known(self, count, expected):
        assert _status(orders_to_cancel=count).step_words(ShutdownStep.ORDER_CLEANUP) == expected

    def test_an_order_says_what_it_is_how_long_it_travels_and_what_it_waits_for(self):
        assert _status(awaited_orders=(_order(),)).order_lines() == [
            'market BUY 0.12 ETHUSD · on its way 4.2 s · venue has not answered']

    def test_an_order_this_session_did_not_send_has_no_age_to_name(self):
        assert _status(awaited_orders=(_order(sent_at=None),)).order_lines() == [
            'market BUY 0.12 ETHUSD · venue has not answered']

    def test_the_order_wait_says_how_far_and_what_follows(self):
        status = _status(order_wait_began_monotonic_s=100.0, order_wait_bound_s=30.0,
                         awaited_orders=(_order(),))

        assert status.order_wait_text() == (
            '4.2 s of 30.0 s, then: booked unaccounted, reported, session ends itself')

    @pytest.mark.parametrize('order_types, follows', [
        ((OrderType.LIMIT,), 'left to the cleanup'),
        ((OrderType.STOP, OrderType.LIMIT), 'left to the cleanup'),
        ((OrderType.MARKET, OrderType.LIMIT),
         'market orders booked unaccounted, resting ones left to the cleanup'),
    ], ids=['resting', 'all_resting', 'mixed'])
    def test_a_resting_order_is_promised_the_cleanup_not_a_booking(self, order_types, follows):
        """The run-out books a market order; a resting one goes on to the cleanup's policy."""
        status = _status(
            order_wait_began_monotonic_s=100.0, order_wait_bound_s=5.94,
            awaited_orders=tuple(_order(str(index), order_type=order_type)
                                 for index, order_type in enumerate(order_types)))

        assert status.order_wait_text() == (
            f'4.2 s of 5.9 s, then: {follows}, reported, session ends itself')

    def test_no_wait_has_no_text(self):
        assert _status().order_wait_text() is None

    @pytest.mark.parametrize('kill_at, expected', [
        (None, None),
        (180.0, 'in 75.8 s (supervisor)'),
        (104.2, 'in 0.0 s (supervisor)'),
        (102.2, 'overdue by 2.0 s (supervisor)'),
    ], ids=['undeclared', 'ahead', 'due', 'past'])
    def test_the_kill_counts_down_from_the_reading(self, kill_at, expected):
        assert _status(kill_at_monotonic_s=kill_at).kill_text() == expected

    @pytest.mark.parametrize('kill_at, expected', [
        (None, None),
        (180.0, 'not signalled, the stop keeps within 75.8 s'),
        (102.2, 'not signalled, the stop is 2.0 s past the time it keeps within'),
    ], ids=['undeclared', 'ahead', 'past'])
    def test_a_stop_nobody_signalled_counts_down_no_kill(self, kill_at, expected):
        """A strategy's own end: no supervisor kills, and the stop still keeps to the time."""
        status = _status(asked_by="strategy's request", kill_signalled=False,
                         kill_at_monotonic_s=kill_at)

        assert status.kill_text() == expected

    def test_it_names_the_step_in_progress_only_where_no_order_says_it(self):
        assert _status(step=ShutdownStep.VENUE_READ).now_doing() == 'venue read'
        assert _status(awaited_orders=(_order(),)).now_doing() is None

    def test_the_step_line_says_the_step_and_what_follows(self):
        assert _status(step=ShutdownStep.VENUE_READ).step_line() == (
            '🛑 Stop: venue read · still to do: reports · alerts')
        assert _status(step=ShutdownStep.ALERTS).step_line() == '🛑 Stop: alerts'


class TestWhatTheWaitCameTo:
    """Read off the order records, never by subtracting one counter from another."""

    @pytest.mark.parametrize('status', list(OrderStatus), ids=lambda s: s.name)
    def test_every_way_an_order_stands_counts_once(self, status):
        history = [OrderResult(order_id='a', status=OrderStatus.PENDING, action=_OPEN),
                   OrderResult(order_id='a', status=status, action=_OPEN)]

        outcome = StopWaitOutcome.over_records(1.5, [('a', _OPEN)], history)

        assert outcome.waited_s == 1.5
        assert outcome.orders_waited_for() == 1
        assert outcome.orders_executed == (status is OrderStatus.EXECUTED)
        assert outcome.orders_unaccounted == (status is OrderStatus.UNACCOUNTED)
        assert outcome.orders_still_open == (status is OrderStatus.PENDING)
        assert outcome.orders_ended_unfilled == (status not in {
            OrderStatus.EXECUTED, OrderStatus.UNACCOUNTED, OrderStatus.PENDING})

    def test_an_order_with_no_record_stays_open_rather_than_filled(self):
        assert StopWaitOutcome.over_records(1.0, [('gone', _OPEN)], []).orders_still_open == 1

    def test_an_order_it_did_not_wait_for_does_not_count(self):
        history = [OrderResult(order_id='a', status=OrderStatus.EXECUTED, action=_OPEN),
                   OrderResult(order_id='b', status=OrderStatus.EXECUTED, action=_OPEN)]

        assert StopWaitOutcome.over_records(
            1.0, [('a', _OPEN)], history).orders_waited_for() == 1

    def test_the_last_ending_is_the_one_that_counts(self):
        history = [OrderResult(order_id='a', status=OrderStatus.UNACCOUNTED, action=_OPEN),
                   OrderResult(order_id='a', status=OrderStatus.EXECUTED, action=_OPEN)]

        assert endings_by_order([('a', _OPEN)], history) == {
            ('a', _OPEN): AwaitedOrderEnding(status=OrderStatus.EXECUTED)}

    def test_an_open_and_its_close_are_two_orders_under_one_id(self):
        """A market close carries its position's id, and both executed."""
        history = [
            OrderResult(order_id='p', status=OrderStatus.PENDING, action=_OPEN),
            OrderResult(order_id='p', status=OrderStatus.EXECUTED, action=_OPEN),
            OrderResult(order_id='p', status=OrderStatus.PENDING, action=OrderAction.CLOSE),
            OrderResult(order_id='p', status=OrderStatus.EXECUTED, action=OrderAction.CLOSE),
        ]

        outcome = StopWaitOutcome.over_records(
            1.0, [('p', _OPEN), ('p', OrderAction.CLOSE)], history)

        assert (outcome.orders_executed, outcome.orders_waited_for()) == (2, 2)

    def test_an_execution_below_the_lots_sent_is_a_part(self):
        history = [
            OrderResult(order_id='a', status=OrderStatus.PENDING, action=_OPEN,
                        requested_lots=0.01),
            OrderResult(order_id='a', status=OrderStatus.EXECUTED, action=_OPEN,
                        requested_lots=0.004, executed_lots=0.004),
        ]

        outcome = StopWaitOutcome.over_records(1.0, [('a', _OPEN)], history)
        ending = endings_by_order([('a', _OPEN)], history)[('a', _OPEN)]

        assert (outcome.orders_filled_in_part, outcome.orders_executed) == (1, 0)
        assert outcome.orders_waited_for() == 1
        assert ending.describe() == 'executed in part, 0.004 of 0.01'

    def test_a_full_execution_is_no_part(self):
        history = [
            OrderResult(order_id='a', status=OrderStatus.PENDING, action=_OPEN,
                        requested_lots=0.01),
            OrderResult(order_id='a', status=OrderStatus.EXECUTED, action=_OPEN,
                        requested_lots=0.01, executed_lots=0.01),
        ]

        outcome = StopWaitOutcome.over_records(1.0, [('a', _OPEN)], history)

        assert (outcome.orders_filled_in_part, outcome.orders_executed) == (0, 1)


def _holder(kill_after_s: Optional[float] = None,
            orders_to_cancel: Optional[int] = None) -> Tuple[ShutdownStatusHolder, RecordingLogger]:
    """
    A holder whose stop was asked for at monotonic 100.

    Args:
        kill_after_s: The declared kill
        orders_to_cancel: What the session answers about the cleanup; None answers nothing

    Returns:
        The holder and the session log it writes to
    """
    holder = ShutdownStatusHolder(kill_after_s)
    log = RecordingLogger()
    holder.connect_journal(log, None if orders_to_cancel is None else lambda: orders_to_cancel)
    holder.note_asked('SIGTERM', _ASKED_AT, 100.0, signalled=True)
    return holder, log


class TestTheHolder:

    def test_no_stop_no_status(self):
        assert ShutdownStatusHolder(None).get() is None

    def test_what_asked_first_stays(self):
        holder, _ = _holder()

        holder.note_asked('Ctrl+C', datetime(2027, 1, 1, tzinfo=timezone.utc), 500.0)

        status = holder.get()
        assert (status.asked_by, status.asked_at) == ('SIGTERM', _ASKED_AT)

    def test_each_reading_is_measured_against_its_own_moment(self):
        holder, _ = _holder()

        before = time.monotonic()
        reading = holder.get().as_of_monotonic_s

        assert before <= reading <= time.monotonic()

    def test_the_kill_counts_from_the_instant_the_budget_counts_from(self):
        asked_at_s = time.monotonic()
        holder = ShutdownStatusHolder(80.0)
        holder.note_asked('SIGTERM', _ASKED_AT, asked_at_s)
        budget = StopBudget(80.0, 15.0, asked_at_s)

        status = holder.get()
        assert status.kill_at_monotonic_s == asked_at_s + 80.0
        assert status.kill_at_monotonic_s - status.as_of_monotonic_s == pytest.approx(
            budget.until_kill_s(), abs=0.5)

    def test_no_kill_declared_no_kill(self):
        holder, _ = _holder()
        assert holder.get().kill_at_monotonic_s is None

    def test_a_signal_after_the_first_ask_says_a_kill_is_coming(self):
        """A strategy's own end, then a supervisor's SIGTERM: the first word stays, the kill comes."""
        holder = ShutdownStatusHolder(80.0)
        holder.note_asked("strategy's request", _ASKED_AT, 100.0)
        assert not holder.get().kill_signalled

        holder.note_asked('SIGTERM', datetime(2027, 1, 1, tzinfo=timezone.utc), 105.0,
                          signalled=True)

        status = holder.get()
        assert (status.asked_by, status.asked_at) == ("strategy's request", _ASKED_AT)
        assert status.kill_signalled
        assert status.kill_at_monotonic_s == 180.0

    def test_a_write_replaces_the_snapshot_and_leaves_the_reader_its_own(self):
        holder, _ = _holder()
        before = holder.get()

        holder.enter_step(ShutdownStep.CARRY_OVER)

        assert before.step is ShutdownStep.LOOP_END
        assert holder.get().step is ShutdownStep.CARRY_OVER
        with pytest.raises(dataclasses.FrozenInstanceError):
            before.step = ShutdownStep.ALERTS

    def test_a_step_reads_what_the_cleanup_will_cancel(self):
        holder, log = _holder(orders_to_cancel=2)

        holder.enter_step(ShutdownStep.CARRY_OVER)

        assert holder.get().orders_to_cancel == 2
        assert log.infos[-1] == ('🛑 Stop: carry-over · still to do: 2 orders to cancel · '
                                 'venue read · reports · alerts')

    def test_a_step_ends_what_a_crashed_wait_left_on_screen(self):
        holder, _ = _holder()
        holder.order_wait_began([_order()], 100.0, 30.0)

        holder.enter_step(ShutdownStep.CARRY_OVER)

        status = holder.get()
        assert status.awaited_orders == ()
        assert status.order_wait_text() is None
        # It reached no end of its own, so nothing claims how long it ran
        assert status.order_wait_ran_s is None

    def test_a_write_with_no_stop_asked_writes_nothing(self):
        holder = ShutdownStatusHolder(None)
        log = RecordingLogger()
        holder.connect_journal(log)

        holder.enter_step(ShutdownStep.REPORTS)
        holder.order_wait_ended(1.0)
        holder.note_tick_source_stopped()

        assert holder.get() is None
        assert log.infos == []

    def test_the_source_is_told_apart_once_it_was_stopped(self):
        holder, _ = _holder()
        assert holder.get().tick_source_stopped is False

        holder.note_tick_source_stopped()

        assert holder.get().tick_source_stopped is True


class _RecordingStream(io.StringIO):
    """A console that remembers what had been flushed when."""

    def __init__(self) -> None:
        super().__init__()
        self.flushed = ''

    def flush(self) -> None:
        self.flushed = self.getvalue()


class _BrokenStream(io.StringIO):
    """A console that is gone."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def write(self, text: str) -> int:
        raise self._error


class TestTheJournal:

    def test_a_line_reaches_the_console_flushed_and_the_session_log(self):
        holder, log = _holder()
        console = _RecordingStream()

        with contextlib.redirect_stdout(console):
            holder.enter_step(ShutdownStep.VENUE_READ)

        assert log.infos == [
            '🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC · decisions stopped',
            '🛑 Stop: venue read · still to do: reports · alerts',
        ]
        assert console.flushed.splitlines() == log.infos

    def test_with_the_display_on_it_stays_off_the_console(self):
        holder, log = _holder()
        holder.set_lines_on_console(False)
        console = _RecordingStream()

        with contextlib.redirect_stdout(console):
            holder.enter_step(ShutdownStep.VENUE_READ)

        assert console.getvalue() == ''
        assert len(log.infos) == 2

    def test_once_the_display_stopped_it_is_on_the_console_again(self):
        holder, _ = _holder()
        holder.set_lines_on_console(False)
        holder.set_lines_on_console(True)
        console = _RecordingStream()

        with contextlib.redirect_stdout(console):
            holder.enter_step(ShutdownStep.REPORTS)

        assert '🛑 Stop: reports' in console.getvalue()

    @pytest.mark.parametrize('error', [
        BrokenPipeError('the journal went away'),
        ValueError('I/O operation on closed file'),
        RuntimeError('reentrant call inside <_io.BufferedWriter>'),
    ], ids=['broken_pipe', 'closed', 'reentrant'])
    def test_a_console_that_is_gone_does_not_end_the_stop(self, error):
        holder, log = _holder()

        with contextlib.redirect_stdout(_BrokenStream(error)):
            holder.enter_step(ShutdownStep.VENUE_READ)

        assert log.infos[-1] == '🛑 Stop: venue read · still to do: reports · alerts'
        assert holder.get().step is ShutdownStep.VENUE_READ

    def test_what_asked_is_said_once_at_the_first_step_with_the_kill(self):
        holder = ShutdownStatusHolder(80.0)
        log = RecordingLogger()
        holder.connect_journal(log)
        holder.note_asked('SIGTERM', _ASKED_AT, time.monotonic(), signalled=True)
        assert log.infos == []

        holder.enter_step(ShutdownStep.CARRY_OVER)
        holder.enter_step(ShutdownStep.ORDER_CLEANUP)

        said = [line for line in log.infos if 'SHUTTING DOWN' in line]
        assert len(said) == 1
        assert said[0].startswith(
            '🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC · decisions stopped · kill in ')
        assert said[0].endswith(' s (supervisor)')

    def test_the_wait_says_when_it_begins_and_what_settled_and_when_nothing_is_left(self):
        holder, log = _holder()
        first, second = _order('a'), _order('b')

        holder.order_wait_began([first, second], 100.0, 30.0)
        holder.order_wait_passed(
            [second], [(first, AwaitedOrderEnding(status=OrderStatus.EXECUTED))], 2.0, 28.0)
        holder.order_wait_passed([], [(second, AwaitedOrderEnding(status=None))], 3.0, 27.0)

        assert log.infos[1:] == [
            '⏳ Stop: 2 order(s) on their way, waiting up to 30.0 s',
            '⏳ Stop: market BUY 0.12 ETHUSD settled after 2.0 s: executed',
            '⏳ Stop: 1 order(s) still on their way after 2.0 s, at most 28.0 s more',
            '⏳ Stop: market BUY 0.12 ETHUSD settled after 3.0 s: open at the venue',
            '⏳ Stop: nothing on its way any more after 3.0 s',
        ]

    def test_a_quiet_wait_says_what_it_waits_for_every_five_seconds(self):
        holder, log = _holder()
        row = _order()
        holder.order_wait_began([row], 100.0, 30.0)
        already_said = len(log.infos)

        for waited_s in (1.0, 4.9, 5.1, 9.9, 10.2, 12.0):
            holder.order_wait_passed([row], [], waited_s, 30.0 - waited_s)

        said = log.infos[already_said:]
        assert [line.split(' · ')[0] for line in said] == [
            '⏳ Stop: waited 5.1 s of 30.0 s', '⏳ Stop: waited 10.2 s of 30.0 s']
        assert all('market BUY 0.12 ETHUSD' in line and 'venue has not answered' in line
                   for line in said)

    def test_the_status_carries_the_wait_while_it_runs_and_its_length_afterwards(self):
        holder, _ = _holder()
        first, second = _order('a'), _order('b')

        holder.order_wait_began([first], 100.0, 30.0)
        waiting = holder.get()
        holder.order_wait_passed([first, second], [], 1.0, 21.0)
        with_a_new_order = holder.get()
        holder.order_wait_ended(2.5)
        over = holder.get()

        assert waiting.awaited_orders == (first,)
        assert waiting.order_wait_began_monotonic_s == 100.0
        assert waiting.order_wait_ran_s is None
        # A bound a kill shortened is the bound that is shown
        assert with_a_new_order.order_wait_bound_s == 22.0
        assert over.awaited_orders == ()
        assert over.order_wait_text() is None
        assert over.order_wait_ran_s == 2.5
        assert over.orders_waited_for == {('a', OrderAction.OPEN), ('b', OrderAction.OPEN)}

    def test_the_run_out_says_it_before_the_last_read_and_what_it_came_to_after(self):
        holder, log = _holder()
        given_up, left = _order('a'), _order('b')
        holder.order_wait_began([given_up, left], 100.0, 30.0)

        holder.order_wait_ran_out([given_up, left], 30.1)
        holder.order_wait_given_up([given_up], [left], 30.1)

        assert log.infos[-2:] == [
            '⏳ Stop: the wait ran out after 30.1 s with 2 order(s) on their way',
            '⏳ Stop: 1 order(s) recorded unaccounted, 1 left to the cleanup',
        ]
        assert holder.get().order_wait_ran_s == 30.1
        assert holder.get().awaited_orders == ()


class TestASignalHandlerWritesNothing:
    """
    A handler interrupts the main thread between two bytecodes, possibly inside a write to the very
    stream a print would use, and that print then raises. Its line waits for the first step.
    """

    @pytest.mark.parametrize('sent, asked_by', [
        (signal.SIGTERM, 'SIGTERM'),
        (signal.SIGINT, 'Ctrl+C'),
    ], ids=['sigterm', 'ctrl_c'])
    def test_a_real_signal_only_notes_what_asked(self, sent, asked_by):
        session = AutotraderMain(AutoTraderConfig(
            profile_name='status', symbol='BTCUSD', broker_type='kraken_spot',
            shutdown=ShutdownDefaults(supervisor_kill_after_s=80.0)))
        journal = RecordingLogger()
        session._shutdown_status.connect_journal(journal)
        # A log write here could meet the very logger the interrupted main thread is inside
        session._global_logger = RecordingLogger()
        previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
        session._setup_signal_handlers()
        console = _RecordingStream()
        try:
            with contextlib.redirect_stdout(console):
                os.kill(os.getpid(), sent)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)

        status = session._shutdown_status.get()
        assert status.asked_by == asked_by
        assert status.kill_signalled
        assert status.kill_at_monotonic_s == session._stop_asked_monotonic_s + 80.0
        assert journal.infos == []
        assert session._global_logger.infos == []
        # Nothing reaches the buffered stdout: Ctrl+C's notice goes to the descriptor
        assert console.getvalue() == ''

    def test_ctrl_c_writes_its_notice_past_the_buffered_stream(self, monkeypatch, capfd):
        """A print would raise if the main thread were inside one; the notice reaches stderr all the same."""
        session = AutotraderMain(AutoTraderConfig(
            profile_name='status', symbol='BTCUSD', broker_type='kraken_spot',
            shutdown=ShutdownDefaults(supervisor_kill_after_s=80.0)))
        session._global_logger = RecordingLogger()

        def _no_print(*args, **kwargs):
            raise RuntimeError('reentrant call inside <_io.BufferedWriter>')

        monkeypatch.setattr('builtins.print', _no_print)
        session._handle_interrupt(signal.SIGINT, None)

        assert session._shutdown_status.get().asked_by == 'Ctrl+C'
        assert 'Shutdown initiated' in capfd.readouterr().err


def _session_under_sigterm(profile: str, settings: ShutdownDefaults, answer_after_s: float,
                           bot_suffix: str,
                           display: bool = False,
                           terminal: Optional[bool] = None) -> Tuple[AutotraderMain,
                                                                     AutoTraderResult, str,
                                                                     Dict[str, List[Any]]]:
    """
    One whole mock session that receives a real SIGTERM with an order on its way.

    The signal is sent on the main thread right after the order went out, so it lands inside a tick
    pass the way a supervisor's does, and `os.kill` runs the handler before it returns: nothing here
    depends on which thread is scheduled when.

    Args:
        profile: The profile
        settings: The stop's settings
        answer_after_s: How long the venue takes to answer a submit, after it acted
        bot_suffix: Keeps this session's carry-over apart from the others'
        display: Whether the live display runs
        terminal: What the display answers about its terminal; None leaves it the real answer,
            and the console here is no terminal

    Returns:
        The session, its result, what reached the console, and what was watched: the status of each
        frame rendered, and the order in which the stop's marks came
    """
    config = load_autotrader_config(profile)
    config.shutdown = settings
    config.bot_id = f'{config.bot_id}{bot_suffix}'
    config.strategy_config['decision_logic_config']['session_end_tick'] = _NEVER
    config.display.enabled = display
    config.display.update_interval_ms = 10
    trader = AutotraderMain(config)
    watched: Dict[str, List[Any]] = {'frames': [], 'marks': []}
    real_submit = MockBrokerAdapter.do_request_submit
    real_open = LiveTradeExecutor.open_order
    real_render = AutoTraderLiveDisplay.render
    real_display_stop = AutoTraderLiveDisplay.stop
    real_truth = LiveTradeExecutor.record_session_truth
    real_collect = AutotraderMain._collect_results

    def slow_submit(adapter: MockBrokerAdapter, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = real_submit(adapter, payload)
        time.sleep(answer_after_s)
        return raw

    def open_then_signal(executor: LiveTradeExecutor, *args: Any, **kwargs: Any) -> Any:
        result = real_open(executor, *args, **kwargs)
        os.kill(os.getpid(), signal.SIGTERM)
        return result

    def watching_render(instance: AutoTraderLiveDisplay, *args: Any) -> Any:
        watched['frames'].append(args[4])
        return real_render(instance, *args)

    def watching_display_stop(instance: AutoTraderLiveDisplay) -> bool:
        watched['marks'].append('display_stop')
        return real_display_stop(instance)

    def watching_truth(instance: LiveTradeExecutor, *args: Any, **kwargs: Any) -> Any:
        watched['marks'].append('end_read')
        return real_truth(instance, *args, **kwargs)

    def watching_collect(instance: AutotraderMain, *args: Any) -> AutoTraderResult:
        watched['marks'].append('reports')
        return real_collect(instance, *args)

    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    console = io.StringIO()
    try:
        with pytest.MonkeyPatch.context() as patch, contextlib.redirect_stdout(console):
            patch.setattr(MockBrokerAdapter, 'do_request_submit', slow_submit)
            patch.setattr(LiveTradeExecutor, 'open_order', open_then_signal)
            patch.setattr(AutoTraderLiveDisplay, 'render', watching_render)
            patch.setattr(AutoTraderLiveDisplay, 'stop', watching_display_stop)
            patch.setattr(LiveTradeExecutor, 'record_session_truth', watching_truth)
            patch.setattr(AutotraderMain, '_collect_results', watching_collect)
            if terminal is not None:
                patch.setattr(AutoTraderLiveDisplay, 'holds_a_terminal',
                              lambda instance: terminal)
            result = trader.run()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return trader, result, console.getvalue(), watched


def _session_log(trader: AutotraderMain) -> str:
    """
    What a finished session wrote to its session log: the start's file, then the files of the
    trading days, which the loop rotates to.

    Args:
        trader: The session

    Returns:
        The text
    """
    run_dir = Path(trader._run_dir)
    files = [run_dir / 'autotrader_session.log', *sorted((run_dir / 'session_logs').glob('*.log'))]
    return ''.join(file.read_text(encoding='utf-8') for file in files)


def _lines_in_order(text: str, wanted: List[str]) -> None:
    """
    Assert that each wanted piece stands in a text, each after the one before it.

    Args:
        text: The text
        wanted: The pieces, in the order they must come
    """
    position = 0
    for piece in wanted:
        found = text.find(piece, position)
        assert found >= 0, f'{piece!r} not found after position {position}:\n{text}'
        position = found + len(piece)


_THE_LINES_OF_A_STOP_WITH_AN_ORDER = [
    '🛑 SHUTTING DOWN · SIGTERM ',
    '⏳ Stop: 1 order(s) on their way',
    '⏳ Stop: market BUY 0.01 ',
    ' settled after ',
    ': executed',
    '⏳ Stop: nothing on its way any more',
    '🛑 Stop: stopping the feeds',
    '🛑 Stop: carry-over · still to do: 0 orders to cancel · venue read · reports · alerts',
    '🛑 Stop: 0 orders to cancel · still to do: venue read · reports · alerts',
    '🛑 Stop: venue read · still to do: reports · alerts',
    '🛑 Stop: reports · still to do: alerts',
    '🛑 Stop: alerts',
]


@pytest.fixture(scope='module', params=list(ACCOUNT_MODELS), ids=list(ACCOUNT_MODELS))
def session_under_sigterm(request):
    """
    One mock session stopped by a real SIGTERM while its order is on its way, the display off.

    Yields:
        The session, its result, what reached the console, what was watched
    """
    built = _session_under_sigterm(
        ACCOUNT_MODELS[request.param], ShutdownDefaults(order_wait_s=10.0), answer_after_s=0.3,
        bot_suffix='sg')
    yield built
    remove_run_dir(built[0]._run_dir)


class TestASigtermInASession:

    def test_it_asked_for_the_stop_and_waited(self, session_under_sigterm):
        trader, result, _, _ = session_under_sigterm

        assert trader._shutdown_status.get().asked_by == 'SIGTERM'
        assert result.shutdown_mode == 'normal'
        assert result.get_exit_code() == 0

    def test_the_session_log_has_the_lines_in_order(self, session_under_sigterm):
        trader, _, _, _ = session_under_sigterm

        log = _session_log(trader)

        _lines_in_order(log, _THE_LINES_OF_A_STOP_WITH_AN_ORDER)

    def test_the_console_has_them_too_with_the_display_off(self, session_under_sigterm):
        _, _, console, _ = session_under_sigterm

        _lines_in_order(console, _THE_LINES_OF_A_STOP_WITH_AN_ORDER)

    def test_it_walked_every_step_in_the_declared_order(self, session_under_sigterm):
        trader, _, _, _ = session_under_sigterm
        log = _session_log(trader)
        status = trader._shutdown_status.get()

        assert status.step is list(ShutdownStep)[-1]
        said = [dataclasses.replace(status, step=step, orders_to_cancel=0).step_line()
                for step in ShutdownStep if step is not ShutdownStep.LOOP_END]
        _lines_in_order(log, said)

    def test_the_result_says_what_the_wait_came_to(self, session_under_sigterm):
        _, result, _, _ = session_under_sigterm

        assert result.stop_wait.orders_waited_for() == 1
        assert result.stop_wait.orders_executed == 1
        assert result.stop_wait.orders_unaccounted == 0
        assert 0.0 < result.stop_wait.waited_s < 10.0

    def test_the_closing_message_carries_its_line(self, session_under_sigterm):
        _, result, _, _ = session_under_sigterm

        _, _, detail = session_ended_text(result, None)

        line = [row for row in detail.splitlines() if row.startswith('Stop:')]
        assert len(line) == 1
        assert line[0].startswith('Stop: waited ')
        assert line[0].endswith(' for 1 order, filled and booked')


@pytest.fixture(scope='module', params=list(ACCOUNT_MODELS), ids=list(ACCOUNT_MODELS))
def session_under_sigterm_run_out(request):
    """
    One mock session stopped by a real SIGTERM whose order stays unanswered past the bound.

    Yields:
        The session, its result, what reached the console, what was watched
    """
    built = _session_under_sigterm(
        ACCOUNT_MODELS[request.param],
        ShutdownDefaults(order_wait_s=0.2, request_worker_join_s=0.1),
        answer_after_s=1.0, bot_suffix='sr')
    yield built
    remove_run_dir(built[0]._run_dir)


class TestASigtermWhoseWaitRunsOut:

    def test_the_lines_say_it(self, session_under_sigterm_run_out):
        trader, _, console, _ = session_under_sigterm_run_out
        log = _session_log(trader)
        wanted = [
            '🛑 SHUTTING DOWN · SIGTERM ',
            '⏳ Stop: 1 order(s) on their way',
            '⏳ Stop: the wait ran out after ',
            '⏳ Stop: 1 order(s) recorded unaccounted, 0 left to the cleanup',
            '🛑 Stop: carry-over',
        ]

        _lines_in_order(log, wanted)
        _lines_in_order(console, wanted)

    def test_the_closing_message_names_the_unanswered_count(self, session_under_sigterm_run_out):
        _, result, _, _ = session_under_sigterm_run_out

        assert result.stop_wait.orders_unaccounted == 1
        assert result.stop_wait.orders_executed == 0
        assert result.get_exit_code() == 3
        _, _, detail = session_ended_text(result, None)
        assert ' for 1 order, no answer for 1, recorded unaccounted' in detail


@pytest.fixture(scope='module')
def session_with_the_display_on():
    """
    One mock session stopped by a real SIGTERM with the live display running, on spot.

    Yields:
        The session, its result, what reached the console, what was watched
    """
    built = _session_under_sigterm(
        ACCOUNT_MODELS['spot'], ShutdownDefaults(order_wait_s=10.0), answer_after_s=0.3,
        bot_suffix='sd', display=True, terminal=True)
    yield built
    remove_run_dir(built[0]._run_dir)


class TestTheDisplayThroughAStop:

    def test_it_showed_the_order_it_waited_for(self, session_with_the_display_on):
        _, _, _, watched = session_with_the_display_on
        stops = [frame for frame in watched['frames'] if frame is not None]

        assert any(frame.awaited_orders for frame in stops)
        assert [row.order_id for frame in stops for row in frame.awaited_orders]

    def test_it_stays_on_until_after_the_end_read_and_ends_before_the_reports(
            self, session_with_the_display_on):
        _, _, _, watched = session_with_the_display_on
        marks = watched['marks']

        assert marks.index('end_read') < marks.index('display_stop') < marks.index('reports')

    def test_its_last_frame_says_what_is_left(self, session_with_the_display_on):
        _, _, console, watched = session_with_the_display_on
        last = watched['frames'][-1]

        assert last.step is ShutdownStep.VENUE_READ
        assert last.tick_source_stopped
        assert 'SHUTTING DOWN' in console
        assert 'Still to do' in console
        assert 'reports · alerts' in console

    def test_the_lines_reach_the_console_only_once_it_stopped(self, session_with_the_display_on):
        trader, _, console, _ = session_with_the_display_on
        log = _session_log(trader)

        # While the display held the console they went to the session log alone
        assert '⏳ Stop: 1 order(s) on their way' not in console
        assert '🛑 Stop: venue read' not in console
        _lines_in_order(log, _THE_LINES_OF_A_STOP_WITH_AN_ORDER)
        _lines_in_order(console, ['🛑 Stop: reports · still to do: alerts', '🛑 Stop: alerts'])

    def test_no_panel_failed(self, session_with_the_display_on):
        _, result, console, _ = session_with_the_display_on

        assert 'panel failed' not in console
        assert 'render failed' not in console
        assert result.get_exit_code() == 0


class TestADisplayWithNoTerminal:
    """
    A profile has the display on by default, and under a supervisor it has no terminal: it shows
    only its last frame. Going by "the display is on", that session's journal would hear nothing.
    """

    def test_the_journal_still_hears_every_line(self):
        trader, result, console, _ = _session_under_sigterm(
            ACCOUNT_MODELS['spot'], ShutdownDefaults(order_wait_s=10.0), answer_after_s=0.3,
            bot_suffix='sn', display=True)
        try:
            _lines_in_order(console, _THE_LINES_OF_A_STOP_WITH_AN_ORDER)
            assert result.get_exit_code() == 0
        finally:
            remove_run_dir(trader._run_dir)


class TestACountThatFails:
    """The panel's number of orders to cancel must not end the stop before its cancels."""

    def test_the_steps_go_on_and_it_is_said_once(self):
        logger = MagicMock()

        def broken_count() -> int:
            raise RuntimeError('snapshot failed')

        holder = ShutdownStatusHolder(None)
        holder.connect_journal(logger, broken_count)
        holder.set_lines_on_console(False)
        holder.note_asked('SIGTERM', datetime.now(timezone.utc), time.monotonic())
        steps = list(ShutdownStep)

        holder.enter_step(steps[1])
        holder.enter_step(steps[2])

        assert holder.get().orders_to_cancel is None
        logger.error.assert_called_once()


class TestAFillTimeoutInsideAWholeStop:
    """The fill timeout counts from the send, so it falls inside the wait, and the wait decides."""

    @pytest.mark.parametrize('model', ACCOUNT_MODELS)
    def test_an_answer_within_the_wait_is_booked(self, model, monkeypatch):
        real_load = load_autotrader_config

        def short_fill_timeout(profile: str) -> AutoTraderConfig:
            config = real_load(profile)
            config.execution.order_timeout_seconds = 0.5
            return config

        monkeypatch.setitem(globals(), 'load_autotrader_config', short_fill_timeout)
        trader, result, _, _ = _session_under_sigterm(
            ACCOUNT_MODELS[model], ShutdownDefaults(order_wait_s=5.0, request_worker_join_s=0.1), 2.0,
            f'f{model[0]}')
        try:
            assert result.stop_wait.orders_executed == 1
            assert result.stop_wait.orders_unaccounted == 0
            assert len(result.open_positions) == 1
            assert result.get_exit_code() == 0
        finally:
            remove_run_dir(trader._run_dir)


class TestACrashWhileStopping:
    """A crash in the stop or the reports still stops the display and says so out of band."""

    def test_the_display_is_stopped_the_alert_goes_out_and_the_flush_is_an_emergency(self):
        config = load_autotrader_config('configs/autotrader_profiles/mock/session_stop_test.json')
        config.bot_id = f'{config.bot_id}cr'
        trader = AutotraderMain(config)
        display = MagicMock()
        display.stop.return_value = True
        watched: Dict[str, Any] = {}

        def crashing_shutdown(instance: AutotraderMain, *args: Any) -> AutoTraderResult:
            instance._display = display
            watched['alerts'] = MagicMock(wraps=instance._operator_alerts)
            instance._operator_alerts = watched['alerts']
            raise RuntimeError('the stop crashed')

        with pytest.MonkeyPatch.context() as patch, contextlib.redirect_stdout(io.StringIO()):
            patch.setattr(AutotraderMain, '_shutdown', crashing_shutdown)
            with pytest.raises(RuntimeError, match='the stop crashed'):
                trader.run()
        remove_run_dir(trader._run_dir)

        display.stop.assert_called_once()
        kind, level, title, detail = watched['alerts'].send.call_args.args
        assert (kind, level) == (OperatorAlertKind.SHUTDOWN_CRASHED, OperatorAlertLevel.CRITICAL)
        assert 'the stop crashed' in detail
        assert watched['alerts'].finish.call_args.kwargs['emergency'] is True


class TestThreadsThatOutlastTheirJoin:
    """A join that times out is said on the session channel, never passed over in silence."""

    def _session(self) -> AutotraderMain:
        session = AutotraderMain(AutoTraderConfig(
            profile_name='status', symbol='BTCUSD', broker_type='kraken_spot',
            shutdown=ShutdownDefaults(display_join_s=0.25)))
        session._session_logger = RecordingLogger()
        session._global_logger = RecordingLogger()
        return session

    def test_a_display_that_did_not_finish_is_said(self):
        session = self._session()
        session._display = MagicMock()
        session._display.stop.return_value = False

        session._stop_display()

        assert any('live display did not finish' in message and '0.25 s' in message
                   for message in session._session_logger.warnings)

    def test_a_display_that_finished_says_nothing(self):
        session = self._session()
        session._display = MagicMock()
        session._display.stop.return_value = True

        session._stop_display()

        assert session._session_logger.warnings == []

    def test_the_display_reports_a_thread_it_could_not_join(self):
        display = AutoTraderLiveDisplay(
            queue.Queue(), MagicMock(),
            AutoTraderConfig(shutdown=ShutdownDefaults(display_join_s=0.01)))
        stuck = MagicMock()
        stuck.is_alive.return_value = True
        display._thread = stuck

        assert display.stop() is False
        stuck.is_alive.return_value = False
        assert display.stop() is True


class TestADisplayThatFails:

    def test_it_hands_the_console_back_to_the_lines(self):
        holder = ShutdownStatusHolder(None)
        holder.set_lines_on_console(False)
        display = AutoTraderLiveDisplay(
            queue.Queue(), MagicMock(), AutoTraderConfig(),
            shutdown_status=holder)
        display._running = True

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr('python.system.ui.autotrader_live_display.Live', MagicMock(
                side_effect=RuntimeError('no terminal after all')))
            display._update_loop()

        assert holder._lines_on_console is True


class TestTheLastFrameAfterTheCleanup:

    def test_the_display_gets_the_book_the_cleanup_left_before_it_stops(self, monkeypatch):
        session = AutotraderMain(AutoTraderConfig(
            profile_name='status', symbol='BTCUSD', broker_type='kraken_spot'))
        session._session_logger = RecordingLogger()
        session._global_logger = RecordingLogger()
        marks: List[str] = []
        session._tick_loop = MagicMock()
        session._tick_loop.push_last_display_frame.side_effect = lambda ticks: marks.append(
            f'frame:{ticks}')
        monkeypatch.setattr(session, '_stop_display', lambda: marks.append('display_stop'))
        monkeypatch.setattr(session, '_collect_results',
                            lambda ticks, clipped: AutoTraderResult())
        monkeypatch.setattr(session, '_generate_reports', lambda result: None)
        monkeypatch.setattr('python.framework.autotrader.autotrader_main.SessionPostRunValidator',
                            MagicMock())

        session._shutdown(7, 0)

        assert marks == ['frame:7', 'display_stop']
