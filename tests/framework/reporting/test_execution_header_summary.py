"""
Execution Header Summary Tests.

ExecutionHeaderSummary renders the top-of-report header + basic-stats line and owns the
batch-status derivation (from the warnings/errors outcome). Tested with REAL RunMetaReport /
WarningsErrorsReport fixtures — status mapping + a render smoke test.
"""
import io
import re
from contextlib import redirect_stdout

from python.framework.batch_reporting.execution_header_summary import ExecutionHeaderSummary
from python.framework.types.api.report_types import (
    RunMetaReport, WarningsErrorsOutcome, WarningsErrorsReport)
from python.framework.types.rendering_types import BatchStatus
from python.framework.utils.console_renderer import ConsoleRenderer
from python.configuration.app_config_manager import AppConfigManager


def _header(failed=0, total=5, scenario_count=5, exec_time=1.5, is_profile=False, app_config=None):
    outcome = WarningsErrorsOutcome(failed_count=failed, total_units=total)
    meta = RunMetaReport(
        scenario_count=scenario_count, execution_time_s=exec_time, is_profile_run=is_profile)
    return ExecutionHeaderSummary(meta, WarningsErrorsReport(outcome=outcome), app_config)


class TestStatus:
    def test_success_all_ok(self):
        assert _header(failed=0, total=5)._calculate_batch_status() == BatchStatus.SUCCESS

    def test_partial_some_failed(self):
        assert _header(failed=2, total=5)._calculate_batch_status() == BatchStatus.PARTIAL

    def test_failed_all_failed(self):
        assert _header(failed=5, total=5)._calculate_batch_status() == BatchStatus.FAILED

    def test_empty_run_is_success(self):
        assert _header(failed=0, total=0)._calculate_batch_status() == BatchStatus.SUCCESS


class TestRender:
    def test_header_and_stats(self):
        header = _header(failed=0, total=5, scenario_count=5, app_config=AppConfigManager())
        buf = io.StringIO()
        with redirect_stdout(buf):
            header.render(ConsoleRenderer())
        out = re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())
        assert 'EXECUTION RESULTS' in out and 'Profile Run' not in out
        assert 'Success: True' in out and '📊 Scenarios: 5' in out
        assert 'Batch Mode:' in out

    def test_profile_run_header(self):
        header = _header(scenario_count=3, is_profile=True, app_config=AppConfigManager())
        header._run_meta.symbols = ['BTCUSD', 'ETHUSD']
        buf = io.StringIO()
        with redirect_stdout(buf):
            header.render(ConsoleRenderer())
        out = re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())
        assert 'Profile Run (3 blocks, 2 symbol(s))' in out
