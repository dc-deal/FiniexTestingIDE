"""
The live side's collector over real run directories (#512).

Each session is written the way a session writes itself: registered in the run index under its
deployment, its order events and state frames streamed and sealed into day records by the stream
sealer at each boundary, its warmup history and its warnings-errors report beside them, and its
booking periods through the ledger's own writer. The collector reads them back — the sessions
without a stream first, their sealed days second — and the classification compares every day of
an unbroken session.
"""

import dataclasses
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pytest

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.reporting.builders.booking_period_builder import (
    PeriodSnapshot,
    derive_booking_period,
)
from python.framework.reporting.builders.divergence.live_segment_builder import derive_segments
from python.framework.reporting.builders.divergence.live_side_collector import LiveSideCollector
from python.framework.reporting.builders.divergence.live_trading_day_builder import closing_of
from python.framework.reporting.io.artifact_specs import WARNINGS_ERRORS_ARTIFACT
from python.framework.reporting.io.order_event_stream_writer import OrderEventStreamWriter
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.reporting.io.run_stream_sealer import RunStreamSealer
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.divergence_report_types import SessionEndKind
from python.framework.types.api.report_types import (
    ParentKind,
    RunHeader,
    SessionBookingState,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
)
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_results_types import PeriodCloseReason, RunProvenance
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats
from python.framework.validators.divergence_day_validator import live_side_of
from tests.shared.divergence_gate_pair import RENDERED_STRATEGY, code_identity
from tests.shared.divergence_live_chain import (
    CRYPTO_ANCHOR,
    DEPLOYMENT,
    FIRST_DAY,
    FIRST_TICK,
    SYMBOL,
    code_stand,
    frame,
)
from tests.shared.null_logger import NullLogger

_BROKER_DOCUMENT = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_ETHUSD_LOT_STEP = 1e-08   # the document's own volume_step for ETHUSD


class _Tree:
    """A run index, a ledger and run directories in one temporary tree."""

    def __init__(self, root: Path):
        self.root = root
        self.index = root / 'runs_index.parquet'
        self.ledger_dir = root / 'ledger'
        self.run_configs = RunConfigStore(root / 'run_configs')
        self.rendered_id = self.run_configs.register_document(
            {'kind': RunConfigKind.AUTOTRADER_RENDERED.value, 'strategy_config': RENDERED_STRATEGY},
            RunConfigKind.AUTOTRADER_RENDERED).config_id

    def collector(self) -> LiveSideCollector:
        with open(_BROKER_DOCUMENT, 'r', encoding='utf-8') as handle:
            document = json.load(handle)
        return LiveSideCollector(
            ledger=RunResultsLedger(self.ledger_dir), run_index=RunIndex(self.index),
            reader=RunEntryReader(self.index), store=ReportStore(self.index, self.ledger_dir),
            market_config=MarketConfigManager(), read_broker_config=lambda _id: document,
            run_configs=self.run_configs)

    def session(self, run_id: str, first_tick: datetime, days: List[date],
                closed_at: Optional[datetime] = None, broker_type: str = 'kraken_spot',
                book: bool = True) -> Path:
        """Write one session that sealed the given days, one order filled at each day's noon."""
        run_dir = self.root / 'runs' / run_id
        header = RunHeader(
            run_id=run_id, start_time=first_tick - timedelta(seconds=30),
            run_type=RUN_TYPE_AUTOTRADER, run_name='my_bot', parent_id=DEPLOYMENT,
            parent_kind=ParentKind.DEPLOYMENT, code_identity=code_identity())
        RunIndex(self.index).register_run(header, run_dir)
        write_run_header(header, run_dir)
        logger = NullLogger()
        sealer = RunStreamSealer(logger)
        writer = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER, streams=sealer)
        orders = OrderEventStreamWriter(writer.open_stream(
            RunEntryMemberId.ORDER_EVENTS, run_id, logger, stops='-', required=True), 'my_session')
        frames = writer.open_stream(RunEntryMemberId.STATE_FRAMES, run_id, logger, stops='-',
                                    header_fields={'unit_name': 'my_session',
                                                   'cadence_timeframe': 'M1', 'cadence_s': 60.0})
        frames.write(frame(first_tick).model_dump(mode='json'))
        seq = 0
        for day in days:
            closing = closing_of(day, CRYPTO_ANCHOR)
            noon = closing - timedelta(hours=12)
            for event_type in (OrderEventType.SUBMITTED, OrderEventType.FILLED):
                seq += 1
                orders(OrderEvent(seq=seq, event_type=event_type, order_id=f'order_{day}',
                                  submitted_seq=seq, symbol=SYMBOL, lots=0.1, event_time=noon))
            frames.write(frame(noon, lots=0.1).model_dump(mode='json'))
            frames.write(frame(closing - timedelta(minutes=1), lots=0.1).model_dump(mode='json'))
            sealer.seal(day, day + timedelta(days=1))
        orders.close()
        frames.close()
        RunDirReader(run_dir).path_of(RunEntryMemberId.WARMUP_HISTORY).write_text(json.dumps({
            'symbol': SYMBOL, 'source': 'venue', 'read_at': first_tick.isoformat(),
            'selected_for': 'live', 'first_tick': first_tick.isoformat(),
            'reread_at_first_tick': [], 'timeframes': {}}), encoding='utf-8')
        if closed_at is not None:
            write_artifact(WarningsErrorsReport(
                run_id=run_id, outcome=WarningsErrorsOutcome(shutdown_mode='normal')),
                RunDirReader(run_dir).section_path(WARNINGS_ERRORS_ARTIFACT).parent,
                WARNINGS_ERRORS_ARTIFACT)
        if book:
            self._book(run_id, first_tick, days, closed_at, broker_type)
        return run_dir

    def _book(self, run_id: str, first_tick: datetime, days: List[date],
              closed_at: Optional[datetime], broker_type: str) -> None:
        """The session's booking periods, through the ledger's writer."""
        periods = []
        opened = first_tick
        for period_no, day in enumerate(days, start=1):
            closing = closing_of(day, CRYPTO_ANCHOR)
            periods.append(dataclasses.replace(
                derive_booking_period('my_session', period_no, opened, closing,
                                      PeriodCloseReason.ANCHOR, [], PeriodSnapshot(currency='USD'),
                                      trading_day=day),
                execution=ExecutionStats(orders_submitted=1, orders_executed=1)))
            opened = closing
        if closed_at is not None:
            periods.append(dataclasses.replace(
                derive_booking_period('my_session', len(days) + 1, opened, closed_at,
                                      PeriodCloseReason.SESSION_END, [],
                                      PeriodSnapshot(currency='USD')),
                execution=ExecutionStats()))
        RunResultsLedger(self.ledger_dir).write_sealed_periods(RunProvenance(
            param_hash='hash', status='ok', error=None, run_id=run_id,
            run_timestamp=first_tick - timedelta(seconds=30), scenario_set_name='my_bot',
            app_version='1.5.0', git_commit='a1b2c3d4', git_branch='main', git_dirty=False,
            decision_logic_type='CORE/my_logic', decision_version='1.0.0', worker_versions={},
            strategy_config_json='{}', symbols=[SYMBOL], data_broker_type=broker_type,
            deployment_id=DEPLOYMENT, run_type=RUN_TYPE_AUTOTRADER,
            rendered_config_id=self.rendered_id, broker_config_id='broker_1',
            code_stand=code_stand()), periods)


@pytest.fixture
def tree(tmp_path) -> _Tree:
    return _Tree(tmp_path)


def _two_sessions(tree: _Tree):
    """A session closed after three days, and a second one still running after it."""
    first_days = [FIRST_DAY + timedelta(days=offset) for offset in range(3)]
    closed_at = closing_of(first_days[-1], CRYPTO_ANCHOR) + timedelta(hours=2)
    tree.session('s1', FIRST_TICK, first_days, closed_at=closed_at)
    tree.session('s2', closed_at + timedelta(minutes=10),
                 [FIRST_DAY + timedelta(days=offset) for offset in range(3, 5)])
    return closed_at


class TestTheSessionsWithoutAStream:

    def test_every_session_with_what_its_records_say(self, tree):
        closed_at = _two_sessions(tree)
        capture = tree.collector().collect_sessions(DEPLOYMENT)
        assert [s.run_id for s in capture.sessions] == ['s1', 's2']
        first, second = capture.sessions
        assert (first.booking_state, second.booking_state) == (SessionBookingState.BOOKED,) * 2
        assert first.first_tick == FIRST_TICK
        assert first.outcome is not None and second.outcome is None
        assert first.frame_ends.first.frame_time == FIRST_TICK
        assert (first.lot_step, first.base_currency) == (_ETHUSD_LOT_STEP, 'ETH')
        assert first.code_stand == code_stand() and not first.unreadable
        assert first.app_version == '1.5.0'
        assert first.rendered_strategy_config == RENDERED_STRATEGY
        assert first.code_identity == code_identity()
        assert [row.period_close_reason for row in first.rows][-1] == 'session_end'
        assert (capture.anchor, capture.weekend_closure) == (CRYPTO_ANCHOR, False)
        assert closed_at > FIRST_TICK

    def test_a_session_only_the_run_index_names_has_booked_nothing_yet(self, tree):
        _two_sessions(tree)
        tree.session('s3', FIRST_TICK + timedelta(days=6), [], book=False)
        capture = tree.collector().collect_sessions(DEPLOYMENT)
        assert capture.sessions[-1].booking_state is SessionBookingState.NOT_BOOKED_YET

    def test_a_deployment_nobody_names_is_refused(self, tree):
        with pytest.raises(ValueError, match='No session names'):
            tree.collector().collect_sessions('no_such_deployment')

    def test_two_brokers_in_one_deployment_are_refused(self, tree):
        _two_sessions(tree)
        tree.session('s3', FIRST_TICK + timedelta(days=6), [FIRST_DAY + timedelta(days=6)],
                     broker_type='mt5')
        with pytest.raises(ValueError, match='2 brokers'):
            tree.collector().collect_sessions(DEPLOYMENT)


class TestEverySessionsFrames:

    def test_read_whole_across_the_sealed_days_in_time_order(self, tree):
        _two_sessions(tree)
        collector = tree.collector()
        frames = collector.collect_session_frames(collector.collect_sessions(DEPLOYMENT))
        assert sorted(frames) == ['s1', 's2']
        first = frames['s1']
        # the first tick's frame, then two a day — at noon and a minute before the closing
        assert len(first.frames) == 1 + 2 * 3 and first.unreadable == ''
        times = [frame.frame_time for frame in first.frames]
        assert times == sorted(times) and times[0] == FIRST_TICK

    def test_a_session_that_records_no_frames_says_so(self, tree):
        _two_sessions(tree)
        tree.session('s3', FIRST_TICK + timedelta(days=6), [], book=False)
        shutil.rmtree(RunDirReader(tree.root / 'runs' / 's3').path_of(
            RunEntryMemberId.STATE_FRAMES).parent)
        collector = tree.collector()
        frames = collector.collect_session_frames(collector.collect_sessions(DEPLOYMENT))
        assert frames['s3'].frames == [] and frames['s3'].unreadable


class TestTheSealedDays:

    def test_each_days_records(self, tree):
        _two_sessions(tree)
        collector = tree.collector()
        days = collector.collect_days(collector.collect_sessions(DEPLOYMENT))
        assert sorted(days) == (
            [('s1', FIRST_DAY + timedelta(days=offset)) for offset in range(3)]
            + [('s2', FIRST_DAY + timedelta(days=offset)) for offset in range(3, 5)])
        record = days[('s1', FIRST_DAY)]
        closing = closing_of(FIRST_DAY, CRYPTO_ANCHOR)
        assert [e.event_type for e in record.order_events] == [OrderEventType.SUBMITTED,
                                                               OrderEventType.FILLED]
        assert record.order_events_label == FIRST_DAY and not record.order_events_cut_last_line
        assert record.frames_last_time == closing - timedelta(minutes=1)
        assert record.closing_position.net_lots == {SYMBOL: pytest.approx(0.1)}

    def test_an_unbroken_session_is_compared_every_day_and_a_restart_waits(self, tree):
        _two_sessions(tree)
        collector = tree.collector()
        capture = collector.collect_sessions(DEPLOYMENT)
        segments, named = derive_segments(capture)
        segment, = live_side_of(capture, segments, named, collector.collect_days(capture)).segments
        assert [day.included for day in segment.days[:3]] == [True, True, True]
        assert segment.session_ends[0].kind is SessionEndKind.CLOSED
        assert all(not day.included for day in segment.days[3:]), 'after the stop, until resumed'
        assert all(day.completeness.state.value == 'complete' for day in segment.days)

    def test_a_day_record_gone_is_not_checked(self, tree):
        run_dir = tree.session('s1', FIRST_TICK, [FIRST_DAY, FIRST_DAY + timedelta(days=1)])
        shutil.rmtree(run_dir / 'days' / f'{FIRST_DAY:%Y%m%d}')
        collector = tree.collector()
        capture = collector.collect_sessions(DEPLOYMENT)
        segments, named = derive_segments(capture)
        side = live_side_of(capture, segments, named, collector.collect_days(capture))
        first = side.segments[0].days[0]
        assert not first.included and first.completeness.state.value == 'not_checked'
