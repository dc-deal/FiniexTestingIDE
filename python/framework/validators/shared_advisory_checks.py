"""
FiniexTestingIDE - Shared Advisory Checks

The post-run advisory checks BOTH pipelines can make. Each is a pure function over its raw
inputs that RETURNS findings; the caller routes them into its own validation channel —
`BatchExecutionSummary.batch_validation_result` (sim) or
`AutoTraderResult.session_validation_result` (live).

Functions rather than a shared validator base class: only two of the sim's thirteen checks
apply to a live session, and a function over its inputs is testable without building a batch.
Same division the reporting pipeline already uses — shared derivation, pipeline-specific
coordinators.
"""

from typing import Dict, List, Optional, Tuple

from python.framework.types.trading_env_types.stress_test_types import StressTestConfig
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)

# Scope of a run-global finding — it concerns the run, not one unit.
_RUN_SCOPE = 'run'
# Component-cost verdict threshold — a worker or decision logic averaging more than this
# per call is worth optimizing (was an inline recommendation in the performance report).
SLOW_COMPONENT_MS = 1.0


def _finding(check: str, domain: ValidationDomain, message: str) -> ValidationFinding:
    """
    Build a run-scoped advisory finding.

    Args:
        check: Stable identifier of the assertion that produced the finding
        domain: The area it belongs to
        message: Operator-readable text

    Returns:
        The advisory ValidationFinding
    """
    return ValidationFinding(
        severity=Severity.WARNING, check=check, domain=domain, message=message,
        scope=_RUN_SCOPE)


def check_stress_test(
    units: List[Tuple[str, Optional[dict]]], unit_label: str
) -> Optional[ValidationFinding]:
    """
    Warn when any unit has active stress tests (results contain intentional errors).

    Args:
        units: One (unit name, raw stress config) pair per unit — scenarios in a sim batch,
            the single profile in a live session
        unit_label: What a unit is called in the message ('Scenarios' / 'Session')

    Returns:
        The finding, or None when no unit carries an enabled stress config
    """
    config_groups: dict[str, list[str]] = {}
    for unit_name, raw_config in units:
        config = StressTestConfig.from_dict(raw_config)
        if not config.has_any_enabled():
            continue
        parts = []
        if config.reject_open_order and config.reject_open_order.enabled:
            ro = config.reject_open_order
            parts.append(
                f'reject_open_order: probability={ro.probability:.0%}, seed={ro.seed}')
        if config.stale_data_stress and config.stale_data_stress.enabled:
            sd = config.stale_data_stress
            # Name the windows, not just their count: this is the INTENT half of the
            # record ("what was planned"). What the run actually experienced is the
            # feed-stability section (#451) — deliberately a different source.
            windows = ' | '.join(
                f"'{e.label}' on {e.data_source} "
                f"{e.stale_start_date.isoformat()} → {e.stale_end_date.isoformat()}"
                for e in sd.events)
            parts.append(
                f'stale_data_stress: {len(sd.events)} planned window(s) — {windows}')
        signature = ' | '.join(parts)
        config_groups.setdefault(signature, []).append(unit_name)

    if not config_groups:
        return None

    lines = ['STRESS TEST ACTIVE — Results contain INTENTIONAL errors and rejections!']
    for signature, unit_names in config_groups.items():
        lines.append(f'  → {signature}')
        lines.append(f"    {unit_label} ({len(unit_names)}): {', '.join(unit_names)}")
    return _finding('stress_test', ValidationDomain.SETUP, '\n'.join(lines))


def check_slow_components(
    worker_times: Dict[str, List[float]], logic_times: Dict[str, List[float]]
) -> List[ValidationFinding]:
    """
    Warn when a worker or decision logic averages more than the slow-component threshold.

    Args:
        worker_times: Per-worker average times in ms, one entry per unit that ran it
        logic_times: Per-decision-logic average times in ms, one entry per unit that ran it

    Returns:
        One finding per component kind that exceeded the threshold (empty when none did)
    """
    findings = []
    for label, times in (('worker', worker_times), ('decision logic', logic_times)):
        if not times:
            continue
        # Mean per component across units, then the worst of them — the same reading the
        # performance report shows, so the advisory and the table cannot disagree.
        name, avg = max(
            ((n, sum(t) / len(t)) for n, t in times.items()), key=lambda pair: pair[1])
        if avg <= SLOW_COMPONENT_MS:
            continue
        findings.append(_finding('slow_component', ValidationDomain.PERFORMANCE, (
            f"Slowest {label} '{name}' averages {avg:.3f}ms per call "
            f'(threshold {SLOW_COMPONENT_MS:.1f}ms) — candidate for optimization')))
    return findings
