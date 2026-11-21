"""
A Mock Replay Puts Arrivals — Its Throttle and Its Outage Drill Fall Between Them (#461)

The replay puts the ticks of one arrival millisecond on the queue as one item. Its throttle sleeps
before each arrival, for all of its ticks at once, and the outage drill (#436) pauses at the first
arrival boundary once N ticks were put — never inside an arrival, which the loop would otherwise
read in two parts. The sleeps are recorded rather than slept.
"""

import queue
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from python.framework.autotrader.tick_sources import mock_tick_source
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.types.market_types.market_data_types import TickData

_START = datetime(2026, 4, 27, 6, 0, tzinfo=timezone.utc)
_START_MSC = round(_START.timestamp() * 1000)


def _ticks(*receipt_offsets_ms: int) -> List[TickData]:
    return [TickData(timestamp=_START + timedelta(milliseconds=offset), symbol='BTCUSD',
                     bid=50000.0, ask=50001.0, time_msc=_START_MSC + offset,
                     collected_msc=_START_MSC + offset) for offset in receipt_offsets_ms]


def _replay(monkeypatch, ticks: List[TickData], **options) -> Tuple[List[Tuple[float, int]], list]:
    """
    Run a replay with its sleeps recorded.

    Args:
        monkeypatch: The test's monkeypatch
        ticks: What the replay holds
        options: The source's throttle and drill options

    Returns:
        Each sleep with the number of items on the queue when it began, and every item put
    """
    lane: queue.Queue = queue.Queue()
    sleeps: List[Tuple[float, int]] = []
    monkeypatch.setattr(mock_tick_source.time, 'sleep',
                        lambda seconds: sleeps.append((seconds, lane.qsize())))
    MockTickSource(ticks=ticks, symbol='BTCUSD', tick_queue=lane, **options).start()
    return sleeps, [lane.get_nowait() for _ in range(lane.qsize())]


class TestTheThrottle:

    def test_it_sleeps_before_each_arrival_for_all_of_its_ticks(self, monkeypatch):
        sleeps, items = _replay(monkeypatch, _ticks(1, 1, 1, 2), tick_delay_ms=10)

        assert [round(seconds, 6) for seconds, _ in sleeps] == [0.03, 0.01]
        assert [len(item) for item in items[:-1]] == [3, 1]


class TestTheOutageDrill:

    def test_it_pauses_at_the_first_arrival_boundary_after_n_ticks(self, monkeypatch):
        """N = 2 falls inside a millisecond of three ticks; the pause waits until it is put."""
        sleeps, items = _replay(monkeypatch, _ticks(1, 1, 1, 2, 3),
                                freeze_after_ticks=2, freeze_duration_s=0.5)

        assert sleeps == [(0.5, 1)], 'once, after the first arrival of three ticks'
        assert [len(item) for item in items[:-1]] == [3, 1, 1]
        assert items[-1] is None

    def test_it_pauses_only_once(self, monkeypatch):
        sleeps, _ = _replay(monkeypatch, _ticks(1, 2, 3, 4),
                            freeze_after_ticks=1, freeze_duration_s=0.5)

        assert sleeps == [(0.5, 1)]
