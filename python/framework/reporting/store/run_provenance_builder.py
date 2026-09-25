"""
Run-provenance builder (#390) — assembles the ledger's per-run provenance.

Composes `RunProvenance` from a finished run: the `param_hash` (fingerprint of the
effective strategy_config), git state, component versions, the full config snapshot, and the
optional sweep tagging. The sim variant reads the batch + scenario set; the live variant
(`build_run_provenance_from_session`) reads the autotrader profile — its strategy_config has the
same shape, so the param_hash is directly comparable to the backtest (sim/live parity in the
ledger).

The component versions, the dirty flag and the framework commit are READ from the run header's
code identity (#551), never derived here a second time. The header is written at the run's START,
so a session killed before its close still says which code it ran — and one derivation of that
fact cannot disagree with itself. The header is read from the run directory the CALLER holds,
never looked up in the run index: the index is derived and deletable (§44), and a provenance that
depended on it would lose its versions whenever it was rebuilt or removed. Reading is
best-effort: provenance must never crash the report phase (§33), so a header that cannot be read
degrades to unknown, never to an error — and says so in the run's OWN log, not only the global
one, because the ledger row cannot tell that state apart from a dirty tree by itself.
"""

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT, read_run_header
from python.framework.types.api.report_types import WarningsErrorsReport
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.data_origin_types import (
    is_admissible_for_measurement,
    joined_distinct,
)
from python.framework.types.git_info_types import GitInfo
from python.framework.types.log_layout_types import (
    RUN_TYPE_LIVE,
    RUN_TYPE_SIMULATION,
)
from python.framework.types.run_origin_types import CodeIdentity, ComponentRole
from python.framework.types.run_results_types import RunProvenance, SweepContext
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.scenario.scenario_set import ScenarioSet
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
    # The SIMULATION's operational half, filling the SAME column the live side fills with
    # `_profile_fingerprint` — one question, one field, both pipelines. `param_hash` above
    # covers what the bot DECIDES; this covers what the RUN DOES: the merged execution and
    # trade-simulator blocks, which carry the latency model, the slippage model, the RNG
    # seeds, the heartbeat interval and the tick budget.
    #
    # It must be the MERGED value and not the scenario set's own section. Both blocks cascade
    # THREE levels — app_config → global → scenario (scenario_config_loader.py:176-190) — and
    # the base layer lives in `app_config.json`, whose `user_configs/` override is gitignored.
    # So a change there moves neither `config_id` (which fingerprints the scenario set FILE)
    # nor `git_commit`, and the cycle's parity proof would compare two backtests whose
    # simulator differed with nothing recorded saying so. That is an unrecorded configuration
    # identity, which the goal statement calls a launch blocker rather than a nicety.
    operational = sorted(
        generate_config_fingerprint({
            'execution_config': s.execution_config or {},
            'trade_simulator_config': s.trade_simulator_config or {},
        }) for s in scenarios)
    # Spans ALL scenarios, by the same rule and for the same reason as `param_hash`.
    profile_hash = (operational[0] if len(set(operational)) == 1
                    else generate_config_fingerprint({'per_scenario': operational}))
    # Reported, when unreadable, in the run's summary log — the file an operator opens first,
    # and the same one the live side writes to.
    code_identity = _read_code_identity(
        run_id, scenario_set.logger.get_log_dir(), scenario_set.printed_summary_logger)
    decision_version, worker_versions = _versions_from_identity(code_identity, strategy_config)
    # Cheap by now: the code identity captured at the start already paid this repository's
    # `git status`, and the read is cached per process (§42). Only the BRANCH still comes from
    # here — the commit is the header's (`_framework_commit`).
    git = get_git_info()
    git_commit = _framework_commit(code_identity, git)
    status, error = _run_status(warnings_errors_report)

    return RunProvenance(
        param_hash=param_hash,
        profile_hash=profile_hash,
        status=status,
        error=error,
        run_id=run_id,
        run_timestamp=scenario_set.run_timestamp,
        scenario_set_name=scenario_set.scenario_set_name,
        app_version=AppConfigManager().get_version(),
        git_commit=git_commit,
        git_branch=_framework_branch(code_identity, git, git_commit),
        git_dirty=_is_dirty(code_identity),
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
        # One when nobody swept this run — the run WAS the only candidate. The live builder
        # below leaves the same default for the same reason, and neither writes an empty value:
        # "not swept" is an answer, "no field" is the absence of one.
        trial_count=sweep_context.trial_count if sweep_context else 1,
        run_type=RUN_TYPE_SIMULATION,
        **consumption_record(scenarios),
    )


def build_run_provenance_from_session(
    config: AutoTraderConfig,
    run_id: str,
    run_timestamp: datetime,
    warnings_errors_report: Optional[WarningsErrorsReport] = None,
    deployment_id: str = '',
    *,
    run_dir: Optional[Path],
    logger: AbstractLogger,
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
        deployment_id: The resolved deployment, '' when the session stands alone (#497)
        run_dir: The session's run directory, where its header was written at the start —
            required, because the run index that could also name it is derived and
            deletable (§44); None when the session has no run directory, which reads as unknown
        logger: The session's own logger — where an unreadable header is reported, so the
            operator finds it beside the session instead of only in the global log

    Returns:
        The provenance bundle
    """
    strategy_config = config.strategy_config or {}
    code_identity = _read_code_identity(run_id, run_dir, logger)
    decision_version, worker_versions = _versions_from_identity(code_identity, strategy_config)
    git = get_git_info()
    git_commit = _framework_commit(code_identity, git)
    status, error = _run_status(warnings_errors_report)

    return RunProvenance(
        param_hash=generate_config_fingerprint(strategy_config),
        status=status,
        error=error,
        run_id=run_id,
        run_timestamp=run_timestamp,
        scenario_set_name=config.name or config.symbol,
        app_version=AppConfigManager().get_version(),
        git_commit=git_commit,
        git_branch=_framework_branch(code_identity, git, git_commit),
        git_dirty=_is_dirty(code_identity),
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
        bot_id=config.bot_id,
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


def _read_code_identity(run_id: str, run_dir: Optional[Path],
                        logger: AbstractLogger) -> Optional[CodeIdentity]:
    """
    The code identity a run recorded in its header at its start (#551).

    Which runs reach the ledger decides what "missing" means here, and it was checked rather than
    assumed: both report coordinators write into the run directory the header was written into,
    a simulation always captures a code identity when it is commissioned to report — and only
    such a run is given a report coordinator — and a live session always captures one. So a
    header without one is either a run whose header could not be read, or a caller that built
    provenance for a run nobody started (a unit test). Both are UNKNOWN, and are said to be.

    Said in the run's OWN log, because the ledger row cannot say it: its `git_dirty` then reads
    True and its versions empty, which a reader cannot tell apart from a dirty tree and a
    component that declares no version. The line is what tells them apart.

    Args:
        run_id: The run the provenance describes
        run_dir: Its directory, as the caller holds it; None when the run has none
        logger: The run's own logger

    Returns:
        The recorded code identity, or None when the header is missing, unreadable, belongs to
        another run, or carries none
    """
    consequence = ('— the ledger row records it as UNKNOWN: git_dirty True and no component '
                   'versions, which here mean "not recorded", not "dirty" or "undeclared"')
    if run_dir is None:
        logger.warning(f'⚠️ Ledger provenance: run {run_id} has no run directory, so no header '
                       f'names its code {consequence}')
        return None
    try:
        header = read_run_header(Path(run_dir) / RUN_HEADER_ARTIFACT)
    except (OSError, ValueError) as error:
        logger.warning(f'⚠️ Ledger provenance: the header of run {run_id} could not be read '
                       f'({error}) {consequence}')
        return None
    if header.run_id != run_id or header.code_identity is None:
        logger.warning(f'⚠️ Ledger provenance: the header in {run_dir} carries no code identity '
                       f'for run {run_id} {consequence}')
        return None
    return header.code_identity


def _framework_commit(code_identity: Optional[CodeIdentity],
                      git: Optional[GitInfo]) -> Optional[str]:
    """
    The ledger's `git_commit`: this repository's commit as the header's code identity recorded it.

    One derivation of the fact (#551): the header's commit, its code identity and the ledger row
    all name the repository the code is imported FROM (`get_framework_root`), and the ledger
    takes the value the header already holds instead of reading git a second time. The live read
    is only the fallback for a run whose header could not say.

    Args:
        code_identity: The run's recorded code identity, or None when unknown
        git: The process's git read, or None when git could not answer

    Returns:
        The short commit hash, or None when neither source knows it
    """
    if code_identity is not None:
        # A recorded identity is the answer even where its commit is unknown: a missing commit
        # stays missing rather than being filled from a second, later read.
        return code_identity.framework.commit if code_identity.framework is not None else None
    return git.commit if git else None


def _framework_branch(code_identity: Optional[CodeIdentity], git: Optional[GitInfo],
                      git_commit: Optional[str]) -> Optional[str]:
    """
    The ledger's `git_branch` — the branch checked out at CAPTURE, beside the commit it belongs to.

    Taken from the header's code identity, which read it in the same breath as the commit. A live
    session writes this row at its END; a branch read then would describe whatever is checked out
    after thirty days, paired with the commit the session started from. Only a run without a
    recorded identity falls back to the process's read, and only where that read names the same
    commit the row records.

    Args:
        code_identity: The run's recorded code identity, or None when unknown
        git: The process's git read, or None when git could not answer
        git_commit: The commit the row records

    Returns:
        The branch name, or None
    """
    if code_identity is not None:
        return code_identity.framework.branch if code_identity.framework is not None else None
    if git is None or git.commit != git_commit:
        return None
    return git.branch


def _versions_from_identity(code_identity: Optional[CodeIdentity],
                            strategy_config: Dict) -> Tuple[str, Dict[str, str]]:
    """
    The decision and worker versions of ONE strategy_config, as its components were resolved.

    Filtered to the given config because the code identity spans every scenario of a run while
    the ledger's version columns describe the config its snapshot records. A component the
    factories could not resolve carries no source path and is left out, exactly as a failed
    resolution used to leave it out.

    Args:
        code_identity: The run's recorded code identity, or None when unknown
        strategy_config: The strategy_config the ledger row's snapshot records

    Returns:
        (decision version, worker instance name → version); empty when unknown
    """
    if code_identity is None:
        return '', {}
    decision_type = strategy_config.get('decision_logic_type', '')
    worker_types = strategy_config.get('worker_instances', {})
    decision_version = ''
    worker_versions: Dict[str, str] = {}
    for component in code_identity.components:
        if component.source_path is None:
            continue
        if component.role is ComponentRole.DECISION and component.type == decision_type:
            decision_version = component.version
        elif (component.role is ComponentRole.WORKER
              and worker_types.get(component.name) == component.type):
            worker_versions[component.name] = component.version
    return decision_version, worker_versions


def _is_dirty(code_identity: Optional[CodeIdentity]) -> bool:
    """
    The ledger's `git_dirty`: whether the code that ran cannot be reproduced from commits alone.

    Wider than it used to be (#551), and on purpose. It covered only this repository, so a run of
    a strategy living in an uncommitted algo repository read as clean. It now covers every
    repository a component came from, and an UNKNOWN state counts as dirty, because nothing says
    it was clean — the old default of `False` for "git could not answer" claimed exactly that.

    Args:
        code_identity: The run's recorded code identity, or None when unknown

    Returns:
        True when any repository was dirty, unversioned or unreadable, or nothing was recorded
    """
    if code_identity is None:
        return True
    return code_identity.is_dirty()
