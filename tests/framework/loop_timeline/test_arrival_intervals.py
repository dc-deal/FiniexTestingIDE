"""
FiniexTestingIDE - Arrival Interval Tests (#460, #450)

The backtest's profiling measures the time the market left between two arrivals: one interval per
slice, from its arrival stamp. The ticks that arrived in one millisecond are one arrival, and the
algo computes once on them, so a burst no longer drives the P5 interval to 0 ms — the interval a
budget advisory holds the time of one algo pass against.
"""

from datetime import datetime, timezone
from typing import List

from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.process_data_types import ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_performance_types import ProfilingData
from tests.shared.booking_day_harness import SYMBOL, simulation_result

# 2026-04-01 10:00 UTC
_START_MSC = 1_775_037_600_000


def _tick(arrival_offset: int) -> TickData:
    """
    A tick that arrived at an offset from the start, stamped by the venue 20 ms before.

    Args:
        arrival_offset: Its arrival, in ms after the start

    Returns:
        The tick
    """
    venue = _START_MSC + arrival_offset - 20
    return TickData(timestamp=datetime.fromtimestamp(venue / 1000, tz=timezone.utc), symbol=SYMBOL,
                    bid=60_000.0, ask=60_001.0, volume=1.0, time_msc=venue,
                    collected_msc=_START_MSC + arrival_offset)


def _run(arrivals: List[int]) -> ProcessTickLoopResult:
    """
    A profiled backtest over ticks that arrived at the offsets given.

    Args:
        arrivals: Each tick's arrival, in ms after the start

    Returns:
        What the loop returns
    """
    return simulation_result([_tick(offset) for offset in arrivals], spot_mode=False,
                             tick_loop_profiling=True)


class TestOneIntervalPerArrival:

    def test_the_ticks_of_one_millisecond_count_once(self):
        # Three ticks at +0, two at +5, one at +20: three arrivals, two gaps
        result = _run([0, 0, 0, 5, 5, 20])

        assert result.profiling_data.inter_tick_intervals_ms == [5.0, 15.0]

    def test_a_burst_leaves_the_p5_above_zero(self):
        # Counted per tick, six of these nine gaps were 0 ms and the P5 was 0
        data = _run([0, 0, 0, 10, 10, 10, 20, 20, 30, 30]).profiling_data
        profiling = ProfilingData.from_dicts(
            data.profile_times, data.profile_counts,
            inter_tick_intervals_ms=data.inter_tick_intervals_ms,
            gap_threshold_s=data.gap_threshold_s)

        assert profiling.interval_stats.p5_ms == 10.0
