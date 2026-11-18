"""
Loop Cadence — Where a Session Stamps What Arrives Between Its Ticks (#461)

A venue's answer and a timer are stamped on the session's receipt axis. Live that is the wall
clock. A replay's own time is the front of its source — the last tick it put on the queue — and a
loop that runs behind the replay stands earlier: stamped from the front, an answer would move the
clock past ticks still waiting, and every one of them would be clamped forward onto it. So a replay
stamps at the loop's own position, and a live session at the wall clock.

The loop is exercised through `__new__`: the method under test reads three collaborators, and
building a real event loop would drag in a queue, workers and a decision logic without testing any
of them.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from python.framework.autotrader.autotrader_event_loop import AutotraderEventLoop

_FRONT = datetime(2026, 1, 25, 12, 0, 0, tzinfo=timezone.utc)
_FRONT_MSC = round(_FRONT.timestamp() * 1000)
_BEHIND_MSC = _FRONT_MSC - 3_600_000


class _Intake:
    """Where the loop stands, as its intake answers it."""

    def __init__(self, position_msc):
        self._position_msc = position_msc

    def get_position_msc(self):
        return self._position_msc


def _loop(replay: bool, position_msc) -> AutotraderEventLoop:
    loop = AutotraderEventLoop.__new__(AutotraderEventLoop)
    loop._tick_source = MagicMock()
    loop._tick_source.get_idle_pass_time.return_value = _FRONT
    loop._replay = replay
    loop._intake = _Intake(position_msc)
    return loop


class TestAReplayStampsAtTheLoopsPosition:

    def test_a_loop_behind_its_replay_stamps_where_it_stands(self):
        """An hour of replayed ticks still waits: the answer joins them, it does not jump them."""
        assert _loop(replay=True, position_msc=_BEHIND_MSC)._axis_now_msc() == _BEHIND_MSC

    def test_a_loop_caught_up_with_its_replay_stamps_at_the_replays_time(self):
        assert _loop(replay=True, position_msc=_FRONT_MSC + 5)._axis_now_msc() == _FRONT_MSC

    def test_before_its_first_instant_a_replay_stamps_at_its_own_time(self):
        assert _loop(replay=True, position_msc=None)._axis_now_msc() == _FRONT_MSC


class TestALiveSessionStampsAtTheWallClock:

    def test_the_wall_clock_is_the_receipt_however_far_the_loop_is_behind(self):
        """Live, the source's answer is the wall clock, and when an answer landed IS its receipt."""
        assert _loop(replay=False, position_msc=_BEHIND_MSC)._axis_now_msc() == _FRONT_MSC
