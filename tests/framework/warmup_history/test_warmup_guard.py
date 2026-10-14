"""
Warmup Guard — the one route into the renderer's history refuses what no correct selection
can produce (#588).

Two lines of defence, deliberately independent: the injection checks every bar against the
instant the history was selected for, and the renderer checks, at the first bar it renders
itself, that the history ends where that bar begins. Either alone catches the bar that contains
the start; the second also catches a history whose reference instant was wrong.
"""

from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.exceptions.warmup_history_errors import WarmupHistoryDefectError
from python.framework.types.market_types.market_data_types import Bar, TickData
from tests.framework.warmup_history.warmup_history_builders import SYMBOL, RequirementWorker

UTC = timezone.utc


def _bar(hour: int, timeframe: str = 'H1') -> Bar:
    return Bar(symbol=SYMBOL, timeframe=timeframe,
               timestamp=datetime(2026, 8, 5, hour, tzinfo=UTC).isoformat(),
               open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0, tick_count=1)


def _controller() -> BarRenderingController:
    controller = BarRenderingController(logger=MagicMock())
    controller.register_workers([RequirementWorker({'H1': 2})])
    return controller


def _tick(at: datetime) -> TickData:
    return TickData(timestamp=at, symbol=SYMBOL, bid=1.0, ask=1.0, volume=1.0, last=1.0)


AT_13 = datetime(2026, 8, 5, 13, tzinfo=UTC)


class TestTheInjectionRoute:

    def test_a_bar_not_complete_at_the_reference_is_refused(self):
        with pytest.raises(WarmupHistoryDefectError, match='not complete'):
            _controller().inject_warmup_bars(SYMBOL, {'H1': [_bar(11), _bar(12)]},
                                             AT_13 - timedelta(minutes=30))

    def test_a_repeated_period_is_refused(self):
        with pytest.raises(WarmupHistoryDefectError, match='does not follow'):
            _controller().inject_warmup_bars(SYMBOL, {'H1': [_bar(11), _bar(11)]}, AT_13)

    def test_periods_out_of_order_are_refused(self):
        with pytest.raises(WarmupHistoryDefectError, match='does not follow'):
            _controller().inject_warmup_bars(SYMBOL, {'H1': [_bar(12), _bar(11)]}, AT_13)

    def test_a_bar_of_another_timeframe_is_refused(self):
        with pytest.raises(WarmupHistoryDefectError, match='M5 bar'):
            _controller().inject_warmup_bars(SYMBOL, {'H1': [_bar(11, 'M5')]}, AT_13)

    def test_an_accepted_history_is_stamped_complete(self):
        bars = [_bar(11), _bar(12)]
        assert not any(bar.is_complete for bar in bars)
        controller = _controller()
        controller.inject_warmup_bars(SYMBOL, {'H1': bars}, AT_13)
        assert all(bar.is_complete for bar in controller.get_bar_history(SYMBOL, 'H1'))

    def test_injecting_again_replaces_the_history(self):
        """What the seam does when it re-reads a timeframe at the first tick."""
        controller = _controller()
        controller.inject_warmup_bars(SYMBOL, {'H1': [_bar(10), _bar(11)]}, AT_13)
        controller.inject_warmup_bars(SYMBOL, {'H1': [_bar(11), _bar(12)]}, AT_13)
        stamps = [bar.timestamp[11:16] for bar in controller.get_bar_history(SYMBOL, 'H1')]
        assert stamps == ['11:00', '12:00']


class TestTheFirstRenderedBar:

    def test_a_history_reaching_into_the_first_bar_is_refused(self):
        """A reference instant later than the first tick: each bar passes, the seam does not."""
        controller = _controller()
        controller.inject_warmup_bars(SYMBOL, {'H1': [_bar(11), _bar(12)]}, AT_13)
        with pytest.raises(WarmupHistoryDefectError, match='after the first bar'):
            controller.process_tick(_tick(datetime(2026, 8, 5, 12, 40, tzinfo=UTC)))

    def test_a_history_ending_where_the_first_bar_opens_is_accepted(self):
        controller = _controller()
        controller.inject_warmup_bars(SYMBOL, {'H1': [_bar(11), _bar(12)]}, AT_13)
        controller.process_tick(_tick(AT_13 + timedelta(seconds=2)))
        assert controller.get_current_bar(SYMBOL, 'H1').timestamp == AT_13.isoformat()


class TestTheSeamHook:

    def test_the_seam_closes_once_before_the_first_tick_is_rendered(self):
        controller = _controller()
        controller.inject_warmup_bars(SYMBOL, {'H1': [_bar(11), _bar(12)]}, AT_13)
        calls: List[tuple] = []
        controller.register_warmup_seam(
            lambda at: calls.append((at, controller.get_current_bar(SYMBOL, 'H1'))))

        first = AT_13 + timedelta(seconds=2)
        controller.process_tick(_tick(first))
        controller.process_tick(_tick(first + timedelta(seconds=5)))

        assert calls == [(first, None)], 'once, with the first tick, before anything rendered'
