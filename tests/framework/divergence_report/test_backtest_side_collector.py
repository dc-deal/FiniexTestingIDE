"""
The backtest side's collector over a real run directory (#512).

The run is written the way a backtest writes itself: its header at the start, registered in the
run index, a state-frame stream per scenario, its scenario details at the end and its ledger row
through the ledger's own writer. The collector reads it back, and the gate takes what it read.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from python.framework.constants.run_layout_constants import (
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.reporting.builders.divergence.backtest_side_collector import (
    BacktestSideCollector,
)
from python.framework.reporting.io.artifact_specs import SCENARIO_DETAILS_ARTIFACT
from python.framework.reporting.io.order_event_stream_writer import OrderEventStreamWriter
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.entry_members.run_dir_reader import RunDirReader
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.report_types import (
    DataWindow,
    RunHeader,
    RunSummary,
    ScenarioDetailsReport,
    ScenarioDetailsRow,
)
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_results_types import RunProvenance
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.validators.divergence_gate_validator import check_divergence_gate
from tests.shared.divergence_gate_pair import (
    BACKTEST_COMMIT,
    BROKER_CONFIG,
    RENDERED_STRATEGY,
    SCENARIO,
    STRATEGY_ROOT_HERE,
    WINDOW_END,
    code_identity,
    empty_tree,
    gate_session,
    gate_window,
)
from tests.shared.divergence_live_chain import FIRST_TICK, frame
from tests.shared.null_logger import NullLogger

_LAST_TICK = WINDOW_END - timedelta(seconds=3)


class _Tree:
    """A run index, a ledger and one backtest run directory in a temporary tree."""

    def __init__(self, root: Path):
        self.root = root
        self.index = root / 'runs_index.parquet'
        self.ledger_dir = root / 'ledger'
        self.run_configs = RunConfigStore(root / 'run_configs')
        self.rendered_id = self.run_configs.register_document(
            {'kind': RunConfigKind.SCENARIO_SET_RENDERED.value,
             'scenarios': {SCENARIO: {'strategy_config': RENDERED_STRATEGY}}},
            RunConfigKind.SCENARIO_SET_RENDERED).config_id

    def collector(self) -> BacktestSideCollector:
        return BacktestSideCollector(ledger=RunResultsLedger(self.ledger_dir),
                                     reader=RunEntryReader(self.index),
                                     run_configs=self.run_configs)

    def backtest(self, run_id: str = 'b1', run_type: str = RUN_TYPE_SIMULATION,
                 frames: bool = True, details: bool = True,
                 end_date: Optional[str] = WINDOW_END.isoformat(),
                 rendered: bool = True) -> Path:
        """Write one finished backtest of the window."""
        run_dir = self.root / 'runs' / run_id
        run_dir.mkdir(parents=True)
        header = RunHeader(
            run_id=run_id, start_time=datetime(2027, 1, 2, 9, tzinfo=timezone.utc),
            run_type=run_type, run_name='my_recorded_set', app_version='1.5.0',
            rendered_config_id=self.rendered_id if rendered else '',
            code_identity=code_identity(framework_commit=BACKTEST_COMMIT,
                                        strategy_root=STRATEGY_ROOT_HERE),
            data_windows=[DataWindow(unit_name=SCENARIO, start_date=FIRST_TICK.isoformat(),
                                     end_date=end_date)])
        write_run_header(header, run_dir)
        RunIndex(self.index).register_run(header, run_dir)
        directory = RunDirReader(run_dir)
        writer = RunEntryWriter(run_dir, RUN_TYPE_SIMULATION)
        if frames:
            stream = writer.open_stream(
                RunEntryMemberId.SCENARIO_STATE_FRAMES, run_id, NullLogger(), stops='-',
                header_fields={'unit_name': SCENARIO, 'cadence_timeframe': 'M1',
                               'cadence_s': 60.0}, scenario=SCENARIO)
            for minute in range(3):
                stream.write(frame(FIRST_TICK + timedelta(minutes=minute)).model_dump(mode='json'))
            stream.close()
        # Two scenarios' events in the run's one stream, as the batch writes them at its end
        appender = writer.open_stream(RunEntryMemberId.ORDER_EVENTS, run_id, NullLogger(),
                                      stops='-', required=True)
        events = OrderEventStreamWriter(appender, SCENARIO)
        others = OrderEventStreamWriter(appender, 'my_other_scenario')
        for seq, event_type in ((1, OrderEventType.SUBMITTED), (2, OrderEventType.FILLED)):
            event = OrderEvent(seq=seq, event_type=event_type, order_id='order_1',
                               submitted_seq=1, symbol='ETHUSD', lots=0.1, event_time=FIRST_TICK)
            events(event)
            others(event)
        appender.close()
        if details:
            directory.section_path(SCENARIO_DETAILS_ARTIFACT).parent.mkdir(exist_ok=True)
            write_artifact(ScenarioDetailsReport(run_id=run_id, units=[ScenarioDetailsRow(
                name=SCENARIO, symbol='ETHUSD', first_tick_time=FIRST_TICK.isoformat(),
                last_tick_time=_LAST_TICK.isoformat())]),
                directory.section_path(SCENARIO_DETAILS_ARTIFACT).parent,
                SCENARIO_DETAILS_ARTIFACT)
        RunResultsLedger(self.ledger_dir).append(
            RunSummary(run_id=run_id, currencies=[], orders_submitted=0, orders_executed=0,
                       orders_rejected=0, sl_tp_triggered=0, unit_count=1),
            RunProvenance(
                param_hash='my_hash', status='ok', error=None, run_id=run_id,
                run_timestamp=header.start_time, scenario_set_name='my_recorded_set',
                app_version='1.5.0', git_commit=BACKTEST_COMMIT, git_branch='dev',
                git_dirty=False, decision_logic_type='my_algo/decision.py',
                decision_version='1.0.0', worker_versions={},
                strategy_config_json='{}', symbols=['ETHUSD'],
                data_broker_type='kraken_spot', run_type=RUN_TYPE_SIMULATION,
                broker_config_id=BROKER_CONFIG))
        return run_dir


@pytest.fixture
def tree(tmp_path) -> _Tree:
    return _Tree(tmp_path)


class TestTheRunAsItRecordedItself:

    def test_its_header_rows_and_scenarios(self, tree):
        tree.backtest()
        capture = tree.collector().collect_run('b1')
        assert capture.app_version == '1.5.0'
        assert capture.code_identity.framework.commit == BACKTEST_COMMIT
        row, = capture.rows
        assert row.broker_config_id == BROKER_CONFIG
        scenario, = capture.scenarios
        assert scenario.name == SCENARIO and scenario.records_frames
        assert scenario.rendered_strategy_config == RENDERED_STRATEGY
        assert (scenario.first_tick, scenario.last_tick) == (FIRST_TICK, _LAST_TICK)
        assert scenario.declared_window.end_date == WINDOW_END.isoformat()
        assert not capture.unreadable

    def test_a_scenario_without_its_frames_says_so(self, tree):
        tree.backtest(frames=False)
        assert not tree.collector().collect_run('b1').scenarios[0].records_frames
        records = tree.collector().collect_scenario_records('b1', SCENARIO)
        assert records.frames.frames == [] and records.frames.unreadable


class TestWhatAScenarioRecordedAsItRan:

    def test_its_own_order_events_and_its_frames(self, tree):
        tree.backtest()
        records = tree.collector().collect_scenario_records('b1', SCENARIO)
        assert [event.seq for event in records.order_events] == [1, 2]
        assert {event.scenario_name for event in records.order_events} == {SCENARIO}
        assert [f.frame_time for f in records.frames.frames] == [
            FIRST_TICK + timedelta(minutes=minute) for minute in range(3)]
        assert records.frames.unreadable == '' and records.unreadable == {}

    def test_missing_scenario_details_are_named_never_filled_in(self, tree):
        tree.backtest(details=False)
        capture = tree.collector().collect_run('b1')
        scenario, = capture.scenarios
        assert scenario.first_tick is None and scenario.last_tick is None
        assert 'scenario_details' in capture.unreadable

    def test_a_run_naming_no_rendered_set_says_so(self, tree):
        tree.backtest(rendered=False)
        capture = tree.collector().collect_run('b1')
        assert capture.scenarios[0].rendered_strategy_config is None
        assert 'rendered_config' in capture.unreadable

    def test_a_tick_limited_scenario_has_an_open_window(self, tree):
        tree.backtest(end_date=None)
        assert tree.collector().collect_run('b1').scenarios[0].declared_window.end_date is None

    def test_a_run_nobody_names_is_refused(self, tree):
        with pytest.raises(ValueError, match='neither a header nor a ledger row'):
            tree.collector().collect_run('no_such_run')

    def test_a_session_is_no_backtest(self, tree):
        tree.backtest(run_id='s1', run_type=RUN_TYPE_AUTOTRADER)
        with pytest.raises(ValueError, match='not a backtest'):
            tree.collector().collect_run('s1')


def test_the_gate_takes_what_the_collector_read(tree):
    tree.backtest()
    result = check_divergence_gate([gate_session()], tree.collector().collect_run('b1'),
                                   gate_window(), read_tree=empty_tree)
    assert result.is_comparable(), result.findings
