"""
Warmup From a Record — a backtest of a session's window starts from the bars the session recorded
(#512).

A session writes the history it started from to `io/warmup_history.json`; the comparison reads it
back and feeds it to the backtest of the session's window. Read through the file's JSON and fed back
for the same first tick, it must give exactly the history the archive path gives. Fed back for a
first tick in a later period than it reaches, it must refuse rather than start with a hole: the bars
in between exist in neither the record nor the backtest.
"""

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.bars.warmup_history import WarmupHistory
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.loop.blocks.bar_render_block import render_bars_for_tick
from python.framework.types.market_types.warmup_history_types import (
    WarmupHistoryRecord,
    WarmupSource,
)
from tests.framework.warmup_history.warmup_history_builders import (
    SYMBOL,
    RequirementWorker,
    archive_package,
    history_rows,
    ticks_of,
)

UTC = timezone.utc
REQUIRED = {'M1': 30, 'M5': 12, 'H1': 6, 'D1': 1}
START = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)


def _controller() -> BarRenderingController:
    controller = BarRenderingController(logger=MagicMock())
    controller.register_workers([RequirementWorker(REQUIRED)])
    return controller


def _archive_history(preparator, first_tick: datetime) -> WarmupHistory:
    """The history the batch selects from the archive for this first tick."""
    package = archive_package(preparator, REQUIRED, START, first_tick)
    return WarmupHistory.from_package(SYMBOL, package, MagicMock(), required_by_timeframe=REQUIRED)


def _as_written(history: WarmupHistory) -> WarmupHistoryRecord:
    """The record as a run writes it and the comparison reads it back — through its JSON."""
    return WarmupHistoryRecord.from_dict(json.loads(json.dumps(asdict(history.build_record()))))


class TestTheRecordGivesTheArchiveHistory:
    """Fed back for the same first tick, the record is the archive's history, bar for bar."""

    def test_the_record_survives_its_json(self, preparator, stream):
        """What the reader parses equals what the writer built."""
        first = ticks_of(stream, START)[0]
        history = _archive_history(preparator, first.timestamp)
        assert _as_written(history) == history.build_record()

    def test_fed_back_it_holds_the_archive_history(self, preparator, stream):
        """After the first tick both renderers hold the same bars per timeframe."""
        first = ticks_of(stream, START)[0]
        archive = _archive_history(preparator, first.timestamp)
        backtest, recorded = _controller(), _controller()
        archive.inject_into(backtest)
        from_record = WarmupHistory.from_record(_as_written(archive), MagicMock())
        from_record.inject_into(recorded)
        for controller in (backtest, recorded):
            render_bars_for_tick(first, controller)

        assert from_record.get_source() is WarmupSource.RECORD
        for timeframe, required in REQUIRED.items():
            expected = backtest.get_bar_history(SYMBOL, timeframe)
            assert len(expected) == required
            assert history_rows(recorded.get_bar_history(SYMBOL, timeframe)) == history_rows(expected), (
                f'{timeframe}: the history fed back from the record differs from the archive path')


class TestTheRecordRefusesRatherThanGuesses:
    """A record that cannot supply the bars the backtest's start needs stops the run."""

    def test_a_first_tick_in_a_later_period(self, preparator, stream):
        """Ten minutes later the M1 and M5 periods in between are in neither the record nor the run."""
        first = ticks_of(stream, START)[0]
        record = _as_written(_archive_history(preparator, first.timestamp))
        later = ticks_of(stream, START + timedelta(minutes=10))[0]
        controller = _controller()
        WarmupHistory.from_record(record, MagicMock()).inject_into(controller)

        with pytest.raises(WarmupHistoryRefusedError, match='falls in a later period'):
            render_bars_for_tick(later, controller)

    def test_a_record_that_names_no_instant(self, preparator, stream):
        """A record with no selection instant recorded no history to start from."""
        first = ticks_of(stream, START)[0]
        record = replace(_as_written(_archive_history(preparator, first.timestamp)), selected_for='')

        with pytest.raises(WarmupHistoryRefusedError, match='names no instant'):
            WarmupHistory.from_record(record, MagicMock())

    def test_a_record_missing_a_key(self, preparator, stream):
        """A record written by another version of the writer is not read with a guess."""
        first = ticks_of(stream, START)[0]
        written = asdict(_archive_history(preparator, first.timestamp).build_record())
        del written['selected_for']

        with pytest.raises(ValueError, match='selected_for'):
            WarmupHistoryRecord.from_dict(written)
