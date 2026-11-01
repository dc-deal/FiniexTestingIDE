"""
The stop budget: what a session's stop may still spend before its supervisor kills it (#630).

Every case hands in the instant the stop was asked for, a moment back on the monotonic clock, so
nothing sleeps. A figure that depends on the clock is read to within a second, because the clock
moves on while the test runs.
"""

import time
from typing import Optional

import pytest

from python.framework.autotrader.stop_budget import StopBudget

_KILL_AFTER_S = 80.0
_RESERVE_S = 15.0
_TOLERANCE_S = 1.0


def _budget(elapsed_s: float, kill_after_s: Optional[float] = _KILL_AFTER_S) -> StopBudget:
    """
    A budget whose stop was asked for a given time ago.

    Args:
        elapsed_s: How long ago the stop was asked for
        kill_after_s: The declared kill, None for none

    Returns:
        The budget
    """
    return StopBudget(kill_after_s, _RESERVE_S, time.monotonic() - elapsed_s)


class TestNoKillDeclared:

    def test_the_time_to_the_kill_is_unknown_and_no_number(self):
        assert _budget(10.0, kill_after_s=None).until_kill_s() is None

    def test_there_is_time_for_anything(self):
        assert _budget(10_000.0, kill_after_s=None).has_time_for(1_000_000.0)

    def test_the_wait_keeps_its_own_bound_whatever_is_still_to_come(self):
        assert _budget(10_000.0, kill_after_s=None).order_wait_bound_s(30.0, 500.0) == 30.0

    def test_a_wait_past_its_own_bound_has_nothing_left(self):
        assert _budget(10.0, kill_after_s=None).order_wait_bound_s(-1.0, 0.0) == 0.0

    def test_the_time_since_the_stop_is_measured_all_the_same(self):
        elapsed_s = _budget(10.0, kill_after_s=None).elapsed_s()
        assert elapsed_s == pytest.approx(10.0, abs=_TOLERANCE_S)


class TestAKillDeclared:

    def test_the_kill_counts_down_from_the_instant_the_stop_was_asked_for(self):
        assert _budget(4.2).until_kill_s() == pytest.approx(75.8, abs=_TOLERANCE_S)

    def test_a_step_has_to_fit_in_front_of_the_reserve(self):
        budget = _budget(10.0)      # 70 s to the kill, 55 s of them in front of the reserve
        assert budget.has_time_for(50.0)
        assert not budget.has_time_for(60.0)

    def test_a_wanted_wait_that_fits_stays_as_it_is(self):
        assert _budget(0.0).order_wait_bound_s(30.0, 10.0) == 30.0

    def test_the_wait_leaves_the_reserve_and_what_is_still_to_come(self):
        # 40 s to the kill, less the reserve and 10 s still to come
        bound_s = _budget(40.0).order_wait_bound_s(30.0, 10.0)
        assert bound_s == pytest.approx(15.0, abs=_TOLERANCE_S)

    def test_a_bound_read_again_later_still_ends_the_wait_at_the_same_instant(self):
        # Read when the wait begins, the bound is 15 s. Ten seconds on the wait wants 10 s less
        # than it did and the kill leaves 10 s less: 5 s from then, the same end.
        wants_s = 30.0 - 10.0
        bound_s = _budget(50.0).order_wait_bound_s(wants_s, 10.0)
        assert bound_s == pytest.approx(5.0, abs=_TOLERANCE_S)


class TestPastTheKill:

    def test_nothing_is_left_until_it(self):
        assert _budget(100.0).until_kill_s() == 0.0

    def test_there_is_time_for_nothing(self):
        assert not _budget(100.0).has_time_for(0.0)

    def test_the_wait_is_over(self):
        assert _budget(100.0).order_wait_bound_s(30.0, 0.0) == 0.0

    def test_the_reserve_ends_the_budget_before_the_kill_does(self):
        budget = _budget(70.0)      # 10 s to the kill, all of them the reserve's
        assert budget.until_kill_s() > 0.0
        assert not budget.has_time_for(0.0)
        assert budget.order_wait_bound_s(30.0, 0.0) == 0.0
