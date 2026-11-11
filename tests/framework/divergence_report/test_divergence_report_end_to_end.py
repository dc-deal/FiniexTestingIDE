"""
The divergence report from end to end (#512): a session's real records — header, ledger rows,
frames, warmup, order events — written by a mock session, and the comparison run over them as the
command runs it: the live side read, the backtest built from what the session recorded and run, the
gate, the measures, the verdict and the record.

The mock session replays the collector's own BTCUSD ticks across a trading-day boundary, so one
whole day is sealed and compared; it buys on its first tick and sells the next day. Its mock venue
holds no record, so the comparison asks no venue.

The gate checks the code for real. On a tree with uncommitted changes it refuses — nobody can say
which code ran — and that refusal must be the only one: the session is found, its broker document is
the backtest's. On a committed tree, the merge suite's and the release's, the chain runs to the
verdict, and a backtest of the mock session's own window must pass.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.constants.run_layout_constants import IO_SUBDIR
from python.framework.reporting.divergence_report_coordinator import DivergenceReportCoordinator
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.divergence_report_types import (
    DivergenceMeasure,
    DivergenceReading,
    MeasureState,
)
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.validation_types import Severity
from python.framework.utils.git_info_utils import get_git_info
from tests.shared.fixture_helpers import remove_run_dir
from tests.shared.null_logger import NullLogger

PROFILE = 'configs/autotrader_profiles/mock/divergence_report_test.json'
# The boundary after the one whole day the session ran through
UNTIL = datetime(2026, 1, 27, tzinfo=timezone.utc)
_UNCOMMITTED = 'divergence_gate_uncommitted'


@pytest.fixture(scope='module')
def compared():
    """The mock session, then the comparison of its deployment."""
    trader = AutotraderMain(load_autotrader_config(PROFILE))
    trader.run()
    session_dir = trader._run_dir
    rows = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path())).read_rows(
        run_id=session_dir.name)
    report, text, report_dir = DivergenceReportCoordinator(
        rows[0].deployment_id, 'M30', DivergenceReading.REHEARSAL, NullLogger(), until=UNTIL,
        run_purpose=RunPurpose.FIXTURE).run()
    yield report, text, report_dir, session_dir
    remove_run_dir(session_dir)


class TestTheRecord:

    def test_it_keeps_the_set_the_warmup_and_the_report(self, compared):
        report, _, report_dir, _ = compared
        segment, = report.segments
        kept = {'report.json', 'report.txt', f'{segment.scenario_set_name}.json',
                'warmup_segment_1.json'}
        if segment.measured_latency is not None and segment.measured_latency.scenario_set_name:
            kept.add(f'{segment.measured_latency.scenario_set_name}.json')
        assert {path.name for path in report_dir.iterdir()} == kept

    def test_the_kept_warmup_is_the_session_s_own(self, compared):
        _, _, report_dir, session_dir = compared
        assert (report_dir / 'warmup_segment_1.json').read_bytes() == (
            session_dir / IO_SUBDIR / 'warmup_history.json').read_bytes()

    def test_the_printed_text_is_kept(self, compared):
        _, text, report_dir, _ = compared
        assert (report_dir / 'report.txt').read_text(encoding='utf-8') == text


class TestTheLiveSide:

    def test_one_segment_one_session_whose_window_opens_at_its_first_tick(self, compared):
        report, _, _, _ = compared
        segment, = report.live.segments
        assert len(segment.run_ids) == 1
        assert segment.window.start.date().isoformat() == '2026-01-25'
        assert segment.window.end == UNTIL

    def test_a_mock_session_is_asked_of_no_venue(self, compared):
        report, _, _, _ = compared
        assert 'mock adapter' in report.venue_ledger_unread


class TestTheGateAndTheVerdict:

    def test_the_backtest_is_judged_against_the_session_and_its_document(self, compared):
        """Whatever the tree, the gate saw the session and the two froze one broker document."""
        report, _, _, _ = compared
        segment, = report.segments
        assert segment.backtest_run_id
        refusals = {finding.check for finding in segment.gate.findings
                    if finding.severity == Severity.ERROR.value}
        assert refusals <= {_UNCOMMITTED}

    def test_a_committed_tree_reaches_the_verdict_and_it_passes(self, compared):
        """On uncommitted code the gate refuses, and that is the only refusal; on a commit the
        backtest of the session's own window passes every measure the rehearsal reading judges."""
        report, _, _, _ = compared
        segment, = report.segments
        git = get_git_info()
        if git is None or git.dirty:
            assert _UNCOMMITTED in segment.not_compared_because
            return
        assert segment.gate.comparable and segment.verdict is not None
        assert segment.verdict.state is MeasureState.PASSED
        verdicts = {measure.measure: measure.state for measure in segment.verdict.measures}
        assert verdicts[DivergenceMeasure.DECISIONS] is MeasureState.PASSED
        assert verdicts[DivergenceMeasure.ATTRIBUTION] is MeasureState.PASSED

    def test_beside_the_verdict_a_backtest_at_the_measured_latency(self, compared):
        """The session's two fills measured a latency, so a second backtest ran at it and measure
        2b stands against it — beside the verdict, never in it. Only a compared segment has one."""
        report, _, report_dir, _ = compared
        segment, = report.segments
        if segment.not_compared_because:
            assert segment.measured_latency is None
            return
        latency = segment.measured_latency
        assert latency.orders_measured == 2 and latency.latency_min_ms is not None
        assert latency.backtest_run_id and not latency.not_run_because
        assert latency.execution_price.measure is DivergenceMeasure.EXECUTION_PRICE
        assert (report_dir / f'{latency.scenario_set_name}.json').exists()
