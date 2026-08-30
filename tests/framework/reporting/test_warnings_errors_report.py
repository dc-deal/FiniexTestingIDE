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

from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_batch,
    build_warnings_errors_report_from_session,
)
from python.framework.reporting.console.warnings_summary import WarningsSummary
from python.framework.reporting.io.warnings_errors_report_io import (
    write_warnings_errors_report,
)
from python.framework.types.api.report_types import (
    LogEntryRow,
    UnitErrorRow,
    WarningRow,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
    WarningTier,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.log_level import LogLevel
from python.framework.types.log_record_types import LogRecord
from python.framework.types.process_data_types import LOGGED_ERRORS_TYPE, ProcessResult
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.utils.console_renderer import ConsoleRenderer

_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _scenario(name, idx, symbol, val_results=None) -> SingleScenario:
    s = SingleScenario(
        name=name, scenario_index=idx, symbol=symbol, data_broker_type='kraken_spot', start_date=_DT)
    for vr in (val_results or []):
        s.validation_result.append(vr)
    return s


def _record(level, message: str) -> LogRecord:
    """One buffered log entry as the loggers now produce it."""
    return LogRecord(level=level, timestamp=_DT, scope='s1', message=message)


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
                       batch_validation_result=[ValidationResult('run', [ValidationFinding(
                           severity=Severity.WARNING, check='debug_mode',
                           domain=ValidationDomain.SETUP, message='DEBUG MODE — ...',
                           scope='run')])])
        report = build_warnings_errors_report_from_batch(batch)
        major = [w for w in report.warnings if w.tier == 'major']
        assert any(w.scope == 'run' and 'DEBUG MODE' in w.message for w in major)

    def test_per_scenario_major_warning(self):
        scenario = _scenario('s1', 0, 'BTCUSD', val_results=[
            ValidationResult('s1', [ValidationFinding(
                severity=Severity.WARNING, check='account_currency_normalized',
                domain=ValidationDomain.CONFIG, message='account_currency normalized',
                scope='s1')])])
        report = build_warnings_errors_report_from_batch(_batch([_result('s1', 0)], [scenario]))
        major = [w for w in report.warnings if w.tier == 'major' and w.scope == 's1']
        assert len(major) == 1 and 'account_currency' in major[0].message

    def test_minor_warning_summary_from_log_pot(self):
        buffer = [_record(LogLevel.WARNING, 'w1'), _record(LogLevel.WARNING, 'w2'),
                  _record(LogLevel.INFO, 'i1')]
        report = build_warnings_errors_report_from_batch(
            _batch([_result('s1', 0, buffer=buffer)], [_scenario('s1', 0, 'BTCUSD')]))
        minor = [w for w in report.warnings if w.tier == 'minor']
        assert len(minor) == 1 and '2 warning(s)' in minor[0].message

    def test_error_from_villain_and_validation(self):
        scenario = _scenario('bad', 0, 'BTCUSD', val_results=[
            ValidationResult('bad', [ValidationFinding(
                severity=Severity.ERROR, check='data_availability',
                domain=ValidationDomain.DATA, message='start before data', scope='bad')])])
        result = _result('bad', 0, success=False, error_type='ValidationError',
                         error_message='failed', buffer=[_record(LogLevel.ERROR, 'e1')])
        report = build_warnings_errors_report_from_batch(_batch([result], [scenario]))
        assert len(report.errors) == 1
        err = report.errors[0]
        assert err.error_type == 'ValidationError'
        assert err.validation_errors == ['start before data']
        assert [e.message for e in err.logged_errors] == ['e1']

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

    def test_outcome_carries_the_canonical_grading(self):
        """
        The model carries the grading itself (#372), not only the counts.

        Without it every surface downstream — artifact, store, API — would have to decide
        again what the counts mean, which is the second derivation the pipeline forbids.
        """
        clean = _batch([_result('ok', 0)], [_scenario('ok', 0, 'BTCUSD')])
        assert build_warnings_errors_report_from_batch(clean).outcome.run_outcome == \
            RunOutcome.SUCCESS.value

        crashed = _batch(
            [_result('bad', 0, success=False, error_type='ValueError', error_message='boom')],
            [_scenario('bad', 0, 'BTCUSD')])
        assert build_warnings_errors_report_from_batch(crashed).outcome.run_outcome == \
            RunOutcome.FAILED.value

        pot = _batch(
            [_result('noisy', 0, success=False, error_type=LOGGED_ERRORS_TYPE,
                     error_message='Scenario logged 2 ERROR(s)')],
            [_scenario('noisy', 0, 'BTCUSD')])
        assert build_warnings_errors_report_from_batch(pot).outcome.run_outcome == \
            RunOutcome.FINISHED_WITH_ERRORS.value


class TestNoRenderingReachesTheArtifact:
    """
    The buffer used to hold a rendered console line, ANSI codes included, and `_logged()` copied
    it straight into `UnitErrorRow.logged_errors` — so the persisted JSON, which is the API's own
    source, carried terminal escape sequences. Records carry the bare message, so it cannot.
    """

    _ANSI = re.compile(r'\x1b\[[0-9;]*m')

    def test_a_logged_error_reaches_the_artifact_clean(self, tmp_path):
        buffer = [_record(LogLevel.ERROR, 'Broker rejected order')]
        report = build_warnings_errors_report_from_batch(_batch(
            [_result('s1', 0, success=False, error_type='X', error_message='boom', buffer=buffer)],
            [_scenario('s1', 0, 'BTCUSD')]))

        entry = report.errors[0].logged_errors[0]
        assert entry.message == 'Broker rejected order'
        # The fields the record carried survive DERIVE — that is what the artifact is for.
        assert entry.level == LogLevel.ERROR and entry.scope == 's1'

        path = write_warnings_errors_report(report, tmp_path)
        raw = path.read_bytes()
        assert b'\x1b[' not in raw, 'terminal escape codes must never reach the artifact'
        assert not self._ANSI.search(raw.decode('utf-8'))

    def test_the_message_needs_no_unpicking(self):
        """No level column, no timestamp, no tick prefix — nothing left to split off."""
        buffer = [_record(LogLevel.WARNING, 'signal feed stale')]
        report = build_warnings_errors_report_from_batch(
            _batch([_result('s1', 0, buffer=buffer)], [_scenario('s1', 0, 'BTCUSD')]))
        pot = [w for w in report.warnings if w.tier == 'minor'][0]
        assert ' | ' not in pot.message


class TestOriginSurvivesToTheModel:
    """The finding's origin must reach WarningRow — it used to be dropped at the builder."""

    def test_check_and_domain_reach_the_row(self):
        batch = _batch([_result('s1', 0)], [_scenario('s1', 0, 'BTCUSD')],
                       batch_validation_result=[ValidationResult('run', [ValidationFinding(
                           severity=Severity.WARNING, check='budget_granularity',
                           domain=ValidationDomain.PROFILING, message='budget below granularity',
                           scope='run')])])
        row = build_warnings_errors_report_from_batch(batch).warnings[0]
        assert row.check == 'budget_granularity' and row.domain == 'profiling'
        assert row.scope == 'run' and row.tier == 'major'

    def test_an_advisory_is_kept_when_the_same_result_also_rejects(self):
        """A mixed result yields both — the advisory is no longer discarded with the rejection."""
        scenario = _scenario('s1', 0, 'BTCUSD', val_results=[ValidationResult('s1', [
            ValidationFinding(
                severity=Severity.ERROR, check='signal_availability',
                domain=ValidationDomain.DATA, message='signal source missing', scope='s1'),
            ValidationFinding(
                severity=Severity.WARNING, check='signal_availability',
                domain=ValidationDomain.DATA, message='signal coverage partial', scope='s1'),
        ])])
        report = build_warnings_errors_report_from_batch(_batch([_result('s1', 0)], [scenario]))
        major = [w for w in report.warnings if w.tier == 'major']
        assert [w.message for w in major] == ['signal coverage partial']
        assert major[0].domain == 'data' and major[0].scope == 's1'

    def test_a_log_pot_row_claims_no_assertion(self):
        """The channel is the TIER's answer; `check` means which assertion, and there is none."""
        buffer = [_record(LogLevel.WARNING, 'w1')]
        report = build_warnings_errors_report_from_batch(
            _batch([_result('s1', 0, buffer=buffer)], [_scenario('s1', 0, 'BTCUSD')]))
        minor = [w for w in report.warnings if w.tier == WarningTier.LOGGER_PRODUCED]
        assert len(minor) == 1
        assert minor[0].check == '', 'no assertion decided a log line'
        assert minor[0].domain == '', 'a log line belongs to no validator domain'


class TestBuildFromSession:
    def test_live_warnings_and_errors(self):
        result = AutoTraderResult(
            shutdown_mode='emergency', emergency_reason='balance breach',
            session_logger_buffer=[_record(LogLevel.WARNING, 'stale tick'),
                                   _record(LogLevel.WARNING, 'reconnect'),
                                   _record(LogLevel.ERROR, 'order rejected')])
        report = build_warnings_errors_report_from_session(result, 'dotusd_live', 'DOTUSD')
        assert [w.tier for w in report.warnings] == ['minor', 'minor']
        assert all(w.scope == 'dotusd_live' for w in report.warnings)
        assert len(report.errors) == 1
        assert report.errors[0].error_message == 'balance breach'
        assert [e.message for e in report.errors[0].logged_errors] == ['order rejected']
        assert report.outcome.shutdown_mode == 'emergency'
        assert report.outcome.emergency_reason == 'balance breach'

    def test_live_clean_session(self):
        report = build_warnings_errors_report_from_session(AutoTraderResult(), 'p', 'BTCUSD')
        assert report.warnings == [] and report.errors == []
        assert report.outcome.shutdown_mode == 'normal' and report.outcome.failed_count == 0

    def test_live_outcome_carries_the_canonical_grading(self):
        """The live half stamps the same field, so both pipelines answer identically."""
        clean = build_warnings_errors_report_from_session(AutoTraderResult(), 'p', 'BTCUSD')
        assert clean.outcome.run_outcome == RunOutcome.SUCCESS.value

        emergency = build_warnings_errors_report_from_session(
            AutoTraderResult(shutdown_mode='emergency', emergency_reason='balance breach'),
            'p', 'BTCUSD')
        assert emergency.outcome.run_outcome == RunOutcome.FAILED.value

        pot = build_warnings_errors_report_from_session(
            AutoTraderResult(shutdown_mode='normal',
                             session_logger_buffer=[_record(LogLevel.ERROR, 'order rejected')]),
            'p', 'BTCUSD')
        assert pot.outcome.run_outcome == RunOutcome.FINISHED_WITH_ERRORS.value

    def test_operator_stop_is_told_apart_from_a_crash(self):
        """Ctrl+C also arrives as 'emergency', so the outcome carries the discriminator."""
        interrupted = build_warnings_errors_report_from_session(
            AutoTraderResult(shutdown_mode='emergency', operator_interrupted=True),
            'p', 'BTCUSD')
        assert interrupted.outcome.shutdown_mode == 'emergency'
        assert interrupted.outcome.operator_interrupted is True
        assert interrupted.outcome.run_outcome == RunOutcome.SUCCESS.value
        assert interrupted.outcome.emergency_reason == ''

        crashed = build_warnings_errors_report_from_session(
            AutoTraderResult(shutdown_mode='emergency', emergency_reason='tick loop died'),
            'p', 'BTCUSD')
        assert crashed.outcome.operator_interrupted is False
        assert crashed.outcome.run_outcome == RunOutcome.FAILED.value

    def test_sim_outcome_leaves_the_live_only_fields_empty(self):
        """shutdown_mode '' on a sim run means 'not applicable', not 'unknown'."""
        report = build_warnings_errors_report_from_batch(_batch([], []))
        assert report.outcome.shutdown_mode == ''
        assert report.outcome.operator_interrupted is False


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
                                 validation_errors=['start before data'],
                                 logged_errors=[LogEntryRow(
                                     level=LogLevel.ERROR, observed_at=_DT,
                                     scope='bad', message='e1')])],
            outcome=WarningsErrorsOutcome(failed_count=1))
        out = self._render(report)
        assert 'WARNINGS & ERRORS' in out
        assert 'Scenario errors detected — 1 unit(s)' in out
        assert '✗ start before data' in out
        assert '1 logged error(s)' in out
        assert 'DEBUG MODE' in out
        assert '[s1] account_currency normalized' in out
        assert '3 warning(s) in 2 scenario log(s)' in out

    def test_empty_report_renders_zero_state(self):
        # Always rendered now (both pipelines) — a clean zero-state when there are none.
        out = self._render(WarningsErrorsReport())
        assert 'WARNINGS & ERRORS' in out
        assert 'No warnings or errors' in out
