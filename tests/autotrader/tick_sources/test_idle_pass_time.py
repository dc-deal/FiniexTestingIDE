"""
The time of a pass that carries no tick, asked of the tick source (#616).

A live feed has no time of its own between two ticks and answers the wall clock; a replay answers
its own time, so a pause in the replay never moves a mock session's clock to the day it runs.
"""

import queue
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional

from python.framework.autotrader.tick_sources import mock_tick_source
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.autotrader.tick_sources.mock_tick_source import MockTickSource
from python.framework.types.market_types.market_data_types import TickData

_REPLAY_START = datetime(2026, 4, 27, 6, 0, tzinfo=timezone.utc)


class _LiveSource(AbstractTickSource):
    """A source with no time of its own, as a venue feed is."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_symbol(self) -> str:
        return 'BTCUSD'

    def is_exhausted(self) -> bool:
        return False


def _ticks(count: int) -> List[TickData]:
    return [TickData(timestamp=_REPLAY_START + timedelta(seconds=index), symbol='BTCUSD',
                     bid=50000.0, ask=50001.0) for index in range(count)]


def _replay(ticks: List[TickData]) -> MockTickSource:
    return MockTickSource(ticks=ticks, symbol='BTCUSD', tick_queue=queue.Queue())


def _fake_monotonic(monkeypatch, readings: List[float]) -> None:
    """
    Replace the mock source's monotonic clock with fixed readings, one per call.

    Args:
        monkeypatch: The test's monkeypatch
        readings: The readings, handed out in order; the last one repeats
    """
    remaining = list(readings)

    def monotonic() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    monkeypatch.setattr(mock_tick_source, 'time', SimpleNamespace(
        monotonic=monotonic, sleep=time.sleep))


class TestALiveFeedAnswersTheWallClock:

    def test_the_answer_is_now_in_utc(self):
        before = datetime.now(timezone.utc)
        answer = _LiveSource().get_idle_pass_time()
        after = datetime.now(timezone.utc)

        assert answer.tzinfo is not None
        assert before <= answer <= after


class TestAReplayAnswersItsOwnTime:

    def test_before_its_first_tick_it_stands_on_that_tick(self):
        assert _replay(_ticks(3)).get_idle_pass_time() == _REPLAY_START

    def test_a_pause_counts_from_the_last_tick_put_on_the_queue(self, monkeypatch):
        """Three ticks put at monotonic 100.0; asked 2.5 s later, as during the outage drill."""
        source = _replay(_ticks(3))
        _fake_monotonic(monkeypatch, [100.0, 100.0, 100.0, 102.5])
        source.start()

        last_tick = _REPLAY_START + timedelta(seconds=2)
        assert source.get_idle_pass_time() == last_tick + timedelta(seconds=2.5)

    def test_the_answer_never_reaches_the_day_the_session_runs(self, monkeypatch):
        source = _replay(_ticks(3))
        _fake_monotonic(monkeypatch, [100.0, 100.0, 100.0, 160.0])
        source.start()

        answer = source.get_idle_pass_time()
        assert answer.date() == _REPLAY_START.date()
        assert answer < datetime.now(timezone.utc) - timedelta(days=1)

    def test_an_empty_replay_has_no_time_of_its_own(self):
        """It keeps the base's answer, the wall clock; such a session ends at its first pass."""
        before = datetime.now(timezone.utc)
        answer: Optional[datetime] = _replay([]).get_idle_pass_time()

        assert answer is not None and answer >= before
