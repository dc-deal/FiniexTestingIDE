"""
A real mock session records its state frames (#579 part A): a frame every minute of the replayed
tick time, one at every closed bar and at the order the window holds, at most one per pass, and a
last frame that agrees with the session's own portfolio report — valued at the same moment, at the
same price, with the same position rows.

The session runs once for the module; its canonical clock is the replayed tick time, so every frame
time here is reproducible.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.io.artifact_specs import PORTFOLIO_ARTIFACT
from python.framework.reporting.io.report_artifact_io import read_artifact
from python.framework.reporting.store.report_store import ReportStore
from python.framework.types.config_types.autotrader_defaults_config_types import StateFramesDefaults
from python.framework.types.log_layout_types import IO_SUBDIR, STATE_FRAMES_FILE
from tests.shared.fixture_helpers import remove_run_dir

PROFILE = 'configs/autotrader_profiles/mock/state_frames_test.json'


def _read(run_dir: Path):
    lines = [json.loads(line) for line in
             (run_dir / IO_SUBDIR / STATE_FRAMES_FILE).read_text(encoding='utf-8').splitlines()]
    return lines[0], lines[1:]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


@pytest.fixture(scope='module')
def session():
    """One session of the suite's profile, its run directory, and what it recorded."""
    trader = AutotraderMain(load_autotrader_config(PROFILE))
    result = trader.run()
    header, frames = _read(trader._run_dir)
    yield result, trader._run_dir, header, frames
    remove_run_dir(trader._run_dir)


class TestTheRecord:

    def test_opens_with_its_header(self, session):
        _, run_dir, header, _ = session
        assert header == {'record_kind': 'header', 'stream': 'state_frames', 'schema_version': 1,
                          'run_id': run_dir.name, 'unit_name': 'state_frames_test',
                          'cadence_timeframe': 'M1', 'cadence_s': 60.0}

    def test_the_cadence_follows_the_replayed_tick_time(self, session):
        _, _, _, frames = session
        slots = [_instant(f['cadence_slot']) for f in frames if 'cadence_slot' in f]
        assert len(slots) > 60, 'about six hours of ticks fill far more than an hour of slots'
        assert slots == sorted(set(slots)), 'every slot is filled once, in order'
        assert all(slot.second == 0 and slot.microsecond == 0 for slot in slots)
        assert slots[0].year == 2026 and slots[0].month == 1, 'the replay, not the wall clock'

    def test_one_frame_per_pass(self, session):
        _, _, _, frames = session
        passes = [f['ticks_processed'] for f in frames if f['written_for'] != 'shutdown']
        assert len(passes) == len(set(passes))

    def test_every_closed_bar_is_named_by_its_opening(self, session):
        _, _, _, frames = session
        closes = [f for f in frames if f['written_for'] == 'bar_close']
        assert closes
        for frame in closes:
            for timeframe, opening in frame['closed_bars'].items():
                minutes = {'M5': 5, 'M30': 30}[timeframe]
                bar_open = _instant(opening)
                assert bar_open.minute % minutes == 0 and bar_open.second == 0
                assert bar_open < _instant(frame['frame_time'])

    def test_the_order_is_recorded_at_its_moment(self, session):
        result, _, _, frames = session
        orders = [f for f in frames if f['written_for'] == 'order']
        assert orders, 'the window holds an order'
        sent = {order.order_id for order in result.order_history}
        assert all(f['order_id'] in sent for f in orders)

    def test_the_run_list_names_the_stream(self, session):
        """Recorded when the stream opens, so the check-in finds it while the session runs (#611)."""
        _, run_dir, _, _ = session
        run = next(info for info in ReportStore().list_runs() if info.run_id == run_dir.name)
        assert STATE_FRAMES_FILE in run.stream_files
        assert STATE_FRAMES_FILE not in run.artifacts

    def test_a_health_nobody_checked_is_null(self, session):
        _, _, _, frames = session
        assert not any('feed_stale' in f or 'reconcile_clean' in f for f in frames), \
            'a mock session binds no signal source and runs no reconciler'


class TestTheLastFrameAgreesWithTheReport:
    """The state the report describes, at the same moment and price, with the same rows."""

    def test_balance_equity_price_and_positions(self, session):
        _, run_dir, _, frames = session
        last = frames[-1]
        assert last['written_for'] == 'shutdown'
        unit = read_artifact(run_dir / IO_SUBDIR / PORTFOLIO_ARTIFACT.filename,
                             PORTFOLIO_ARTIFACT).units[0]
        assert last['balance'] == pytest.approx(unit.current_balance)
        assert last['equity'] == pytest.approx(unit.final_equity)
        assert last['last_price'] == pytest.approx(unit.last_price)
        assert last['open_positions'] == [row.model_dump(mode='json') for row in unit.open_positions]
        assert last['open_positions'], 'the window ends with the position it opened'


class TestSwitchedOff:

    def test_writes_no_stream(self):
        config = load_autotrader_config(PROFILE)
        config.state_frames = StateFramesDefaults(enabled=False)
        trader = AutotraderMain(config)
        try:
            trader.run()
            assert not (trader._run_dir / IO_SUBDIR / STATE_FRAMES_FILE).exists()
        finally:
            remove_run_dir(trader._run_dir)
