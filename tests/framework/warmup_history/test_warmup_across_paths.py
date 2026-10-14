"""
Warmup Across Paths — a backtest, a mock session and a live-adapter session start from the SAME
history (#588).

The pin the warmup never had. One tick stream feeds all three paths: the backtest and the mock
session read it as an archive rendered by the importer, through the batch's own selection; the
live session reads it as Kraken's OHLC endpoint would answer at the instant of its boot read,
with only the HTTP call replaced. After the first tick — once the live path has closed its seam —
every timeframe must hold the same bars in every path, each of them complete at that tick, and
the first bar the session renders must follow the history without repeating it.

Twelve warmup incidents since 2025-09 and not one of them was found by a test; the paths were
compared with themselves, never with each other.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.autotrader_warmup_preparator import AutotraderWarmupPreparator
from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.bars.warmup_history import WarmupHistory
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.process.tick_pipeline_core import render_bars_for_tick
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.utils.bar_period import is_complete_at, open_time_of, period_end, period_start
from tests.framework.warmup_history.warmup_history_builders import (
    SYMBOL,
    RequirementWorker,
    VenueStub,
    archive_package,
    history_rows,
    session_config,
    ticks_of,
)

UTC = timezone.utc
REQUIRED = {'M1': 30, 'M5': 12, 'H1': 6, 'D1': 1}


def _controller() -> BarRenderingController:
    controller = BarRenderingController(logger=MagicMock())
    controller.register_workers([RequirementWorker(REQUIRED)])
    return controller


def _backtest(preparator, start: datetime, first_tick: datetime) -> BarRenderingController:
    """The simulation subprocess: the package's history, through WarmupHistory."""
    controller = _controller()
    package = archive_package(preparator, REQUIRED, start, first_tick)
    WarmupHistory.from_package(SYMBOL, package, MagicMock()).inject_into(controller)
    return controller


def _mock(preparator, start: datetime, first_tick: datetime) -> BarRenderingController:
    """A mock AutoTrader session: the same package, through the AutoTrader's preparator."""
    controller = _controller()
    AutotraderWarmupPreparator(MagicMock()).prepare_and_inject(
        config=session_config('mock'), workers=[RequirementWorker(REQUIRED)],
        bar_controller=controller, package=archive_package(preparator, REQUIRED, start, first_tick))
    return controller


def _live(venue: VenueStub, read_at: datetime) -> BarRenderingController:
    """A live-adapter session: the venue read at boot, through the AutoTrader's preparator."""
    controller = _controller()
    venue.now = read_at
    AutotraderWarmupPreparator(MagicMock(), clock=lambda: read_at).prepare_and_inject(
        config=session_config('live'), workers=[RequirementWorker(REQUIRED)],
        bar_controller=controller, connection_policy=ConnectionPolicy())
    return controller


@pytest.fixture
def venue(stream, monkeypatch) -> VenueStub:
    """Kraken's OHLC endpoint over the stream — the only thing replaced on the live path."""
    stub = VenueStub(stream)
    monkeypatch.setattr(KrakenOhlcBarFetcher, '_fetch_ohlc', stub.answer)
    return stub


def _start_all(preparator, venue, stream, start: datetime, read_at: datetime):
    """Build the three paths for one start and feed each its first tick."""
    first = ticks_of(stream, start)[0]
    paths = {
        'backtest': _backtest(preparator, start, first.timestamp),
        'mock': _mock(preparator, start, first.timestamp),
        'live': _live(venue, read_at),
    }
    # The venue's clock when the first trade has arrived and the seam is read
    venue.now = first.timestamp + timedelta(seconds=1)
    for controller in paths.values():
        render_bars_for_tick(first, controller)
    return paths, first


def _assert_one_history(paths, first_tick: datetime) -> None:
    """Every path holds the same bars per timeframe, each complete at the first tick."""
    for timeframe, required in REQUIRED.items():
        backtest = paths['backtest'].get_bar_history(SYMBOL, timeframe)
        assert len(backtest) == required, f'{timeframe}: {len(backtest)}/{required} bars'
        unfinished = [bar.timestamp for bar in backtest
                      if not is_complete_at(open_time_of(bar), timeframe, first_tick)]
        assert not unfinished, f'{timeframe}: bars not complete at {first_tick}: {unfinished}'
        for name in ('mock', 'live'):
            other = paths[name].get_bar_history(SYMBOL, timeframe)
            assert history_rows(other) == history_rows(backtest), (
                f"{timeframe}: the {name} history differs from the backtest's\n"
                f'  backtest {[bar.timestamp for bar in backtest][-3:]}\n'
                f'  {name:8} {[bar.timestamp for bar in other][-3:]}')


def _assert_seamless(paths, first_tick: datetime) -> None:
    """The first rendered bar opens where the history ends — no repeat, no hole."""
    for name, controller in paths.items():
        for timeframe in REQUIRED:
            newest = controller.get_bar_history(SYMBOL, timeframe)[-1]
            current = controller.get_current_bar(SYMBOL, timeframe)
            assert current.timestamp == period_start(first_tick, timeframe).isoformat()
            assert period_end(open_time_of(newest), timeframe) <= open_time_of(current), (
                f'{name} {timeframe}: history ends after the first rendered bar opens')


class TestOneHistoryForEveryPath:
    """The same start gives the same history in the backtest, the mock and the live session."""

    def test_a_start_inside_a_bar(self, preparator, venue, stream):
        """13:02:30 lies inside an M5, an H1 and a D1 bar; none of the three may be taken."""
        start = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
        paths, first = _start_all(preparator, venue, stream, start, read_at=start)

        _assert_one_history(paths, first.timestamp)
        _assert_seamless(paths, first.timestamp)
        assert venue.calls == [start] * len(REQUIRED), (
            'no period ended between the read and the first tick, so nothing is read again')

    def test_a_period_ending_between_the_read_and_the_first_tick(self, preparator, venue, stream):
        """
        The live read happens at boot, the first trade arrives later: here an M1 and an M5
        period end in between, and the live path reads exactly those timeframes again.
        """
        start = datetime(2026, 8, 5, 13, 4, 58, tzinfo=UTC)
        paths, first = _start_all(preparator, venue, stream, start, read_at=start)

        assert first.timestamp > datetime(2026, 8, 5, 13, 5, tzinfo=UTC)
        _assert_one_history(paths, first.timestamp)
        _assert_seamless(paths, first.timestamp)
        assert len(venue.calls) == len(REQUIRED) + 2, 'M1 and M5 read again, H1 and D1 not'

    def test_midnight_between_the_read_and_the_first_tick(self, preparator, venue, stream):
        """Read at 23:59:59, first trade at 00:00:02: the day that just ended is in the history."""
        start = datetime(2026, 8, 5, 23, 59, 59, tzinfo=UTC)
        paths, first = _start_all(preparator, venue, stream, start, read_at=start)

        assert first.timestamp == datetime(2026, 8, 6, 0, 0, 2, tzinfo=UTC)
        _assert_one_history(paths, first.timestamp)
        _assert_seamless(paths, first.timestamp)
        assert paths['live'].get_bar_history(SYMBOL, 'D1')[-1].timestamp == (
            '2026-08-05T00:00:00+00:00')

    def test_the_histories_stay_one_after_the_first_rendered_bar_closes(
            self, preparator, venue, stream):
        """Ten minutes on, the first bar each path rendered itself has joined the history alike."""
        start = datetime(2026, 8, 5, 13, 4, 58, tzinfo=UTC)
        paths, first = _start_all(preparator, venue, stream, start, read_at=start)
        later = [tick for tick in ticks_of(stream, start)[1:]
                 if tick.timestamp < first.timestamp + timedelta(minutes=10)]
        for controller in paths.values():
            for tick in later:
                render_bars_for_tick(tick, controller)

        for timeframe in ('M1', 'M5'):
            histories = {name: history_rows(controller.get_bar_history(SYMBOL, timeframe))
                         for name, controller in paths.items()}
            assert histories['mock'] == histories['backtest']
            assert histories['live'] == histories['backtest']


class TestTheSeamRefusesRatherThanGuesses:
    """Where the live path cannot close its seam, it stops before the first decision."""

    def test_a_period_the_venue_has_not_committed_yet(self, preparator, venue, stream):
        """Kraken commits a period within about two seconds of its end; a first trade inside."""
        start = datetime(2026, 8, 5, 13, 4, 58, tzinfo=UTC)
        first = ticks_of(stream, start)[0]
        controller = _live(venue, read_at=start)
        venue.now = first.timestamp
        venue.committed_through = datetime(2026, 8, 5, 13, 3, tzinfo=UTC)

        with pytest.raises(WarmupHistoryRefusedError, match='not yet committed'):
            render_bars_for_tick(first, controller)

    def test_a_venue_that_cannot_be_read_again(self, preparator, venue, stream):
        start = datetime(2026, 8, 5, 13, 4, 58, tzinfo=UTC)
        first = ticks_of(stream, start)[0]
        controller = _live(venue, read_at=start)
        venue.now = first.timestamp
        venue.failing = True

        with pytest.raises(WarmupHistoryRefusedError, match='reading the venue again failed'):
            render_bars_for_tick(first, controller)
