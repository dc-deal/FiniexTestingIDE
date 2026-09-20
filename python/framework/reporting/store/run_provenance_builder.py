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
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.api.report_types import WarningsErrorsReport
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.data_origin_types import (
    is_admissible_for_measurement,
    joined_distinct,
)
from python.framework.types.log_layout_types import (
    RUN_TYPE_LIVE,
    RUN_TYPE_SIMULATION,
)
from python.framework.types.run_results_types import RunProvenance, SweepContext
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet, SingleScenario
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint
from python.framework.utils.git_info_utils import get_git_info


def build_run_provenance(
    batch_execution_summary: BatchExecutionSummary,
    scenario_set: ScenarioSet,
    run_id: str,
    sweep_context: Optional[SweepContext] = None,
    warnings_errors_report: Optional[WarningsErrorsReport] = None,
) -> Optional[RunProvenance]:
    """
    Build the run's provenance bundle for the results ledger.

    Args:
        batch_execution_summary: The finished batch (carries the scenarios + their config)
        scenario_set: The run's scenario set (name + run timestamp)
        run_id: The run this provenance describes — the minted id, never derived
            from the directory name: identity is a field, not a path (#475)
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
        run_id=run_id,
        run_timestamp=scenario_set.run_timestamp,
        scenario_set_name=scenario_set.scenario_set_name,
        app_version=AppConfigManager().get_version(),
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
        run_type=RUN_TYPE_SIMULATION,
        **consumption_record(scenarios),
    )


def build_run_provenance_from_session(
    config: AutoTraderConfig,
    run_id: str,
    run_timestamp: datetime,
    warnings_errors_report: Optional[WarningsErrorsReport] = None,
    deployment_id: str = '',
) -> RunProvenance:
    """
    Build a live session's provenance bundle for the results ledger.

    The live counterpart to build_run_provenance: the profile's strategy_config has the same
    shape as a sim scenario's, so the param_hash + component versions are directly comparable
    to the backtest. A live session is never swept (sweep tagging stays None); an emergency
    (total failure) → ledger status 'error'.

    Args:
        config: The autotrader profile config (strategy_config + name + symbol + broker)
        run_id: The session this provenance describes — the minted id, never
            derived from the directory name: identity is a field, not a path (#475)
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
        run_id=run_id,
        run_timestamp=run_timestamp,
        scenario_set_name=config.name or config.symbol,
        app_version=AppConfigManager().get_version(),
        git_commit=git.commit if git else None,
        git_branch=git.branch if git else None,
        git_dirty=git.dirty if git else False,
        decision_logic_type=strategy_config.get('decision_logic_type', ''),
        decision_version=decision_version,
        worker_versions=worker_versions,
        config_snapshot=json.dumps(strategy_config, sort_keys=True),
        symbols=[config.symbol],
        data_broker_type=config.broker_type,
        # A live session consumes a socket, not an archive, so the consumption record is empty
        # and `input_plane` is what says that on purpose rather than by omission.
        input_plane='stream',
        # The ONE exception to that emptiness, and the one place config is the right source:
        # a live session renders its bars at runtime from `tick.price`, so there is no file to
        # carry a stamp and nothing can be out of date with the declaration. Leaving it blank
        # would defeat the field — the parity proof has to compare the live basis against the
        # backtest's, and `input_plane='stream'` is what tells a reader this one is DECLARED
        # rather than measured (§31c).
        price_bases=MarketConfigManager().get_price_formation(config.broker_type).value,
        deployment_id=deployment_id,
        profile_hash=_profile_fingerprint(config),
        run_type=RUN_TYPE_LIVE,
    )


def _profile_fingerprint(config: AutoTraderConfig) -> str:
    """
    Fingerprint the OPERATIONAL half of a live profile — everything `param_hash` does not cover.

    Two hashes rather than one wide one, because they answer two questions and a value that
    answers both answers neither. `param_hash` covers `strategy_config` and is what #512
    compares a backtest against — widening it would make a raised stop level read as a
    different strategy and put the run beyond comparison for no reason. This one covers the
    rest: the safety thresholds, the order guard, the execution and tick-source settings, the
    capital declaration. Those change what a session DOES without changing what it decides,
    and a reader asking "why did the breaker not fire on day 19" is asking about exactly them.

    Derived from the loaded config rather than the file, so a value the loader resolved is
    fingerprinted as resolved. `config_path` is excluded: where the profile sits on disk is
    not a property of the run.

    Args:
        config: The loaded profile

    Returns:
        SHA256 hex digest over the operational sections
    """
    operational = {
        field.name: _plain(getattr(config, field.name))
        for field in fields(config)
        if field.name not in _NON_OPERATIONAL_FIELDS
    }
    return generate_config_fingerprint(operational)


def _plain(value: Any) -> Any:
    """
    Reduce one config value to something JSON can fingerprint deterministically.

    The blocks come in BOTH shapes — §6 puts config schemas on Pydantic while a few settings
    bundles stay dataclasses — so both are projected rather than one being assumed. Anything
    else falls back to `repr`, which is the one case worth stating: a value whose repr carries
    an address would make the fingerprint differ between two identical runs, so the fallback
    exists to keep the function total and not because such a value is expected here.

    Args:
        value: A config field's value — a scalar, a Pydantic block, or a settings dataclass

    Returns:
        A JSON-serialisable projection of it
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json')
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in sorted(value.items())}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


# What `profile_hash` deliberately leaves out. `strategy_config` belongs to `param_hash`
# and must not be counted twice — that is the whole point of having two. The rest are the
# run's own identity rather than its configuration: a renamed profile or a moved file is not
# an operational change.
_NON_OPERATIONAL_FIELDS = frozenset({
    'strategy_config', 'config_path', 'name', 'symbol', 'broker_type', 'scenario_settings',
})


def consumption_record(scenarios: List[SingleScenario]) -> Dict[str, Any]:
    """
    What the scenarios of one run actually read, flattened to a ledger row.

    Public because the benchmark certificate needs the same answer: a certificate records
    which CODE produced it and had no way to say which DATA it read, so one over development
    ticks looked identical to one over production ticks. Two derivations of that would be the
    pair §19 exists to prevent — and the second one would be the copy nobody updates.

    Reads the per-scenario lists the mount already fills — the same seam
    `data_format_version` has travelled since #520 — rather than resolving anything again.
    Resolving here would report today's registry against files imported under an older one,
    which is the mistake the whole provenance contract is built to avoid.

    The distinct values say WHAT was read; the two counts say how much, which the joined
    strings cannot because they collapse multiplicity. Both are needed: "this run read
    production data" and "this run read 3 development files out of 41" are different answers.

    Args:
        scenarios: The run's scenarios, after the mount filled their input lists

    Returns:
        The consumption fields of `RunProvenance`, ready to splat into its constructor
    """
    versions: List[str] = []
    classes: List[str] = []
    grades: List[str] = []
    bases: List[str] = []
    for scenario in scenarios:
        versions.extend(scenario.data_format_versions)
        classes.extend(scenario.origin_classes)
        grades.extend(scenario.origin_evidence_grades)
        bases.extend(scenario.price_bases)

    unstamped = sum(
        1 for origin_class, evidence in zip(classes, grades)
        if not is_admissible_for_measurement(origin_class, evidence))

    return {
        'input_plane': 'archive',
        'data_format_versions': joined_distinct(versions),
        'origin_classes': joined_distinct(classes),
        'origin_evidence_grades': joined_distinct(grades),
        'input_files': len(classes),
        'unstamped_input_files': unstamped,
        # Counted separately from `input_files` on purpose: the basis comes from the BAR
        # files and the file count from ticks plus signals, so one is not a subset of the
        # other and a shared count would describe neither.
        'price_bases': joined_distinct(bases),
    }


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
    reasons = [f'{e.name}: {e.error_message or e.error_type}'
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
