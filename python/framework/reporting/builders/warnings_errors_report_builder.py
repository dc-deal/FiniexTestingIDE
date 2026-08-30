"""
Warnings & errors report builder (#391/#395) — the unified warnings/errors postprocessor.

Reads the **already-decided** structured truth and maps it to `WarningsErrorsReport`:
- Tier-1 major warnings ← the advisory `ValidationFinding`s of the per-scenario and
  batch-level validation channels (sim) / the session validation channel (live), each
  carrying its own origin (`check` / `domain`);
- Tier-2 minor warnings ← the log WARNING pot (summarized);
- errors ← `ValidationResult.errors` + the `ProcessResult` villain + the log ERROR pot;
- outcome ← failed-scenario rollup (sim) / shutdown + emergency (live), plus the canonical
  `run_outcome` grading (#372) stamped from the pipeline's own result object.

**Batch-direct** (NOT via `RunUnit`): failed scenarios carry no `RunUnit`, and the warnings live on
the scenario / batch validation channels. The builder makes NO decisions — every verdict was produced
by a validator upstream (pre-run phases + `PostRunValidator`). See docs/architecture/warnings_errors_tiers.md.
"""

from python.framework.types.api.report_types import (
    UnitErrorRow,
    WarningRow,
    WarningsErrorsOutcome,
    WarningsErrorsReport,
    WarningTier,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.log_level import LogLevel
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.validation_types import Severity, ValidationResult


def build_warnings_errors_report_from_batch(batch: BatchExecutionSummary) -> WarningsErrorsReport:
    """
    Build the report from a sim batch.

    Args:
        batch: The completed batch summary (validation channels + process results)

    Returns:
        WarningsErrorsReport (warnings + errors + run-level outcome)
    """
    warnings = _batch_warnings(batch)
    errors = _batch_errors(batch)
    outcome = _batch_outcome(batch)
    return WarningsErrorsReport(warnings=warnings, errors=errors, outcome=outcome)


def build_warnings_errors_report_from_session(
    result: AutoTraderResult, name: str, symbol: str) -> WarningsErrorsReport:
    """
    Build the report for a live session.

    Args:
        result: The collected session result
        name: Unit label (profile name / symbol)
        symbol: Traded symbol

    Returns:
        WarningsErrorsReport — Tier-1 from the session validation channel, Tier-2 the session
        WARNING buffer, errors the session ERROR buffer + emergency_reason (the villain)
    """
    # Tier 1 — the session's post-run advisories (SessionPostRunValidator). Same rows as the
    # batch channel produces, so both pipelines render and serve one shape.
    warnings = []
    for vr in result.session_validation_result:
        warnings.extend(_warning_rows(vr, 'run'))

    # Tier-2 — the session WARNING buffer. The message arrives unrendered from the LogRecord,
    # so there is nothing to strip. check/domain stay empty: no assertion decided this, and the
    # channel is already named by the tier.
    warnings.extend(
        WarningRow(tier=WarningTier.LOGGER_PRODUCED, scope=name, message=m)
        for m in result.warning_messages)

    # Errors — the session ERROR buffer (pot) + the emergency villain
    errors = []
    if result.error_messages or result.emergency_reason:
        errors.append(UnitErrorRow(
            name=name, symbol=symbol,
            error_message=result.emergency_reason or '',
            logged_errors=list(result.error_messages)))

    outcome = WarningsErrorsOutcome(
        run_outcome=result.get_outcome().value,
        failed_count=1 if result.emergency_reason else 0,
        total_units=1,
        failed_unit_names=[name] if result.emergency_reason else [],
        first_failure_name=name if result.emergency_reason else '',
        first_failure_error=result.emergency_reason or '',
        emergency_reason=result.emergency_reason or '',
        shutdown_mode=result.shutdown_mode,
        operator_interrupted=result.operator_interrupted)
    return WarningsErrorsReport(warnings=warnings, errors=errors, outcome=outcome)


def _warning_rows(result: ValidationResult, scope: str) -> list:
    """
    The advisory findings of one validation result as Tier-1 rows.

    A finding carries its own severity, so a result holding errors AND advisories yields both
    — the advisories are no longer dropped along with the rejection.

    Args:
        result: The validation result to read
        scope: Fallback scope when the finding does not name one

    Returns:
        One WarningRow per advisory finding, each carrying its origin
    """
    return [
        WarningRow(tier=WarningTier.VALIDATOR_PRODUCED, scope=finding.scope or scope,
                   message=finding.message, check=finding.check, domain=finding.domain.value)
        for finding in result.findings if finding.severity is Severity.WARNING]


def _batch_warnings(batch: BatchExecutionSummary) -> list:
    """Tier-1 major (validation channels) + a Tier-2 minor summary of the log WARNING pot."""
    warnings = []

    # Tier 1 — run-scoped (batch-global) findings, e.g. debug-mode (PostRunValidator)
    for vr in batch.batch_validation_result:
        warnings.extend(_warning_rows(vr, 'run'))

    # Tier 1 — per-scenario validation warnings (pre-run validators, e.g. account-currency advisory)
    for scenario in batch.single_scenario_list:
        for vr in scenario.validation_result:
            warnings.extend(_warning_rows(vr, scenario.name))

    # Tier 2 — the log WARNING pot, summarized (ignorable; the raw lines stay in the scenario logs)
    pot_total, pot_units = _log_pot_summary(batch, LogLevel.WARNING)
    if pot_total > 0:
        warnings.append(WarningRow(
            tier=WarningTier.LOGGER_PRODUCED, scope='run',
            message=(f'{pot_total} warning(s) in {pot_units} scenario log(s) '
                     f'— see scenario logs for details')))
    return warnings


def _batch_errors(batch: BatchExecutionSummary) -> list:
    """One UnitErrorRow per scenario carrying any error (villain / validation / logged ERROR pot)."""
    errors = []
    for result in batch.process_result_list:
        scenario = batch.get_scenario_by_process_result(result)
        validation_errors = [
            e for vr in scenario.validation_result if not vr.is_valid for e in vr.errors]
        logged_errors = _logged(result, LogLevel.ERROR)
        has_villain = bool(result.error_type or result.error_message)
        if not (validation_errors or logged_errors or has_villain):
            continue
        errors.append(UnitErrorRow(
            name=result.scenario_name,
            symbol=scenario.symbol,
            error_type=result.error_type or '',
            error_message=result.error_message or '',
            validation_errors=validation_errors,
            logged_errors=logged_errors,
            traceback=result.traceback or ''))
    return errors


def _batch_outcome(batch: BatchExecutionSummary) -> WarningsErrorsOutcome:
    """Run-level failed-scenario rollup (the Executive headline reads this)."""
    results = batch.process_result_list
    failed = [r for r in results if not r.success]
    first = failed[0] if failed else None
    return WarningsErrorsOutcome(
        run_outcome=batch.get_outcome().value,
        failed_count=len(failed),
        total_units=len(results),
        failed_unit_names=[r.scenario_name for r in failed],
        first_failure_name=first.scenario_name if first else '',
        first_failure_error=(first.error_message or '') if first else '')


def _logged(result: ProcessResult, level: LogLevel) -> list:
    """The messages of one level from a scenario's logger buffer, unrendered."""
    if not result.scenario_logger_buffer:
        return []
    return [record.message for record in result.scenario_logger_buffer
            if record.level == level]


def _log_pot_summary(batch: BatchExecutionSummary, level: LogLevel) -> tuple:
    """Total buffered lines of a level across scenarios + how many scenarios carried any."""
    total = 0
    units = 0
    for result in batch.process_result_list:
        n = len(_logged(result, level))
        if n > 0:
            total += n
            units += 1
    return total, units
