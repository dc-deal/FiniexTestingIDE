"""
Warmup Record — a session writes down the history it started from (#588).

A live session's warmup comes from the venue and exists nowhere else afterwards, yet the parity
proof compares a backtest of the session's window with exactly that history. So the session
records it in its run directory (`io/warmup_history.json`): at injection, and again once the seam
at the first tick has closed — the bars as the workers saw them, and the instants that selected
them.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.autotrader.startup.autotrader_warmup_preparator import (
    AutotraderWarmupPreparator,
)
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.process.tick_pipeline_core import render_bars_for_tick
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from tests.framework.warmup_history.warmup_history_builders import (
    SYMBOL,
    RequirementWorker,
    VenueStub,
    archive_package,
    session_config,
    ticks_of,
)

UTC = timezone.utc
REQUIRED = {'M1': 30, 'M5': 12, 'H1': 6}


def _controller() -> BarRenderingController:
    controller = BarRenderingController(logger=MagicMock())
    controller.register_workers([RequirementWorker(REQUIRED)])
    return controller


@pytest.fixture
def venue(stream, monkeypatch) -> VenueStub:
    stub = VenueStub(stream)
    monkeypatch.setattr(KrakenOhlcBarFetcher, '_fetch_ohlc', stub.answer)
    return stub


def _read(path) -> dict:
    return json.loads(path.read_text())


def test_a_mock_session_records_its_history_and_its_first_tick(preparator, stream, tmp_path):
    start = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
    first = ticks_of(stream, start)[0]
    record_path = tmp_path / 'io' / 'warmup_history.json'
    controller = _controller()
    AutotraderWarmupPreparator(MagicMock()).prepare_and_inject(
        config=session_config('mock'), workers=[RequirementWorker(REQUIRED)],
        bar_controller=controller, package=archive_package(preparator, REQUIRED, start,
                                                           first.timestamp),
        run_writer=RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER))

    record = _read(record_path)
    assert record['source'] == 'archive'
    assert record['first_tick'] is None
    assert record['selected_for'] == first.timestamp.isoformat()
    for timeframe, required in REQUIRED.items():
        recorded = record['timeframes'][timeframe]
        history = controller.get_bar_history(SYMBOL, timeframe)
        assert recorded['required'] == required
        assert [row[0] for row in recorded['bars']] == [bar.timestamp for bar in history]
        assert [row[4] for row in recorded['bars']] == [bar.close for bar in history]

    render_bars_for_tick(first, controller)
    record = _read(record_path)
    assert record['first_tick'] == first.timestamp.isoformat()
    assert record['reread_at_first_tick'] == []


def test_a_live_session_records_the_history_after_the_seam(venue, stream, tmp_path):
    read_at = datetime(2026, 8, 5, 13, 4, 58, tzinfo=UTC)
    first = ticks_of(stream, read_at)[0]
    record_path = tmp_path / 'io' / 'warmup_history.json'
    controller = _controller()
    venue.now = read_at
    AutotraderWarmupPreparator(MagicMock(), clock=lambda: read_at).prepare_and_inject(
        config=session_config('live'), workers=[RequirementWorker(REQUIRED)],
        bar_controller=controller, connection_policy=ConnectionPolicy(),
        run_writer=RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER))

    at_boot = _read(record_path)
    assert at_boot['source'] == 'venue'
    assert at_boot['read_at'] == at_boot['selected_for'] == read_at.isoformat()
    assert at_boot['timeframes']['M5']['bars'][-1][0] == '2026-08-05T12:55:00+00:00'

    venue.now = first.timestamp + timedelta(seconds=1)
    render_bars_for_tick(first, controller)

    after_seam = _read(record_path)
    assert after_seam['read_at'] == read_at.isoformat()
    assert after_seam['selected_for'] == after_seam['first_tick'] == first.timestamp.isoformat()
    assert sorted(after_seam['reread_at_first_tick']) == ['M1', 'M5']
    assert after_seam['timeframes']['M5']['bars'][-1][0] == '2026-08-05T13:00:00+00:00'
    for timeframe in REQUIRED:
        assert [row[0] for row in after_seam['timeframes'][timeframe]['bars']] == [
            bar.timestamp for bar in controller.get_bar_history(SYMBOL, timeframe)]


def test_without_a_path_nothing_is_written(preparator, stream, tmp_path):
    start = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
    AutotraderWarmupPreparator(MagicMock()).prepare_and_inject(
        config=session_config('mock'), workers=[RequirementWorker(REQUIRED)],
        bar_controller=_controller(),
        package=archive_package(preparator, REQUIRED, start, start))
    assert not list(tmp_path.rglob('*.json'))
