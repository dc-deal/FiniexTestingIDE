"""
Sim/Live Parity — where a trading day ends, and what belongs to it

A trading day ends at its boundary instant, BEFORE the first tick at or after it is applied
(#476). The seal reads the account as the last tick of the day left it and is stamped with the
boundary; the tick that crosses it — its fills, its trades, its valuation — opens the new day.
Before, both pipelines sealed AFTER that tick, so a gap on it was booked into the day that was
ending, and the daily loss limit, which rolled on the same tick, counted it against neither day.

Pinned as parity: the same ticks across midnight UTC, a position held through it, a price gap
on the boundary tick, both pipelines and both account models — the periods agree field by
field, the gap is the new day's in both, each closing value lies inside its own band, and the
AutoTrader's daily loss limit cuts the day where its booking period does. And the same for the
boundaries no tick lands on: one passed in the quiet between two ticks, two crossed by one gap,
and a stop the boundary tick breaches — and a fill that lands between the day's last tick and its
boundary, which the daily loss limit books to the day that ends.
"""

from datetime import datetime, timedelta
from typing import List, Optional

import pytest

import python.framework.autotrader.autotrader_tick_loop as tick_loop_module
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.run_results_types import BookingPeriod, PeriodCloseReason
from tests.shared.booking_day_harness import (
    GAP,
    LOTS,
    MIDNIGHT,
    autotrader_loop,
    autotrader_periods,
    simulation_periods,
    ticks_across_midnight,
)

ACCOUNT_MODELS = pytest.mark.parametrize('spot_mode', [False, True], ids=['margin', 'spot'])


def _assert_books_agree(sim_periods: List[BookingPeriod], at_periods: List[BookingPeriod]) -> None:
    """
    Both pipelines' periods agree field by field.

    Args:
        sim_periods: The simulation's periods
        at_periods: The AutoTrader's periods over the same ticks
    """
    assert [p.reason for p in at_periods] == [p.reason for p in sim_periods]
    for sim, live in zip(sim_periods, at_periods):
        assert live.period_no == sim.period_no
        assert live.opened_at == sim.opened_at
        assert live.trading_day == sim.trading_day
        assert live.trade_count == sim.trade_count
        assert live.tick_count == sim.tick_count
        assert live.figures.net_pnl == pytest.approx(sim.figures.net_pnl, abs=1e-6)
        assert live.figures.final_equity == pytest.approx(sim.figures.final_equity, abs=1e-6)
        assert live.period_opening_equity == pytest.approx(sim.period_opening_equity, abs=1e-6)
        assert live.period_max_equity == pytest.approx(sim.period_max_equity, abs=1e-6)
        assert live.period_min_equity == pytest.approx(sim.period_min_equity, abs=1e-6)
        assert live.period_max_drawdown == pytest.approx(sim.period_max_drawdown, abs=1e-6)
        # The curve's running figures the seal reads — sampled before the seal in both
        assert live.figures.max_equity == pytest.approx(sim.figures.max_equity, abs=1e-6)
        assert live.figures.account_max_drawdown == pytest.approx(
            sim.figures.account_max_drawdown, abs=1e-6)
        assert live.closed_at == sim.closed_at


@ACCOUNT_MODELS
def test_both_pipelines_book_the_day_the_same_way(spot_mode):
    """The trading day's period and the one after it agree field by field."""
    ticks = ticks_across_midnight()
    sim_periods = simulation_periods(ticks, spot_mode)
    at_periods = autotrader_periods(ticks, spot_mode)

    assert [p.reason for p in sim_periods] == [
        PeriodCloseReason.ANCHOR, PeriodCloseReason.SESSION_END]
    _assert_books_agree(sim_periods, at_periods)
    assert at_periods[0].closed_at == sim_periods[0].closed_at == MIDNIGHT


@ACCOUNT_MODELS
def test_the_gap_on_the_boundary_tick_belongs_to_the_new_day(spot_mode):
    """
    The day that ended closes at the value its last tick left and never saw the gap; the new day
    opens at that value and carries the gap in its band and its deepest dip.
    """
    ticks = ticks_across_midnight()
    for periods in (simulation_periods(ticks, spot_mode), autotrader_periods(ticks, spot_mode)):
        ended, opened = periods
        assert ended.closed_at == opened.opened_at == MIDNIGHT
        for period in periods:
            closing = period.figures.final_equity
            assert period.period_min_equity <= closing <= period.period_max_equity
        # 0.01 BTC through a 2,000 USD gap: 20 USD, all of it after midnight
        assert ended.period_max_drawdown < GAP * LOTS
        assert opened.period_opening_equity == pytest.approx(ended.figures.final_equity)
        assert opened.period_max_drawdown >= GAP * LOTS


@ACCOUNT_MODELS
def test_the_daily_loss_limit_cuts_the_day_where_the_booking_period_does(spot_mode):
    """
    The daily loss limit's day rolls on the booking period's seal, from the value it read: the
    gap on the boundary tick is the new day's loss in both records. Before, the limit closed its
    day before the tick and took the new baseline after it, so the gap was in no day's record and
    was never checked against a daily limit.
    """
    ticks = ticks_across_midnight()
    loop = autotrader_loop(ticks, spot_mode)
    loop.run()
    periods = loop.get_booking_periods()
    days = loop.get_safety_session().days

    assert [day.day for day in days] == [p.trading_day.isoformat() for p in periods]
    ended, opened = days
    assert ended.worst_loss_abs < GAP * LOTS
    assert opened.worst_loss_abs >= GAP * LOTS
    assert opened.baseline.value == pytest.approx(periods[0].figures.final_equity)
    assert opened.baseline.taken_at_utc == MIDNIGHT.isoformat()


@ACCOUNT_MODELS
def test_a_boundary_between_two_ticks_is_cut_at_its_instant(spot_mode):
    """
    No tick lands within five seconds of midnight. The day still closes AT midnight in both
    pipelines — not at the tick that noticed it — and that tick, with its gap, opens the new day.
    """
    ticks = ticks_across_midnight(quiet_seconds=5)
    sim_periods = simulation_periods(ticks, spot_mode)
    at_periods = autotrader_periods(ticks, spot_mode)

    _assert_books_agree(sim_periods, at_periods)
    ended, opened = at_periods
    assert ended.closed_at == opened.opened_at == MIDNIGHT
    assert ended.period_max_drawdown < GAP * LOTS
    assert opened.period_max_drawdown >= GAP * LOTS


@ACCOUNT_MODELS
def test_every_trading_day_a_gap_crosses_gets_its_period(spot_mode):
    """
    The feed falls silent for a day: the first tick after it crosses two boundaries. The day it
    heard nothing from is booked in both pipelines — no tick, no trade, the value the seal before
    it read, a band that never moved — and the daily loss limit keeps a day for it too.
    """
    ticks = ticks_across_midnight(days_skipped=1)
    sim_periods = simulation_periods(ticks, spot_mode)
    loop = autotrader_loop(ticks, spot_mode)
    loop.run()
    at_periods = loop.get_booking_periods()

    _assert_books_agree(sim_periods, at_periods)
    first, silent, _ = at_periods
    assert [first.closed_at, silent.closed_at] == [MIDNIGHT, MIDNIGHT + timedelta(days=1)]
    assert (silent.tick_count, silent.trade_count) == (0, 0)
    assert silent.figures.final_equity == pytest.approx(first.figures.final_equity)
    assert silent.period_min_equity == pytest.approx(first.figures.final_equity)
    assert silent.period_max_equity == pytest.approx(first.figures.final_equity)
    days = loop.get_safety_session().days
    assert [day.day for day in days] == [p.trading_day.isoformat() for p in at_periods]
    assert days[1].worst_loss_abs == 0.0


@ACCOUNT_MODELS
def test_a_stop_the_boundary_tick_breaches_closes_in_the_new_day(spot_mode):
    """
    The boundary tick's gap breaches the position's stop, and the stop is what closes it. The
    close is the new day's in both pipelines — the simulation fills it on that tick, the
    AutoTrader a round trip later — and the day that ended books no trade. Where it fills is not
    compared: the simulation fills a breached level AT the level, the AutoTrader at the market.
    """
    ticks = ticks_across_midnight()
    before = [tick for tick in ticks if tick.timestamp < MIDNIGHT][-1]
    stop = before.bid - GAP / 2
    for periods in (simulation_periods(ticks, spot_mode, stop_loss=stop),
                    autotrader_periods(ticks, spot_mode, stop_loss=stop)):
        ended, opened = periods
        assert ended.closed_at == MIDNIGHT
        assert (ended.trade_count, opened.trade_count) == (0, 1)


@ACCOUNT_MODELS
def test_a_fill_between_the_last_tick_and_the_boundary_is_the_ending_day_s_loss(
        spot_mode, monkeypatch):
    """
    The close goes out on the last tick before midnight and fills on an idle heartbeat before it.
    The account changes where no tick evaluates the daily limit, and the next tick would already
    belong to the new day — so the seal's reading is the ending day's last instant: the day files
    the loss it shows, stamped with the boundary, and the new day starts after it.
    """
    # The wall clock an idle heartbeat reads, by how many heartbeats have run: eight before
    # midnight, while the close fills, then one after it, which seals the day
    instants = ([MIDNIGHT - timedelta(milliseconds=900 - 100 * k) for k in range(8)]
                + [MIDNIGHT + timedelta(seconds=5)])
    heartbeats = [0]
    real_heartbeat = LiveTradeExecutor.heartbeat

    def heartbeat(executor: LiveTradeExecutor) -> None:
        real_heartbeat(executor)
        heartbeats[0] += 1

    class _WallClock(datetime):
        @classmethod
        def now(cls, tz: Optional[object] = None) -> datetime:
            return instants[min(heartbeats[0], len(instants) - 1)]

    monkeypatch.setattr(LiveTradeExecutor, 'heartbeat', heartbeat)
    before_midnight = [tick for tick in ticks_across_midnight() if tick.timestamp < MIDNIGHT]
    loop = autotrader_loop(before_midnight, spot_mode, idle_heartbeats=len(instants),
                           hold_ticks=len(before_midnight) - 100)
    monkeypatch.setattr(tick_loop_module, 'datetime', _WallClock)
    loop.run()
    ended, _ = loop.get_booking_periods()
    ending_day, new_day = loop.get_safety_session().days

    assert (ended.closed_at, ended.trade_count) == (MIDNIGHT, 1), 'the close filled before it'
    loss_at_seal = ending_day.baseline.value - ended.figures.final_equity
    assert ending_day.worst_loss_abs == pytest.approx(loss_at_seal)
    assert ending_day.worst_loss_at == MIDNIGHT.isoformat()
    assert new_day.baseline.value == pytest.approx(ended.figures.final_equity)
