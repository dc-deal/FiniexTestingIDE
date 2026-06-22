"""
Warnings & errors report builder (#391/#395) — the unified warnings/errors postprocessor.

Reads the **already-decided** structured truth and maps it to `WarningsErrorsReport`:
- Tier-1 major warnings ← `ValidationResult.warnings` (per-scenario + the batch-level channel);
- Tier-2 minor warnings ← the log WARNING pot (summarized);
- errors ← `ValidationResult.errors` + the `ProcessResult` villain + the log ERROR pot;
- outcome ← failed-scenario rollup (sim) / shutdown + emergency (live).

**Batch-direct** (NOT via `RunUnit`): failed scenarios carry no `RunUnit`, and the warnings live on
the scenario / batch validation channels. The builder makes NO decisions — every verdict was produced
by a validator upstream (pre-run phases + `PostRunValidator`). See docs/architecture/warnings_errors_tiers.md.
"""

from python.framework.types.api.report_types import (
    UnitErrorRow, WarningRow, WarningsErrorsOutcome, WarningsErrorsReport)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.log_level import LogLevel
from python.framework.types.process_data_types import ProcessResult


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
        WarningsErrorsReport — live has no validation channel: warnings are the session WARNING
        buffer (Tier 2), errors the session ERROR buffer + emergency_reason (the villain)
    """
    # Tier-2 (minor) — the session WARNING buffer. Strip the logger prefix
    # ('[  4s] WARNING | msg' → 'msg') so the model carries the clean fact, not the log line.
    warnings = [
        WarningRow(tier='minor', scope=name, message=(m.split(' | ', 1)[-1] if ' | ' in m else m))
        for m in result.warning_messages]

    # Errors — the session ERROR buffer (pot) + the emergency villain
    errors = []
    if result.error_messages or result.emergency_reason:
        errors.append(UnitErrorRow(
            name=name, symbol=symbol,
            error_message=result.emergency_reason or '',
            logged_errors=list(result.error_messages)))

    outcome = WarningsErrorsOutcome(
        failed_count=1 if result.emergency_reason else 0,
        total_units=1,
        failed_unit_names=[name] if result.emergency_reason else [],
        first_failure_name=name if result.emergency_reason else '',
        first_failure_error=result.emergency_reason or '',
        emergency_reason=result.emergency_reason or '',
        shutdown_mode=result.shutdown_mode)
    return WarningsErrorsReport(warnings=warnings, errors=errors, outcome=outcome)


def _batch_warnings(batch: BatchExecutionSummary) -> list:
    """Tier-1 major (validation channels) + a Tier-2 minor summary of the log WARNING pot."""
    warnings = []

    # Tier 1 — run-scoped (batch-global) validation results, e.g. debug-mode (PostRunValidator)
    for vr in batch.batch_validation_result:
        for msg in vr.warnings:
            warnings.append(WarningRow(tier='major', scope='run', message=msg))

    # Tier 1 — per-scenario validation warnings (pre-run validators, e.g. account-currency advisory)
    for scenario in batch.single_scenario_list:
        for vr in scenario.validation_result:
            if not vr.is_valid:
                continue
            for msg in vr.warnings:
                warnings.append(WarningRow(tier='major', scope=scenario.name, message=msg))

    # Tier 2 — the log WARNING pot, summarized (ignorable; the raw lines stay in the scenario logs)
    pot_total, pot_units = _log_pot_summary(batch, LogLevel.WARNING)
    if pot_total > 0:
        warnings.append(WarningRow(
            tier='minor', scope='run',
            message=(f"{pot_total} warning(s) in {pot_units} scenario log(s) "
                     f"— see scenario logs for details")))
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
        failed_count=len(failed),
        total_units=len(results),
        failed_unit_names=[r.scenario_name for r in failed],
        first_failure_name=first.scenario_name if first else '',
        first_failure_error=(first.error_message or '') if first else '')


def _logged(result: ProcessResult, level: LogLevel) -> list:
    """Extract the buffered log lines of one level from a scenario's logger buffer."""
    if not result.scenario_logger_buffer:
        return []
    return [line for lvl, line in result.scenario_logger_buffer if lvl == level]


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
