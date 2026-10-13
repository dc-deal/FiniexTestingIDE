"""
Start Invariance — where a run starts must not change what it computes once it has caught up (#588).

The idea behind Freqtrade's lookahead and recursive analysis: run the same strategy from two
different starts and compare. A daily RSI started at midnight and the same RSI started at 13:00
the same day see different first days — the 13:00 run renders that day from 13:00 on — but a
close-based indicator reads that day's CLOSE, and the close is the same either way. So from the
first day both runs saw complete, every value must agree. On the code before #588 they did not:
the 13:00 run warmed up on the day it started in, read that day's 23:59 close at 13:00, and then
rendered the same day a second time.

The real batch selection, WarmupHistory, the bar controller and the CORE RSI worker; only the
data is synthetic.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.bars.warmup_history import WarmupHistory
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.process.tick_pipeline_core import render_bars_for_tick
from tests.framework.warmup_history.warmup_history_builders import (
    SYMBOL,
    ArchiveIndexStub,
    archive_package,
    render_archive,
    tick_frame,
    ticks_of,
)

UTC = timezone.utc
DAY = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture(scope='module')
def daily_stream():
    """Thirty days of trades, one every ten minutes."""
    return tick_frame(DAY - timedelta(days=20), DAY + timedelta(days=10), step_s=600)


@pytest.fixture(scope='module')
def daily_preparator(daily_stream, tmp_path_factory):
    instance = SharedDataPreparator(MagicMock())
    instance.bar_index_manager = ArchiveIndexStub(
        render_archive(daily_stream, tmp_path_factory.mktemp('daily'), ['D1']))
    return instance


def _rsi_from(start: datetime, stream, preparator):
    """
    One run: warm up for its first tick, then compute the daily RSI on every tick.

    Returns:
        RSI value per tick instant, for every tick of the run
    """
    worker = WorkerFactory(logger=MagicMock()).create_worker(
        'rsi_daily', 'CORE/rsi', {'periods': {'D1': 2}})
    controller = BarRenderingController(logger=MagicMock())
    controller.register_workers([worker])

    ticks = ticks_of(stream, start)
    package = archive_package(
        preparator, worker.get_warmup_requirements(), start, ticks[0].timestamp)
    WarmupHistory.from_package(SYMBOL, package, MagicMock()).inject_into(controller)

    values = {}
    for tick in ticks:
        current_bars = render_bars_for_tick(tick, controller)
        result = worker.compute(tick, controller.get_all_bar_history(SYMBOL), current_bars)
        values[tick.timestamp] = result.outputs['rsi_value']
    return values


def test_a_midnight_start_and_a_13h_start_agree_from_the_next_day_on(
        daily_stream, daily_preparator):
    midnight = _rsi_from(DAY, daily_stream, daily_preparator)
    afternoon = _rsi_from(DAY + timedelta(hours=13), daily_stream, daily_preparator)

    common = sorted(instant for instant in afternoon if instant >= DAY + timedelta(days=1))
    assert len(common) > 1000, 'the comparison must span days, not a handful of ticks'
    differing = [(instant, midnight[instant], afternoon[instant]) for instant in common
                 if midnight[instant] != afternoon[instant]]
    assert not differing, (
        f'{len(differing)} of {len(common)} values differ between the starts; '
        f'first: {differing[0]}')
