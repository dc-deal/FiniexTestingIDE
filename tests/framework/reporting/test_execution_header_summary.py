"""
Execution Header Summary Tests.

ExecutionHeaderSummary renders the top-of-report header + basic-stats line from the run meta and
the warnings/errors outcome, which carries the batch status as a derived field. Tested with REAL
RunMetaReport / WarningsErrorsReport fixtures — the status mapping on the outcome + a render
smoke test.
"""
import io
import re
from contextlib import redirect_stdout

from python.framework.reporting.console.execution_header_summary import ExecutionHeaderSummary
from python.framework.types.api.report_types import (
    RunMetaReport,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
)
from python.framework.types.rendering_types import BatchStatus
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'


def _header(failed=0, total=5, scenario_count=5, exec_time=1.5, is_profile=False,
            parallel=False, max_parallel=0):
    outcome = WarningsErrorsOutcome(failed_count=failed, total_units=total)
    meta = RunMetaReport(run_id=_RUN_ID,
        scenario_count=scenario_count, execution_time_s=exec_time, is_profile_run=is_profile,
        parallel_scenarios=parallel, max_parallel_scenarios=max_parallel)
    return ExecutionHeaderSummary(meta, WarningsErrorsReport(run_id=_RUN_ID, outcome=outcome))


def _render(header) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        header.render(ConsoleRenderer())
    return re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())


class TestStatus:
    """The batch status is the outcome's — none, some or all of its units failed."""

    def test_success_all_ok(self):
        assert WarningsErrorsOutcome(failed_count=0, total_units=5).batch_status == BatchStatus.SUCCESS

    def test_partial_some_failed(self):
        assert WarningsErrorsOutcome(failed_count=2, total_units=5).batch_status == BatchStatus.PARTIAL

    def test_failed_all_failed(self):
        assert WarningsErrorsOutcome(failed_count=5, total_units=5).batch_status == BatchStatus.FAILED

    def test_empty_run_is_success(self):
        assert WarningsErrorsOutcome(failed_count=0, total_units=0).batch_status == BatchStatus.SUCCESS

    def test_the_counts_beside_it(self):
        outcome = WarningsErrorsOutcome(failed_count=2, total_units=5)
        assert (outcome.succeeded_count, outcome.success_rate_pct) == (3, 60.0)
        assert WarningsErrorsOutcome().success_rate_pct == 0.0

    def test_the_status_is_served(self):
        dumped = WarningsErrorsOutcome(failed_count=1, total_units=2).model_dump(mode='json')
        assert dumped['batch_status'] == 'partial' and dumped['succeeded_count'] == 1


class TestTheFirstFailure:
    """What the executive prints of the first failure is split on the outcome, not in a renderer."""

    def test_the_validation_heading_and_empty_lines_are_left_out(self):
        outcome = WarningsErrorsOutcome(
            failed_count=3, total_units=4,
            first_failure_error="Scenario 'a' failed validation:\n  range is empty\n\n  no ticks")
        assert outcome.first_failure_messages == ['range is empty', 'no ticks']
        assert outcome.other_failed_count == 2

    def test_nothing_failed_names_nothing(self):
        outcome = WarningsErrorsOutcome()
        assert outcome.first_failure_messages == [] and outcome.other_failed_count == 0


class TestRender:
    def test_header_and_stats(self):
        out = _render(_header(failed=0, total=5, scenario_count=5))
        assert 'EXECUTION RESULTS' in out and 'Profile Run' not in out
        assert 'Success: True' in out and '📊 Scenarios: 5' in out
        assert 'Batch Mode:' in out

    def test_profile_run_header(self):
        header = _header(scenario_count=3, is_profile=True)
        header._run_meta.symbols = ['BTCUSD', 'ETHUSD']
        assert 'Profile Run (3 blocks, 2 symbol(s))' in _render(header)

    def test_the_parallelism_is_the_run_meta_s(self):
        out = _render(_header(scenario_count=8, parallel=True, max_parallel=4))
        assert 'Parallel (4 concurrent)' in out
        assert 'Sequential' in _render(_header(scenario_count=8, parallel=False))
