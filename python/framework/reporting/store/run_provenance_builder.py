"""
Run-provenance builder (#390) — assembles the ledger's per-run provenance.

Composes `RunProvenance` from a finished run: the `param_hash` (fingerprint of the
effective strategy_config), git state, component versions (resolved from the type strings
via the factories), the full config snapshot, and the optional sweep tagging. The sim
variant reads the batch + scenario set; the live variant (`build_run_provenance_from_session`)
reads the autotrader profile — its strategy_config has the same shape, so the param_hash is
directly comparable to the backtest (sim/live parity in the ledger). Version resolution is
best-effort — provenance must never crash the report phase (§33), so a lookup failure
degrades to an empty version, not an error.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.api.report_types import WarningsErrorsReport
from python.framework.types.run_results_types import RunProvenance, SweepContext
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint
from python.framework.utils.git_info_utils import get_git_info


def build_run_provenance(
    batch_execution_summary: BatchExecutionSummary,
    scenario_set: ScenarioSet,
    run_dir: Path,
    sweep_context: Optional[SweepContext] = None,
    warnings_errors_report: Optional[WarningsErrorsReport] = None,
) -> Optional[RunProvenance]:
    """
    Build the run's provenance bundle for the results ledger.

    Args:
        batch_execution_summary: The finished batch (carries the scenarios + their config)
        scenario_set: The run's scenario set (name + run timestamp)
        run_dir: The run's directory (its name is the run_id)
        sweep_context: Optional sweep tagging when run as a sweep combination
        warnings_errors_report: The run's warnings/errors report — its canonical outcome decides
            the ledger status/error (a total failure → 'error'); None → 'ok'

    Returns:
        The provenance bundle, or None if the batch has no scenarios
    """
    scenarios = batch_execution_summary.single_scenario_list
    if not scenarios:
        return None

    # param_hash spans ALL scenarios so a multi-scenario run with per-scenario param
    # differences is not collapsed to scenario[0]; a uniform run (every sweep) keeps the
    # single fingerprint. Component versions + snapshot read scenario[0] (the full
    # per-scenario config is preserved separately in the run dir's config snapshot).
    strategy_config = scenarios[0].strategy_config or {}
    fingerprints = sorted(
        generate_config_fingerprint(s.strategy_config or {}) for s in scenarios)
    param_hash = (fingerprints[0] if len(set(fingerprints)) == 1
                  else generate_config_fingerprint({'per_scenario': fingerprints}))
    decision_version, worker_versions = _resolve_versions(strategy_config)
    git = get_git_info()
    status, error = _run_status(warnings_errors_report)

    return RunProvenance(
        param_hash=param_hash,
        status=status,
        error=error,
        run_id=run_dir.name,
        run_timestamp=scenario_set.run_timestamp,
        scenario_set_name=scenario_set.scenario_set_name,
        git_commit=git.commit if git else None,
        git_branch=git.branch if git else None,
        git_dirty=git.dirty if git else False,
        decision_logic_type=strategy_config.get('decision_logic_type', ''),
        decision_version=decision_version,
        worker_versions=worker_versions,
        config_snapshot=json.dumps(strategy_config, sort_keys=True),
        symbols=sorted({s.symbol for s in scenarios}),
        data_broker_type=','.join(sorted({s.data_broker_type for s in scenarios})),
        sweep_id=sweep_context.sweep_id if sweep_context else None,
        sweep_params=sweep_context.sweep_params if sweep_context else None,
        sweep_objective=sweep_context.objective if sweep_context else None,
        sweep_maximize=sweep_context.maximize if sweep_context else None,
    )


def build_run_provenance_from_session(
    config: AutoTraderConfig,
    run_dir: Path,
    run_timestamp: datetime,
    warnings_errors_report: Optional[WarningsErrorsReport] = None,
) -> RunProvenance:
    """
    Build a live session's provenance bundle for the results ledger.

    The live counterpart to build_run_provenance: the profile's strategy_config has the same
    shape as a sim scenario's, so the param_hash + component versions are directly comparable
    to the backtest. A live session is never swept (sweep tagging stays None); an emergency
    (total failure) → ledger status 'error'.

    Args:
        config: The autotrader profile config (strategy_config + name + symbol + broker)
        run_dir: The session's run directory (its name is the run_id)
        run_timestamp: The session start (UTC)
        warnings_errors_report: The session's warnings/errors report — a total failure
            (emergency) decides status 'error'; None → 'ok'

    Returns:
        The provenance bundle
    """
    strategy_config = config.strategy_config or {}
    decision_version, worker_versions = _resolve_versions(strategy_config)
    git = get_git_info()
    status, error = _run_status(warnings_errors_report)

    return RunProvenance(
        param_hash=generate_config_fingerprint(strategy_config),
        status=status,
        error=error,
        run_id=run_dir.name,
        run_timestamp=run_timestamp,
        scenario_set_name=config.name or config.symbol,
        git_commit=git.commit if git else None,
        git_branch=git.branch if git else None,
        git_dirty=git.dirty if git else False,
        decision_logic_type=strategy_config.get('decision_logic_type', ''),
        decision_version=decision_version,
        worker_versions=worker_versions,
        config_snapshot=json.dumps(strategy_config, sort_keys=True),
        symbols=[config.symbol],
        data_broker_type=config.broker_type,
    )


def _run_status(report: Optional[WarningsErrorsReport]) -> Tuple[str, Optional[str]]:
    """
    Map the canonical run outcome to the ledger (status, error).

    Error-flag only a TOTAL failure (every unit failed — e.g. an out-of-range parameter
    combination); a partial / hybrid run keeps its usable data ('ok'). Reuses the
    `WarningsErrorsOutcome` the executive headline reads — no re-scan.

    Args:
        report: The run's warnings/errors report (None → 'ok')

    Returns:
        (status, error) — ('error', reason) on a total failure, else ('ok', None)
    """
    if report is None:
        return 'ok', None
    outcome = report.outcome
    if outcome.failed_count == 0 or outcome.failed_count < outcome.total_units:
        return 'ok', None
    reasons = [f"{e.name}: {e.error_message or e.error_type}"
               for e in report.errors if e.error_message or e.error_type]
    return 'error', '; '.join(reasons) if reasons else (
        outcome.first_failure_error or 'run produced no usable data')


def _resolve_versions(strategy_config: Dict) -> Tuple[str, Dict[str, str]]:
    """Resolve decision + worker ComponentMetadata versions from the type strings (best-effort)."""
    logger = get_global_logger()
    decision_version = ''
    worker_versions: Dict[str, str] = {}

    decision_type = strategy_config.get('decision_logic_type', '')
    if decision_type:
        try:
            decision_cls, _ = DecisionLogicFactory(logger).resolve_logic_class(decision_type)
            decision_version = decision_cls.get_metadata().version
        except (ValueError, ImportError, OSError):
            # Provenance is non-critical — a missing/unresolvable component degrades to an empty
            # version; only the factory-resolution failure modes are swallowed (§33: real code
            # bugs in get_metadata still propagate).
            logger.debug(f"Could not resolve decision version for '{decision_type}'")

    worker_factory = WorkerFactory(logger)
    for name, worker_type in strategy_config.get('worker_instances', {}).items():
        try:
            worker_cls, _ = worker_factory.resolve_worker_class(worker_type)
            worker_versions[name] = worker_cls.get_metadata().version
        except (ValueError, ImportError, OSError):
            logger.debug(f"Could not resolve worker version for '{name}' ({worker_type})")

    return decision_version, worker_versions
