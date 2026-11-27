"""
Loop Cadence — The Spot Dashboard's P&L Baseline (#461)

A spot session's dashboard measures its P&L against the account's full value at the session's
first price: the quote currency plus the base holding at that tick's mid. It is captured on the
session's first tick. Since #461 a slice can hand the loop several ticks at once — a backlog, a
Kraken frame — so the loop's tick count is no witness of the first tick; a baseline gated on it
was never captured when the first slice held more than one tick, and the dashboard fell back to the
quote balance alone, overstating the P&L by the whole base holding.
"""

import queue

from python.framework.types.autotrader_types.autotrader_display_types import AutoTraderDisplayStats
from tests.shared import booking_day_harness
from tests.shared.booking_day_harness import autotrader_loop, ticks_across_midnight

_USD = 10000.0
_BTC = 0.5


def _first_frame_baseline(frames: queue.Queue) -> float:
    """
    The P&L baseline of the first dashboard frame the session exported.

    Args:
        frames: The display queue the loop exported into

    Returns:
        The baseline the dashboard measures its P&L against
    """
    while True:
        frame = frames.get_nowait()
        if isinstance(frame, AutoTraderDisplayStats):
            return frame.core.initial_balance


def test_a_first_slice_of_several_ticks_still_captures_the_baseline(monkeypatch):
    monkeypatch.setattr(booking_day_harness, '_balances',
                        lambda spot_mode: {'USD': _USD, 'BTC': _BTC} if spot_mode else None)
    ticks = ticks_across_midnight()[:5]
    frames: queue.Queue = queue.Queue()
    # A live source: the five ticks waiting on the queue are the session's first slice
    loop = autotrader_loop(ticks, spot_mode=True, live_source=True, display_queue=frames)

    loop.run()

    assert _first_frame_baseline(frames) == _USD + _BTC * ticks[0].mid
