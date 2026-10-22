"""
When a session writes a state frame and what it holds (#579 part A) — the recorder against stand-ins
for the session's objects, so every rule is reached in milliseconds: a cadence on a grid of the
canonical clock, a frame at a closed bar and at an order, at most one per pass, a clock that steps
back, the memory on every live frame and paced in a replay, and a frame that cannot be built.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from python.framework.autotrader.run_record.state_frame_recorder import StateFrameRecorder
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.types.autotrader_types.autotrader_display_types import SafetyState
from python.framework.types.config_types.autotrader_defaults_config_types import StateFramesDefaults
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.process_table_types import OwnProcessMemory
from python.framework.types.trading_env_types.active_orders_snapshot_types import ActiveOrdersSnapshot
from tests.shared.recording_logger import RecordingLogger

_T0 = datetime(2026, 11, 3, 10, 0, 30, tzinfo=timezone.utc)


class _Session:
    """The parts of a session the recorder reads, with a clock the test moves."""

    def __init__(self, signal_workers=None, priced=True):
        self.now = _T0
        self.memory_reads = 0
        self.monotonic = 0.0
        self.priced = priced
        self.executor = SimpleNamespace(
            get_current_time_if_set=lambda: self.now,
            portfolio=SimpleNamespace(
                ensure_positions_updated=lambda: None, balance=248.10,
                get_balances=lambda: {'USD': 248.10, 'ETH': 0.1},
                get_account_value=lambda: 489.35),
            get_current_price=self._price,
            get_open_positions=lambda: [],
            get_protective_level_enforcement=lambda: SimpleNamespace(value='local'),
            get_active_orders_snapshot=lambda: ActiveOrdersSnapshot(),
            account_currency='USD',
            get_market_data_status=lambda: SimpleNamespace(is_stale=False),
        )
        self.orchestrator = SimpleNamespace(
            workers={'rsi_main': object()},
            get_worker_result=lambda name: SimpleNamespace(
                outputs={'rsi': np.float64(29.4), 'bars_used': np.int64(14),
                         'not_yet': float('nan')}),
            get_signal_workers=lambda: signal_workers or {},
            get_stale_signal_workers=lambda: [],
        )
        self.bars = SimpleNamespace(get_bar_history=lambda symbol, timeframe: [
            SimpleNamespace(timestamp='2026-11-03T09:55:00+00:00')])

    def _price(self, symbol):
        if not self.priced:
            raise ValueError('no tick yet')
        return 2412.0, 2413.0

    def read_memory(self) -> OwnProcessMemory:
        self.memory_reads += 1
        return OwnProcessMemory(rss_bytes=431225856, private_bytes=388235264)

    def recorder(self, path: Path, logger=None, replayed=False, reconciler=None, cadence='M1'):
        appender = RunStreamAppender(path, 'state_frames', 1, 'my_run', logger,
                                     stops='The state is no longer recorded')
        return StateFrameRecorder(
            appender, cadence, SimpleNamespace(symbol='ETHUSD'), self.executor, self.orchestrator,
            self.bars, TradingModel.SPOT, reconciler, replayed, logger or RecordingLogger(),
            wall_clock=lambda: datetime(2026, 11, 3, 10, 1, tzinfo=timezone.utc),
            memory_reader=self.read_memory, monotonic_clock=lambda: self.monotonic)


def _frames(path: Path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()][1:]


def _decision(action=DecisionLogicAction.FLAT):
    return Decision(action=action, outputs={'signal_strength': np.float64(0.12)})


def _pass(recorder, on_tick=True, closed=frozenset(), order=None, ticks=1):
    recorder.on_pass(ticks, _decision(), SafetyState(), on_tick=on_tick,
                     closed_timeframes=closed, order=order)


class TestTheCadence:

    def test_slots_lie_on_a_grid_of_the_canonical_clock(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        _pass(recorder)                                   # 10:00:30 — the first slot
        session.now = _T0 + timedelta(seconds=20)         # 10:00:50 — the same slot
        _pass(recorder)
        session.now = _T0 + timedelta(seconds=45)         # 10:01:15 — the next one
        _pass(recorder)
        frames = _frames(path)
        assert [f['written_for'] for f in frames] == ['cadence', 'cadence']
        assert [f['cadence_slot'] for f in frames] == ['2026-11-03T10:00:00Z', '2026-11-03T10:01:00Z']
        assert frames[0]['frame_time'] == '2026-11-03T10:00:30Z'

    def test_a_coarser_cadence_fills_the_periods_the_bars_open_on(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path, cadence='M5')
        for minutes in (0, 3, 5, 9, 11):                  # 10:00:30, 10:03:30, 10:05:30, ...
            session.now = _T0 + timedelta(minutes=minutes)
            _pass(recorder)
        assert [f['cadence_slot'] for f in _frames(path)] == [
            '2026-11-03T10:00:00Z', '2026-11-03T10:05:00Z', '2026-11-03T10:10:00Z']

    def test_a_clock_that_steps_back_begins_the_grid_again_without_a_frame(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        _pass(recorder)
        session.now = _T0 - timedelta(days=200)           # a replay's tick after a wall-clock heartbeat
        _pass(recorder)
        session.now = _T0 - timedelta(days=200) + timedelta(seconds=10)
        _pass(recorder)
        assert len(_frames(path)) == 1

    def test_a_replays_heartbeat_does_not_drive_the_cadence(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path, replayed=True)
        _pass(recorder, on_tick=False)
        assert _frames(path) == []
        _pass(recorder, on_tick=True)
        assert len(_frames(path)) == 1

    def test_a_live_heartbeat_does_on_a_quiet_feed(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path, replayed=False)
        _pass(recorder, on_tick=False)
        assert [f['written_for'] for f in _frames(path)] == ['cadence']


class TestTheCadenceSetting:

    def test_is_a_timeframe_one_minute_by_default(self):
        assert StateFramesDefaults().cadence_timeframe == 'M1'
        assert StateFramesDefaults(cadence_timeframe='H1').cadence_timeframe == 'H1'

    def test_refuses_a_name_that_is_no_timeframe_and_names_the_ones_there_are(self):
        with pytest.raises(ValidationError, match="'90s' is not a timeframe") as refused:
            StateFramesDefaults(cadence_timeframe='90s')
        assert 'M1' in str(refused.value) and 'H1' in str(refused.value)


class TestTheEvents:

    def test_an_order_is_a_moment_of_its_own_even_inside_a_filled_slot(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        _pass(recorder)
        session.now = _T0 + timedelta(seconds=5)
        _pass(recorder, order=SimpleNamespace(order_id='my_order_7'))
        frames = _frames(path)
        assert [f['written_for'] for f in frames] == ['cadence', 'order']
        assert frames[1]['order_id'] == 'my_order_7'
        assert 'cadence_slot' not in frames[1], 'the slot was filled already'

    def test_a_closed_bar_names_the_bar_by_its_opening(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        _pass(recorder, closed=frozenset({'M5'}))
        frame = _frames(path)[0]
        assert frame['written_for'] == 'bar_close'
        assert frame['closed_bars'] == {'M5': '2026-11-03T09:55:00Z'}

    def test_an_event_fills_a_due_slot_so_one_pass_writes_one_frame(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        _pass(recorder, closed=frozenset({'M5'}), order=SimpleNamespace(order_id='my_order_7'))
        session.now = _T0 + timedelta(seconds=10)
        _pass(recorder)
        frames = _frames(path)
        assert len(frames) == 1
        assert frames[0]['written_for'] == 'order'
        assert frames[0]['cadence_slot'] == '2026-11-03T10:00:00Z'
        assert frames[0]['closed_bars'] == {'M5': '2026-11-03T09:55:00Z'}


class TestWhatAFrameHolds:

    def test_every_output_as_a_plain_value_and_nan_as_null(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        _pass(session.recorder(path))
        frame = _frames(path)[0]
        assert frame['worker_outputs'] == {'rsi_main': {'rsi': 29.4, 'bars_used': 14,
                                                        'not_yet': None}}
        assert frame['decision_action'] == 'FLAT'
        assert frame['decision_outputs'] == {'signal_strength': 0.12}

    def test_a_structured_output_is_written_as_json(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        session.orchestrator.get_worker_result = lambda name: SimpleNamespace(outputs={
            'warmup_status': {'M5': {'valid': np.bool_(True), 'missing': np.int64(0)}},
            'band': np.array([1.5, float('nan')]),
            'action': DecisionLogicAction.BUY,
            'since': datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)})
        _pass(session.recorder(path))
        assert _frames(path)[0]['worker_outputs']['rsi_main'] == {
            'warmup_status': {'M5': {'valid': True, 'missing': 0}},
            'band': [1.5, None],
            'action': 'BUY',
            'since': '2026-11-03T09:00:00+00:00'}

    def test_the_account_valued_at_the_mid(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        _pass(session.recorder(path))
        frame = _frames(path)[0]
        assert frame['last_price'] == 2412.5
        assert frame['balances'] == {'USD': 248.10, 'ETH': 0.1}
        assert frame['equity'] == 489.35
        assert frame['written_at'] == '2026-11-03T10:01:00Z'

    def test_before_a_tick_the_account_is_unvalued_never_guessed(self, tmp_path):
        session, path = _Session(priced=False), tmp_path / 'frames.jsonl'
        _pass(session.recorder(path))
        assert 'last_price' not in _frames(path)[0]

    def test_a_health_nobody_checked_is_null(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        _pass(session.recorder(path))
        frame = _frames(path)[0]
        assert 'feed_stale' not in frame and 'reconcile_clean' not in frame
        assert frame['market_stale'] is False

    def test_a_health_that_was_checked_is_said(self, tmp_path):
        session, path = _Session(signal_workers={'my_signal': object()}), tmp_path / 'frames.jsonl'
        reconciler = SimpleNamespace(get_last_verdict=lambda: False)
        _pass(session.recorder(path, reconciler=reconciler))
        frame = _frames(path)[0]
        assert frame['feed_stale'] is False
        assert frame['reconcile_clean'] is False

    def test_a_reconciler_without_a_verdict_checked_nothing_yet(self, tmp_path):
        # Before its first cycle, or after one that could not reach the venue — never "clean"
        session, path = _Session(), tmp_path / 'frames.jsonl'
        reconciler = SimpleNamespace(get_last_verdict=lambda: None)
        _pass(session.recorder(path, reconciler=reconciler))
        assert 'reconcile_clean' not in _frames(path)[0]


class TestTheMemory:

    def test_a_live_session_reads_it_on_every_frame(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        for slot in range(3):
            session.now = _T0 + timedelta(seconds=60 * slot)
            session.monotonic = 59.7 * slot               # two frames a little under one interval apart
            _pass(recorder)
        _pass(recorder, closed=frozenset({'M5'}))         # an event frame in the same slot
        recorder.write_shutdown(4, _decision(), SafetyState())
        frames = _frames(path)
        assert len(frames) == 5
        assert all('process_memory' in f for f in frames)
        assert session.memory_reads == 5

    def test_a_replay_reads_it_at_most_once_per_interval_and_always_at_the_end(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path, replayed=True)
        _pass(recorder)                                   # read
        session.now += timedelta(seconds=60)
        session.monotonic = 30.0
        _pass(recorder)                                   # half an interval later: not read
        session.now += timedelta(seconds=60)
        session.monotonic = 61.0
        _pass(recorder)                                   # read again
        recorder.write_shutdown(3, _decision(), SafetyState())   # always read
        frames = _frames(path)
        assert [('process_memory' in f) for f in frames] == [True, False, True, True]
        assert frames[0]['process_memory'] == {'rss_bytes': 431225856, 'private_bytes': 388235264}
        assert session.memory_reads == 3


class TestAFrameThatCannotBeBuilt:

    def test_is_said_once_skipped_and_counted_at_the_end(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        logger = RecordingLogger()
        recorder = session.recorder(path, logger=logger)
        broken = session.executor.get_active_orders_snapshot
        session.executor.get_active_orders_snapshot = lambda: 1 / 0
        _pass(recorder)
        session.now += timedelta(seconds=60)
        _pass(recorder)
        session.executor.get_active_orders_snapshot = broken
        session.now += timedelta(seconds=60)
        _pass(recorder)
        recorder.write_shutdown(3, _decision(), SafetyState())
        assert [f['written_for'] for f in _frames(path)] == ['cadence', 'shutdown']
        assert logger.text_at('error').count('could not be built') == 1
        assert '2 state frame(s) could not be built' in logger.text_at('warning')

    def test_is_tried_again_at_the_next_slot_never_on_every_pass(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        logger = RecordingLogger()
        recorder = session.recorder(path, logger=logger)
        session.executor.get_active_orders_snapshot = lambda: 1 / 0
        for second in range(0, 25, 5):                    # five passes inside one slot
            session.now = _T0 + timedelta(seconds=second)
            _pass(recorder, ticks=second)
        recorder.write_shutdown(5, _decision(), SafetyState())
        assert '2 state frame(s) could not be built' in logger.text_at('warning'), \
            'one attempt for the slot, one for the end — not one per pass'


class TestTheEnd:

    def test_the_last_frame_is_written_and_the_stream_closed(self, tmp_path):
        session, path = _Session(), tmp_path / 'frames.jsonl'
        recorder = session.recorder(path)
        recorder.write_shutdown(0, None, SafetyState())
        session.now += timedelta(seconds=60)
        _pass(recorder)
        frames = _frames(path)
        assert [f['written_for'] for f in frames] == ['shutdown']
        assert 'decision_action' not in frames[0], 'no pass ran a decision'
        assert recorder.get_frames_written() == 1
