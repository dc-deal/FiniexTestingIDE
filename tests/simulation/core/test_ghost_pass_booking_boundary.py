"""
The trading-day boundary inside the simulation's ghost passes (#537 / #539 audit).

A quiet stretch between two data ticks is crossed by GHOST PASSES, and they are the only thing
that advances the canonical clock there. So the boundary has to be checked at each ghost
instant — checked once before them it runs on the clock the PREVIOUS tick already checked,
which is no check at all, and a fill resolved in a ghost pass after a rollover is then booked
into the day before it.

These pin the call, not the seal: whether a day flip seals is `BookingSegmentRecorder`'s own
question and is tested there. What can only be seen here is WHEN the loop asks.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from python.framework.process.process_tick_loop import _run_sim_heartbeats

_START_MSC = 1_758_000_000_000    # an arbitrary instant; only the deltas matter


def _config(interval_ms: int = 1000, gap_threshold_s: float = 3600.0):
    config = MagicMock()
    config.heartbeat_interval_ms = interval_ms
    config.inter_tick_gap_threshold_s = gap_threshold_s
    return config


def _executor():
    """An executor whose clock actually moves, so the recorded instants are the ghost ones."""
    executor = MagicMock()
    state = {}

    def _set(now):
        state['now'] = now

    executor.set_current_time.side_effect = _set
    executor.get_current_time.side_effect = lambda: state.get('now')
    executor.is_session_end_requested.return_value = False
    return executor


def _run(booking, gap_ms: int = 5000, interval_ms: int = 1000):
    decision_logic = MagicMock()
    decision_logic.wants_heartbeat.return_value = True
    return _run_sim_heartbeats(
        _START_MSC, _START_MSC + gap_ms, _config(interval_ms), _executor(),
        MagicMock(), decision_logic, None, booking, lambda: ([], MagicMock()))


class TestTheBoundaryIsAskedAtEveryGhostInstant:

    def test_one_check_per_ghost_pass(self):
        booking = MagicMock()
        _run(booking, gap_ms=5000, interval_ms=1000)
        # Ghost passes fire strictly inside the gap: +1s, +2s, +3s, +4s.
        assert booking.check_boundary.call_count == 4

    def test_each_check_carries_that_ghost_s_own_instant(self):
        booking = MagicMock()
        _run(booking, gap_ms=3000, interval_ms=1000)
        seen = [call.args[0] for call in booking.check_boundary.call_args_list]
        assert seen == [
            datetime.fromtimestamp((_START_MSC + k * 1000) / 1000.0, tz=timezone.utc)
            for k in (1, 2)
        ]

    def test_the_check_precedes_the_resolutions_of_its_instant(self):
        """
        The boundary is the CUT: what closed earlier belongs to the day that is ending, and a
        fill resolving AT this instant belongs to the day that is opening — the period window
        is end-exclusive, so the order of these two calls decides which day a fill lands in.
        """
        order = []
        booking = MagicMock()
        booking.check_boundary.side_effect = lambda *_: order.append('boundary')
        executor = _executor()
        executor.heartbeat.side_effect = lambda: order.append('resolve')
        decision_logic = MagicMock()
        decision_logic.wants_heartbeat.return_value = True

        _run_sim_heartbeats(
            _START_MSC, _START_MSC + 3000, _config(), executor,
            MagicMock(), decision_logic, None, booking, lambda: ([], MagicMock()))

        assert order == ['boundary', 'resolve', 'boundary', 'resolve']

    def test_a_gap_too_short_for_a_ghost_asks_nothing(self):
        # No ghost pass, no clock movement, nothing to check — the tick's own call does it.
        booking = MagicMock()
        _run(booking, gap_ms=500, interval_ms=1000)
        booking.check_boundary.assert_not_called()

    def test_a_gap_past_the_correctness_threshold_asks_nothing(self):
        # #208: across a data or weekend gap the market says nothing, so no ghost is
        # synthesized there — and with no ghost there is no instant to check.
        booking = MagicMock()
        decision_logic = MagicMock()
        decision_logic.wants_heartbeat.return_value = True
        _run_sim_heartbeats(
            _START_MSC, _START_MSC + 7_200_000, _config(gap_threshold_s=3600.0), _executor(),
            MagicMock(), decision_logic, None, booking, lambda: ([], MagicMock()))
        booking.check_boundary.assert_not_called()
