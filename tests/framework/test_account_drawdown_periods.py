"""
The five deepest drawdown periods of the account, not only the deepest (#529).

A period opens with the first sample below the running peak and closes with the first sample back
at or above it — a new peak is the boundary, so two periods never share a fall. The portfolio
keeps the five deepest, ranked by their share of the peak they fell from, the open one among them,
and carries them across a restart with the curve they belong to. Driven directly against the
PortfolioManager: equity handed in where the subject is the bookkeeping, a marked position or
holding where the subject is that both account models feed it the same way.
"""

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from python.framework.exceptions.algo_clock_errors import ClockNotInjectedError
from python.framework.trading_env.drawdown_period_ring import (
    DRAWDOWN_PERIODS_KEPT,
    DrawdownPeriodRing,
)
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.api.report_types import DrawdownPeriodRow
from python.framework.types.persistence_types import CarriedDrawdownPeriod
from python.framework.types.portfolio_types.portfolio_aggregation_types import DrawdownPeriod
from tests.framework.test_account_value import (
    _BALANCE,
    _ENTRY,
    _mark,
    _open_long,
    _portfolio,
)

_T0 = datetime(2026, 12, 3, 14, 0, tzinfo=timezone.utc)


class _Clock:
    """The canonical clock, one minute further at every read, and counting its reads."""

    def __init__(self):
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return _T0 + timedelta(minutes=self.reads)


def _walk(portfolio: PortfolioManager, *values: float) -> None:
    for value in values:
        portfolio.sample_equity(value)


def _periods(portfolio: PortfolioManager) -> List[DrawdownPeriod]:
    return portfolio.get_portfolio_statistics().account_drawdown_periods


class TestAPeriodRunsFromAPeakUntilItIsRecovered:

    def test_it_opens_below_the_peak_and_closes_back_at_it(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 9_800, 9_500, 9_700, 10_000)

        (period,) = _periods(portfolio)
        assert (period.peak, period.depth) == (10_000, 500)
        assert period.depth_pct == pytest.approx(5.0)
        assert period.started_at < period.trough_at < period.recovered_at

    def test_a_long_fall_and_rebound_is_one_period_not_many(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, *range(9_990, 9_000, -10), *range(9_000, 10_011, 10))

        (period,) = _periods(portfolio)
        assert period.depth == 1_000 and period.recovered_at is not None

    def test_a_new_peak_closes_it_and_the_next_decline_is_measured_from_there(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 9_000, 11_000, 10_450)

        deepest, latest = _periods(portfolio)
        assert (deepest.peak, deepest.depth, deepest.recovered_at is not None) == (10_000, 1_000, True)
        assert (latest.peak, latest.depth, latest.recovered_at) == (11_000, 550, None)

    def test_an_account_that_only_rose_has_none(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 10_100, 10_200, 10_200)

        assert _periods(portfolio) == []


class TestTheFiveDeepest:

    def test_five_are_kept_deepest_first_and_a_shallower_sixth_is_left_out(self):
        portfolio = _portfolio(clock_fn=_Clock())
        for depth in (300, 800, 100, 500, 700, 200):
            _walk(portfolio, 10_000, 10_000 - depth, 10_000)

        depths = [period.depth for period in _periods(portfolio)]
        assert depths == [800, 700, 500, 300, 200]
        assert len(depths) == DRAWDOWN_PERIODS_KEPT

    def test_a_deeper_one_later_displaces_the_shallowest(self):
        portfolio = _portfolio(clock_fn=_Clock())
        for depth in (100, 200, 300, 400, 500, 900):
            _walk(portfolio, 10_000, 10_000 - depth, 10_000)

        assert [period.depth for period in _periods(portfolio)] == [900, 500, 400, 300, 200]

    def test_ranked_by_share_of_the_peak_not_by_amount(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 9_000, 30_000, 28_500, 30_000)

        periods = _periods(portfolio)
        assert [p.depth_pct for p in periods] == pytest.approx([10.0, 5.0])
        assert [p.depth for p in periods] == [1_000, 1_500], (
            'a 1,000 fall from 10,000 outranks 1,500 from 30,000')

    def test_the_deepest_is_the_period_the_headline_measures(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 9_400, 12_000, 11_000, 12_500, 11_900)

        stats = portfolio.get_portfolio_statistics()
        assert stats.account_drawdown_periods[0].depth_pct == pytest.approx(
            stats.account_max_drawdown_pct)

    def test_the_open_one_is_ranked_among_them(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 9_700, 10_000, 9_000)

        deepest, other = _periods(portfolio)
        assert (deepest.depth, deepest.recovered_at) == (1_000, None)
        assert other.recovered_at is not None

    def test_a_period_with_no_share_ranks_behind_every_one_with_a_share(self):
        """A peak not above zero leaves no share; the depth still counts, and no share is made up."""
        no_share = DrawdownPeriod(peak=0.0, depth=900.0, depth_pct=None,
                                  started_at=None, trough_at=None)
        shallow = DrawdownPeriod(peak=10_000.0, depth=100.0, depth_pct=1.0,
                                 started_at=None, trough_at=None)
        ring = DrawdownPeriodRing()
        ring.admit(no_share)
        ring.admit(shallow)

        assert ring.get_kept() == [shallow, no_share]


class TestTheClock:

    def test_a_sample_that_neither_opens_deepens_nor_closes_reads_no_clock(self):
        clock = _Clock()
        portfolio = _portfolio(clock_fn=clock)
        _walk(portfolio, 10_000)          # the curve's start reads it once
        before = clock.reads
        _walk(portfolio, 10_100, 10_200, 10_200, 10_300)
        _walk(portfolio, 10_000, 10_100)  # opens once, then a shallower sample
        assert clock.reads == before + 1

    def test_a_clock_never_set_leaves_the_instants_unnamed(self):
        def unset() -> datetime:
            raise ClockNotInjectedError('the executor has not started')
        portfolio = _portfolio(clock_fn=unset)
        _walk(portfolio, 10_000, 9_500, 10_000)

        (period,) = _periods(portfolio)
        assert (period.started_at, period.trough_at, period.recovered_at) == (None, None, None)

    def test_a_portfolio_built_without_a_clock_still_refuses(self):
        portfolio = _portfolio(clock_fn=None)
        with pytest.raises(ClockNotInjectedError):
            _walk(portfolio, 10_000, 9_500)


class TestTheirDurations:
    """How long a period took to reach its trough, and to recover or to the instant it was reported."""

    def test_a_recovered_period_runs_from_its_start_to_its_trough_and_its_recovery(self):
        portfolio = _portfolio(clock_fn=_Clock())
        # The clock moves a minute at every read: the curve's start, the open, the deepening, the close
        _walk(portfolio, 10_000, 9_800, 9_500, 9_700, 10_000)

        (period,) = _periods(portfolio)
        assert (period.to_trough_s, period.duration_s) == (60.0, 120.0)

    def test_an_open_period_has_no_duration_yet(self):
        """It would run to an instant no field of the record names, so it could not be derived again."""
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 9_800, 9_700)

        (period,) = _periods(portfolio)
        assert (period.to_trough_s, period.duration_s, period.recovered_at) == (60.0, None, None)

    def test_a_clock_never_set_leaves_them_unmeasured(self):
        def unset() -> datetime:
            raise ClockNotInjectedError('the executor has not started')
        portfolio = _portfolio(clock_fn=unset)
        _walk(portfolio, 10_000, 9_500, 10_000)

        (period,) = _periods(portfolio)
        assert (period.to_trough_s, period.duration_s) == (None, None)

    def test_a_successor_replaying_an_earlier_window_measures_no_duration(self):
        """A mock session restarted over the same data recovers the carried period before its start."""
        first = _portfolio(clock_fn=_Clock())
        _walk(first, _BALANCE, _BALANCE - 600)
        carried = first.get_account_drawdown_carry_over()

        earlier = _portfolio(clock_fn=lambda: _T0 - timedelta(days=1))
        earlier.restore_drawdown_state(carried)
        _walk(earlier, _BALANCE)

        (period,) = _periods(earlier)
        assert period.recovered_at < period.started_at and period.duration_s is None


class TestTheReadingAStateFrameTakes:
    """`get_account_drawdown_pct`: a value against the peak the periods are measured from."""

    def test_the_share_below_the_running_peak(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000, 12_000, 11_400)

        assert portfolio.get_account_drawdown_pct(11_400) == pytest.approx(5.0)

    def test_a_value_above_the_peak_lies_nothing_below_it(self):
        """A frame written at a new high before the pass samples it."""
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000)

        assert portfolio.get_account_drawdown_pct(10_250) == 0.0

    def test_reading_it_moves_nothing(self):
        portfolio = _portfolio(clock_fn=_Clock())
        _walk(portfolio, 10_000)
        portfolio.get_account_drawdown_pct(15_000)

        assert portfolio.get_account_drawdown_pct(10_000) == 0.0
        assert _periods(portfolio) == []

    def test_no_value_or_no_curve_yet_reads_none(self):
        portfolio = _portfolio(clock_fn=_Clock())
        assert portfolio.get_account_drawdown_pct(9_000) is None, 'before the first valuation'
        _walk(portfolio, 10_000)
        assert portfolio.get_account_drawdown_pct(None) is None


@pytest.mark.parametrize('spot_mode', [False, True])
def test_both_account_models_feed_it_the_same_way(spot_mode):
    """A margin position and a spot holding move the account value; the periods follow it."""
    if spot_mode:
        portfolio = _portfolio(spot_mode=True, initial_balances={'USD': 5_000.0, 'EUR': 5_000.0})
    else:
        portfolio = _portfolio()
        _open_long(portfolio)

    for price in (_ENTRY + 0.0100, _ENTRY - 0.0050, _ENTRY + 0.0200, _ENTRY + 0.0150):
        _mark(portfolio, price)
        portfolio.sample_equity()

    deepest, open_one = _periods(portfolio)
    assert deepest.recovered_at is not None and open_one.recovered_at is None
    assert deepest.depth_pct > open_one.depth_pct
    assert None not in (deepest.to_trough_s, deepest.duration_s, open_one.to_trough_s), (
        'both account models measure how long a period took')
    assert open_one.duration_s is None


def test_a_close_can_recover_a_period():
    """`_update_statistics` samples the curve at every close, past `sample_equity`."""
    portfolio = _portfolio()
    _open_long(portfolio)
    portfolio.sample_equity()
    _mark(portfolio, _ENTRY - 0.0050)
    portfolio.sample_equity()

    portfolio.close_position_portfolio('p1', exit_price=_ENTRY + 0.0100, exit_tick_value=1.0,
                                       exit_tick_index=1)

    (period,) = _periods(portfolio)
    assert period.recovered_at is not None


def test_the_runtime_the_carried_and_the_served_period_hold_the_same_fields():
    """
    A period passes through three types — the runtime record, the carry-over and the served row —
    built from each other by their fields. The carried model ignores a key it does not declare, so
    a field added to the runtime record alone would vanish at every restart without a word.
    """
    runtime = {field.name for field in fields(DrawdownPeriod)}
    assert runtime == set(CarriedDrawdownPeriod.model_fields)
    assert runtime | {'rank'} == set(DrawdownPeriodRow.model_fields)


class TestTheyContinueAcrossARestart:

    @staticmethod
    def _first_session() -> PortfolioManager:
        first = _portfolio(clock_fn=_Clock())
        for depth in (300, 800):
            _walk(first, _BALANCE, _BALANCE - depth, _BALANCE)
        _walk(first, _BALANCE - 600)      # left open
        return first

    def test_the_kept_periods_and_the_open_one_come_back(self):
        carried = self._first_session().get_account_drawdown_carry_over()
        second = _portfolio(clock_fn=_Clock())
        second.restore_drawdown_state(carried)

        depths = [(period.depth, period.recovered_at is None) for period in _periods(second)]
        assert depths == [(800, False), (600, True), (300, False)]
        assert all(period.duration_s is not None
                   for period in _periods(second) if period.recovered_at is not None), (
            'a closed period keeps the durations it was carried with')

    def test_the_open_one_recovers_against_the_restored_peak(self):
        carried = self._first_session().get_account_drawdown_carry_over()
        second = _portfolio(clock_fn=_Clock())
        second.restore_drawdown_state(carried)
        _walk(second, _BALANCE - 900, _BALANCE + 50)

        assert [(period.depth, period.recovered_at is None) for period in _periods(second)] == [
            (900, False), (800, False), (300, False)]

    def test_a_record_written_before_them_starts_empty(self):
        carried = self._first_session().get_account_drawdown_carry_over().model_copy(
            update={'drawdown_periods': [], 'open_drawdown_period': None})
        second = _portfolio(clock_fn=_Clock())
        second.restore_drawdown_state(carried)

        assert _periods(second) == []

    def test_a_reset_forgets_them(self):
        portfolio = self._first_session()
        portfolio.reset()
        assert _periods(portfolio) == []
