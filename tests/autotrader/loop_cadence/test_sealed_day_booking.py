"""
A sealed trading day survives the session (#476).

At each trading day's seal the session hands every period sealed so far to its booker — which
rewrites its ledger fragment whole — and then writes the carry-over, so the period number reaches
the next session even when this one is killed. In that order: killed between the two, the next
session books the same number again, a visible duplicate, where the other order would leave a
number nobody booked. The session's end is booked by the report coordinator, not here.

A check that seals several days at once books and announces them once, and a boundary a
heartbeat notices is sealed before anything that pass resolves.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import python.framework.autotrader.autotrader_tick_loop as tick_loop_module
from python.framework.autotrader.session_log_rotator import SessionLogRotator
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertKind
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from python.framework.utils.run_id_utils import mint_run_id
from tests.shared.booking_day_harness import MIDNIGHT, autotrader_loop, ticks_across_midnight


def test_each_sealed_day_is_booked_when_it_seals():
    booked: List[List[BookingPeriod]] = []
    loop = autotrader_loop(ticks_across_midnight(), spot_mode=True,
                           book_sealed_periods=booked.append)
    loop.run()

    assert [[period.period_no for period in periods] for periods in booked] == [[1]]
    assert booked[0][0].reason is PeriodCloseReason.ANCHOR


def test_the_session_s_end_is_not_booked_at_a_seal():
    booked: List[List[BookingPeriod]] = []
    loop = autotrader_loop(ticks_across_midnight(), spot_mode=True,
                           book_sealed_periods=booked.append)
    loop.run()
    periods = loop.get_booking_periods()

    assert [period.reason for period in periods] == [
        PeriodCloseReason.ANCHOR, PeriodCloseReason.SESSION_END]
    assert len(booked) == 1


def test_the_carry_over_follows_the_ledger_at_the_seal():
    writes: List[Tuple[str, int]] = []

    def persist() -> bool:
        writes.append(('carry_over', loop.get_highest_period_no()))
        return True

    loop = autotrader_loop(
        ticks_across_midnight(), spot_mode=True,
        book_sealed_periods=lambda periods: writes.append(('ledger', periods[-1].period_no)),
        persist_carry_over=persist)
    loop.run()

    sealed = writes.index(('ledger', 1))
    assert writes[sealed + 1] == ('carry_over', 1)


def test_the_log_buffer_lets_go_of_a_sealed_day_s_ordinary_lines():
    """
    Every line is in the day's log file already; the end reads only warnings and errors from the
    buffer, so a thirty-day session holds those rather than every line it wrote.

    The logger shows INFO on its console, as an operator's session does: under the suite's own
    configuration a scenario logger shows only warnings, an INFO line never reaches the buffer, and
    the test would pass with nothing let go of.
    """
    logger = ScenarioLogger(scenario_set_name='parity', scenario_name='booking_buffer',
                            run_timestamp=datetime.now(tz=timezone.utc),
                            run_id=mint_run_id(datetime.now(timezone.utc)),
                            use_global_log_level_for_console=True)
    logger.info('an ordinary line before midnight')
    logger.warning('⚠️ a warning before midnight')
    assert any(record.message == 'an ordinary line before midnight'
               for record in logger.get_records()), (
        'the INFO line is not buffered, so its absence after the seal would prove nothing')
    loop = autotrader_loop(ticks_across_midnight(), spot_mode=True, logger=logger)
    loop.run()

    kept = logger.get_records()
    assert any(record.message == '⚠️ a warning before midnight' for record in kept)
    assert not any(record.message == 'an ordinary line before midnight' for record in kept), (
        'an INFO line of the sealed day is still in the buffer')



def test_days_one_check_seals_are_booked_and_announced_once():
    """
    A check two boundaries late — a feed silent for a day — seals both days in one pass. They
    are booked in one write, and the operator hears the first day in full and the rest in one
    message; per day, a session whose clock jumped a month would rewrite its fragment and send a
    message thirty times within one pass.
    """
    booked: List[List[BookingPeriod]] = []
    alerts = MagicMock()
    loop = autotrader_loop(ticks_across_midnight(days_skipped=1), spot_mode=True,
                           book_sealed_periods=booked.append, operator_alerts=alerts)
    loop.run()

    assert [[period.period_no for period in periods] for periods in booked] == [[1, 2]]
    titles = [call.args[2] for call in alerts.send.call_args_list
              if call.args[0] is OperatorAlertKind.DAY_CLOSED]
    assert len(titles) == 2
    assert titles[1].startswith('Days closed unheard')


class _WallClockAfterMidnight(datetime):
    """The wall clock an idle heartbeat reads: five seconds after midnight."""

    @classmethod
    def now(cls, tz: Optional[object] = None) -> datetime:
        return MIDNIGHT + timedelta(seconds=5)


def test_a_boundary_a_heartbeat_notices_is_sealed_before_the_pass_resolves(monkeypatch):
    """
    The feed is quiet across midnight and an idle heartbeat notices the boundary. The day is
    sealed AT midnight and booked; only then does the session log rotate — so the line that
    seals the day is in that day's file — and the heartbeat resolve what the venue answered, so
    a fill drained there belongs to the day it opened.
    """
    calls: List[str] = []
    real_heartbeat = LiveTradeExecutor.heartbeat
    real_rotation = SessionLogRotator.check_daily_rotation

    def heartbeat(executor: LiveTradeExecutor) -> None:
        calls.append('heartbeat')
        real_heartbeat(executor)

    def rotation(rotator: SessionLogRotator) -> None:
        calls.append('rotation')
        real_rotation(rotator)

    monkeypatch.setattr(LiveTradeExecutor, 'heartbeat', heartbeat)
    monkeypatch.setattr(SessionLogRotator, 'check_daily_rotation', rotation)
    monkeypatch.setattr(tick_loop_module, 'datetime', _WallClockAfterMidnight)

    before_midnight = [tick for tick in ticks_across_midnight() if tick.timestamp < MIDNIGHT]
    loop = autotrader_loop(before_midnight, spot_mode=True, idle_heartbeats=1,
                           book_sealed_periods=lambda periods: calls.append('booking'))
    loop.run()
    periods = loop.get_booking_periods()

    assert periods[0].reason is PeriodCloseReason.ANCHOR
    assert periods[0].closed_at == MIDNIGHT
    assert periods[1].tick_count == 0
    assert calls[calls.index('booking'):][:3] == ['booking', 'rotation', 'heartbeat']
