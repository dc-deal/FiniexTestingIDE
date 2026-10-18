"""
Spread Estimator Tests (#602).

A tick without a quote carries bid = ask = the traded price, so a backtest over it never crosses
a spread. The spread is estimated from the trades instead: a buy and a sell close together in
time straddle it. These tests hold the pure functions to that reading — a burst counts once,
only opposite sides close enough in time form a pair, and Roll's reference behaves as the
textbook says.
"""

import numpy as np
import pytest

from python.framework.discoveries.spread_estimate.spread_estimator import (
    MAX_PAIR_GAP_MS,
    collapse_bursts,
    effective_spread_bps,
    opposite_side_pairs,
    quoted_spread_bps,
    roll_spread_bps,
    utc_day_numbers,
)

T0 = 1_700_000_000_000


class TestABurstCountsOnce:

    def test_fills_of_one_order_in_one_millisecond_collapse_to_the_first(self):
        time_msc = np.array([T0, T0, T0, T0 + 5])
        buy = np.array([True, True, True, False])
        assert collapse_bursts(time_msc, buy).tolist() == [True, False, False, True]

    def test_opposite_sides_in_one_millisecond_are_two_trades(self):
        time_msc = np.array([T0, T0])
        buy = np.array([True, False])
        assert collapse_bursts(time_msc, buy).tolist() == [True, True]


class TestOppositeSidesStraddleTheSpread:

    def test_a_buy_then_a_sell_measures_the_spread_in_bps(self):
        times, spreads = opposite_side_pairs(
            np.array([T0, T0 + 200]), np.array([True, False]), np.array([100.01, 99.99]))
        assert times.tolist() == [T0 + 200]
        assert spreads[0] == pytest.approx(0.02 / 100.0 * 10_000)

    def test_same_side_trades_form_no_pair(self):
        _, spreads = opposite_side_pairs(
            np.array([T0, T0 + 200]), np.array([True, True]), np.array([100.01, 100.02]))
        assert len(spreads) == 0

    def test_trades_further_apart_than_the_gap_form_no_pair(self):
        _, spreads = opposite_side_pairs(
            np.array([T0, T0 + MAX_PAIR_GAP_MS + 1]), np.array([True, False]),
            np.array([100.01, 99.99]))
        assert len(spreads) == 0

    def test_a_single_trade_gives_nothing(self):
        times, spreads = opposite_side_pairs(np.array([T0]), np.array([True]), np.array([100.0]))
        assert len(times) == 0 and len(spreads) == 0


class TestRollIsTheReference:

    def test_trades_at_random_sides_of_a_fixed_quote_give_the_spread(self):
        # Roll's model: each trade at the bid or the ask, independently — then the serial
        # covariance of price changes is -(s/2)^2 and the estimate is the spread s
        sides = np.random.default_rng(7).random(20_000) < 0.5
        price = np.where(sides, 100.01, 99.99)
        assert roll_spread_bps(price) == pytest.approx(2.0, rel=0.05)

    def test_strictly_alternating_sides_break_its_assumption(self):
        # Not independent sides: the covariance is -s^2 and the estimate doubles — one reason
        # the estimate from paired trades is used instead
        price = np.array([100.01, 99.99] * 50)
        assert roll_spread_bps(price) == pytest.approx(4.0, rel=0.05)

    def test_a_trend_without_bounce_gives_no_estimate(self):
        assert roll_spread_bps(np.linspace(100.0, 101.0, 50)) is None

    def test_too_few_prices_give_no_estimate(self):
        assert roll_spread_bps(np.array([100.0, 100.01])) is None


class TestTheQuotedTruth:

    def test_the_quoted_spread_in_bps_of_the_midpoint(self):
        assert quoted_spread_bps(np.array([99.99]), np.array([100.01]))[0] == pytest.approx(2.0)

    def test_a_fill_at_the_ask_has_an_effective_spread_equal_to_the_quoted(self):
        effective = effective_spread_bps(np.array([100.01]), np.array([99.99]), np.array([100.01]))
        assert effective[0] == pytest.approx(2.0)

    def test_a_fill_through_the_ask_costs_more_than_the_quote(self):
        effective = effective_spread_bps(np.array([100.03]), np.array([99.99]), np.array([100.01]))
        assert effective[0] == pytest.approx(6.0)


def test_a_utc_day_starts_at_midnight():
    midnight = 1_700_006_400_000  # 2023-11-15 00:00:00 UTC
    assert utc_day_numbers(np.array([midnight - 1, midnight])).tolist() == [19675, 19676]
