"""
Warnings & Errors Report Builder + Render Tests (#391/#395).

The builder reads the already-decided structured truth (ValidationResult on the scenarios + the
batch-level channel, the ProcessResult villain, the log pots) and maps it to `WarningsErrorsReport`.
Tested with REAL BatchExecutionSummary / SingleScenario / ProcessResult / ValidationResult fixtures
and the real AutoTraderResult for the live builder. The render test feeds the real model into the
model-fed WarningsSummary.
"""

import io
import re
from contextlib import redirect_stdout
from datetime import datetime, timezone

from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.run_reports.warnings_errors_report_builder import (
    build_warnings_errors_report_from_batch, build_warnings_errors_report_from_session)
from python.framework.types.api.report_types import (
    UnitErrorRow, WarningRow, WarningsErrorsOutcome, WarningsErrorsReport)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.log_level import LogLevel
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import ValidationResult
from python.framework.utils.console_renderer import ConsoleRenderer

_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _scenario(name, idx, symbol, val_results=None) -> SingleScenario:
    s = SingleScenario(
        name=name, scenario_index=idx, symbol=symbol, data_broker_type='kraken_spot', start_date=_DT)
    for vr in (val_results or []):
        s.validation_result.append(vr)
    return s


def _result(name, idx, success=True, error_type='', error_message='', buffer=None) -> ProcessResult:
    return ProcessResult(
        success=success, scenario_name=name, scenario_index=idx, tick_loop_results=None,
        error_type=error_type, error_message=error_message, scenario_logger_buffer=buffer)


def _batch(results, scenarios, batch_validation_result=None) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios,
        batch_validation_result=batch_validation_result or [])


class TestBuildFromBatch:
    def test_run_scope_major_warning(self):
        batch = _batch([_result('s1', 0)], [_scenario('s1', 0, 'BTCUSD')],
                       batch_validation_result=[ValidationResult(
                           is_valid=True, scenario_name='debug_mode', warnings=['DEBUG MODE — ...'])])
        report = build_warnings_errors_report_from_batch(batch)
        major = [w for w in report.warnings if w.tier == 'major']
        assert any(w.scope == 'run' and 'DEBUG MODE' in w.message for w in major)

    def test_per_scenario_major_warning(self):
        scenario = _scenario('s1', 0, 'BTCUSD', val_results=[
            ValidationResult(is_valid=True, scenario_name='s1', warnings=['account_currency normalized'])])
        report = build_warnings_errors_report_from_batch(_batch([_result('s1', 0)], [scenario]))
        major = [w for w in report.warnings if w.tier == 'major' and w.scope == 's1']
        assert len(major) == 1 and 'account_currency' in major[0].message

    def test_minor_warning_summary_from_log_pot(self):
        buffer = [(LogLevel.WARNING, 'w1'), (LogLevel.WARNING, 'w2'), (LogLevel.INFO, 'i1')]
        report = build_warnings_errors_report_from_batch(
            _batch([_result('s1', 0, buffer=buffer)], [_scenario('s1', 0, 'BTCUSD')]))
        minor = [w for w in report.warnings if w.tier == 'minor']
        assert len(minor) == 1 and '2 warning(s)' in minor[0].message

    def test_error_from_villain_and_validation(self):
        scenario = _scenario('bad', 0, 'BTCUSD', val_results=[
            ValidationResult(is_valid=False, scenario_name='bad', errors=['start before data'])])
        result = _result('bad', 0, success=False, error_type='ValidationError',
                         error_message='failed', buffer=[(LogLevel.ERROR, 'e1')])
        report = build_warnings_errors_report_from_batch(_batch([result], [scenario]))
        assert len(report.errors) == 1
        err = report.errors[0]
        assert err.error_type == 'ValidationError'
        assert err.validation_errors == ['start before data']
        assert err.logged_errors == ['e1']

    def test_outcome_rollup(self):
        batch = _batch(
            [_result('ok', 0), _result('bad', 1, success=False, error_message='boom')],
            [_scenario('ok', 0, 'BTCUSD'), _scenario('bad', 1, 'ETHUSD')])
        outcome = build_warnings_errors_report_from_batch(batch).outcome
        assert outcome.failed_count == 1 and outcome.total_units == 2
        assert outcome.failed_unit_names == ['bad']
        assert outcome.first_failure_name == 'bad' and outcome.first_failure_error == 'boom'

    def test_no_warnings_no_errors(self):
        report = build_warnings_errors_report_from_batch(
            _batch([_result('s1', 0)], [_scenario('s1', 0, 'BTCUSD')]))
        assert report.warnings == [] and report.errors == []
        assert report.outcome.failed_count == 0


class TestBuildFromSession:
    def test_live_warnings_and_errors(self):
        result = AutoTraderResult(
            shutdown_mode='emergency', emergency_reason='balance breach',
            warning_messages=['stale tick', 'reconnect'], error_messages=['order rejected'])
        report = build_warnings_errors_report_from_session(result, 'dotusd_live', 'DOTUSD')
        assert [w.tier for w in report.warnings] == ['minor', 'minor']
        assert all(w.scope == 'dotusd_live' for w in report.warnings)
        assert len(report.errors) == 1
        assert report.errors[0].error_message == 'balance breach'
        assert report.errors[0].logged_errors == ['order rejected']
        assert report.outcome.shutdown_mode == 'emergency'
        assert report.outcome.emergency_reason == 'balance breach'

    def test_live_clean_session(self):
        report = build_warnings_errors_report_from_session(AutoTraderResult(), 'p', 'BTCUSD')
        assert report.warnings == [] and report.errors == []
        assert report.outcome.shutdown_mode == 'normal' and report.outcome.failed_count == 0


class TestRender:
    def _render(self, report: WarningsErrorsReport) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            WarningsSummary(report).render(ConsoleRenderer())
        return re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())

    def test_renders_errors_and_tiers(self):
        report = WarningsErrorsReport(
            warnings=[
                WarningRow(tier='major', scope='run', message='DEBUG MODE — timings unreliable'),
                WarningRow(tier='major', scope='s1', message='account_currency normalized'),
                WarningRow(tier='minor', scope='run', message='3 warning(s) in 2 scenario log(s)')],
            errors=[UnitErrorRow(name='bad', symbol='BTCUSD', error_type='ValidationError',
                                 validation_errors=['start before data'], logged_errors=['e1'])],
            outcome=WarningsErrorsOutcome(failed_count=1))
        out = self._render(report)
        assert 'WARNINGS & ERRORS' in out
        assert 'Scenario errors detected — 1 unit(s)' in out
        assert '✗ start before data' in out
        assert '1 logged error(s)' in out
        assert 'DEBUG MODE' in out
        assert '[s1] account_currency normalized' in out
        assert '3 warning(s) in 2 scenario log(s)' in out

    def test_empty_report_renders_nothing(self):
        assert self._render(WarningsErrorsReport()) == ''
