"""
FiniexTestingIDE - Shared Advisory Checks

The post-run advisory checks BOTH pipelines can make. Each is a pure function over its raw
inputs that RETURNS findings; the caller routes them into its own validation channel —
`BatchExecutionSummary.batch_validation_result` (sim) or
`AutoTraderResult.session_validation_result` (live).

Functions rather than a shared validator base class: almost none of the sim's post-run checks
apply to a single AutoTrader session (they need profiling data, a tick budget, several scenarios or
several currencies), and a function over its inputs is testable without building a batch. Same
division the reporting pipeline already uses — shared derivation, pipeline-specific coordinators.
"""

from collections import Counter
from typing import Dict, List, Optional, Tuple

from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.types.data_deficiency_types import (
    DEFICIENCY_DECLARATIONS,
    DataDeficiency,
)
from python.framework.types.data_origin_types import is_admissible_for_measurement
from python.framework.types.run_origin_types import CodeIdentity, RepositoryState
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.stress_test_types import StressTestConfig
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)

# Scope of a run-global finding — it concerns the run, not one unit.
_RUN_SCOPE = 'run'


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
            the single profile in an AutoTrader session
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
                f"'{e.label}' on {e.stale_data_source} "
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


# A component-cost advisory against a FIXED millisecond threshold used to live here and was
# removed deliberately: "slow" is only meaningful relative to the tick interval, and the
# grounded form of that question already exists as PostRunValidator._check_budget (avg tick
# processing vs the data's own P5 interval). The two could contradict each other in the same
# report — a fixed 1.0ms fired on a worker using 6% of a 50ms window, and stayed silent when
# eight sub-threshold workers together overran a 2ms one. A relative measure (share of tick
# time) would be the honest replacement; an absolute one cannot be.


def check_unversioned_code(code_identity: Optional[CodeIdentity]) -> Optional[ValidationFinding]:
    """
    Warn when the code a run executed lies under no version control, or git could not say (#551).

    A dirty repository is ordinary development and is not warned about: its commit plus the stored
    patch restore the code that ran. Code in NO repository has neither — typically a strategy placed
    in `user_algos/` before anyone ran `git init` there, which this repository ignores — so the run
    can never be reproduced, and the report says so every time until the directory is a repository.

    Args:
        code_identity: The run's recorded code identity, or None when none was captured

    Returns:
        The finding, or None when every repository the code came from is under version control
    """
    if code_identity is None:
        return None
    states: List[RepositoryState] = (
        ([code_identity.framework] if code_identity.framework else [])
        + list(code_identity.repositories))
    unversioned = [state.root for state in states if state.in_repository is False]
    unknown = [state.root for state in states if state.in_repository is None]
    if not unversioned and not unknown:
        return None
    lines = ['CODE WITHOUT VERSION CONTROL — this run cannot be reproduced from a commit']
    lines += [f'   {root} · not under version control' for root in unversioned]
    lines += [f'   {root} · state unknown (git unavailable or refused the checkout)'
              for root in unknown]
    if unversioned:
        lines.append('   Make each directory a repository once — `git init`, a `.gitignore` with '
                     '`__pycache__/` and `*.pyc`, then commit — and every later run records its '
                     'commit (and, for uncommitted work, a patch that restores it).')
    if unknown:
        lines.append('   Make git runnable for those directories (for a checkout another user '
                     'owns: `git config --global --add safe.directory <dir>`).')
    return _finding('unversioned_code', ValidationDomain.SETUP, '\n'.join(lines))


def check_order_event_stream(
    units: List[Tuple[str, int, int]], unit_label: str
) -> Optional[ValidationFinding]:
    """
    Warn when a unit's order-event stream lacks submissions its executor counted (#362).

    The two numbers come from ONE place — the executor counts a submission and records its
    event in the same statement — and then travel apart: the count in memory, the event through
    the stream (a file a live session writes line by line, a list a scenario hands back from its
    subprocess). So this catches a LOST record, which is what a failed write leaves behind, and
    never a wrong value: whatever is counted from the stream would be short by what went missing.

    Args:
        units: One (unit name, `submitted` events in its stream, submissions it counted) triple
            per unit
        unit_label: What a unit is called in the message ('Scenarios' / 'Session')

    Returns:
        The finding, or None when every unit's stream holds every submission
    """
    short = [(name, recorded, counted) for name, recorded, counted in units
             if recorded != counted]
    if not short:
        return None
    lines = [f'ORDER-EVENT STREAM INCOMPLETE — {unit_label}: the counters derived from it are '
             f'short by what is missing']
    lines += [f'   {name} · {recorded} submission(s) in the stream, {counted} counted'
              for name, recorded, counted in short]
    return _finding('order_event_stream_incomplete', ValidationDomain.EXECUTION, '\n'.join(lines))



def check_usable_funds(
    units: List[Tuple[str, Dict[str, float]]], unit_label: str
) -> Optional[ValidationFinding]:
    """
    Warn when a unit's funds in a currency fall below zero once its own unfilled orders count.

    The balance moved past a claim this bot still holds — most often a resting order adopted at
    boot, whose reserve the account had already spent. The order can no longer be filled in full
    from what the account holds, and the venue may reject it when it fills.

    Args:
        units: One (unit name, currency → balance less its claim) pair per unit
        unit_label: What a unit is called in the message ('Scenarios' / 'Session')

    Returns:
        The finding, or None when every claimed currency's funds stay at or above zero
    """
    short = [(name, currency, usable) for name, funds in units
             for currency, usable in funds.items() if usable < 0]
    if not short:
        return None
    lines = [f'FUNDS BELOW ZERO AFTER OWN ORDERS — {unit_label}: an unfilled order claims more '
             f'than the account holds; check the resting orders']
    lines += [f'   {name} · {currency} {usable:,.4f} usable'
              for name, currency, usable in short]
    return _finding('usable_funds_negative', ValidationDomain.PORTFOLIO, '\n'.join(lines))


def check_data_origin(scenarios: List[SingleScenario]) -> List[ValidationFinding]:
    """
    Report how much of what a run read carries no production stamp of its own.

    One finding for the whole run, deliberately. The per-scenario gate decides whether a
    scenario may run at all; this measures the distance to a state where a parity
    comparison is possible, and that distance is a property of the archive rather than of
    any one scenario. Reported as a count rather than a verdict: the data is usable, and it
    is not comparable, which are two different sentences.

    It stays silent once every file read carries a producer's own stamp — which is the
    state the thirty-day run needs and the only one that says nothing. An AutoTrader session
    passes the scenario it replayed; a live-adapter session reads a socket and passes none.

    Args:
        scenarios: The scenarios whose mounted input files to count

    Returns:
        The findings: external data read, data not producer-stamped — none, one or both
    """
    findings: List[ValidationFinding] = []
    total_files = 0
    unstamped_files = 0
    external_files = 0
    external_scenarios = set()
    for scenario in scenarios:
        for origin_class, evidence in zip(scenario.origin_classes,
                                          scenario.origin_evidence_grades):
            total_files += 1
            # A venue's published history IS stamped — by the converter that brought it in —
            # so it is said in a finding of its own rather than counted as unstamped, where
            # it would contradict that finding beside it.
            if origin_class == OriginClass.EXTERNAL.value:
                external_files += 1
                external_scenarios.add(scenario.name)
                continue
            # Asked of the shared rule rather than spelled out here. It decides whether
            # a measurement may use a file, and a second copy of that sentence would
            # eventually answer a different question than the first — with the copy
            # nobody reads being the one that decays.
            if not is_admissible_for_measurement(origin_class, evidence):
                unstamped_files += 1

    # A warmup reaching back into a venue's published history needs no admission — the
    # ticks traded on are what admission guards — so it is said here rather than refused.
    warmup_scenarios = sorted(
        f'{scenario.name} ({", ".join(scenario.external_warmup_timeframes)})'
        for scenario in scenarios
        if scenario.external_warmup_timeframes)
    if external_files:
        lines = [
            f'EXTERNAL DATA — {external_files} of {total_files} input file(s) are a venue\'s '
            f'published history, not recorded by our collectors',
            '  → never admissible for a parity measurement',
            f'  → scenarios: {", ".join(sorted(external_scenarios))}']
    elif warmup_scenarios:
        lines = [
            'EXTERNAL DATA — the warmup history reached back before our collection, into a '
            'venue\'s published history',
            '  → the ticks traded on are our own; the indicators started from bars rendered '
            'from external ticks']
    else:
        lines = []
    if warmup_scenarios:
        lines.append(f'  → warmup from external history: {", ".join(warmup_scenarios)}')
    if lines:
        findings.append(_finding('data_origin_external', ValidationDomain.DATA, '\n'.join(lines)))

    if unstamped_files == 0:
        return findings

    findings.append(_finding('data_origin_unstamped', ValidationDomain.DATA, (
        f'Data origin not producer-stamped for {unstamped_files}/{total_files} file(s) — '
        f'usable, and NOT comparable\n'
        f'  → a parity measurement reads only data a producer stamped itself\n'
        f'  → docs/architecture/data_provenance.md')))
    return findings


def check_data_deficiencies(scenarios: List[SingleScenario]) -> List[ValidationFinding]:
    """
    Say once per run what the data a run read LACKS — counted, never repeated (#561).

    One finding per deficiency present, over every input file of every scenario, with the
    files grouped by where they came from: the same lack has a different reason in a
    venue's published history than in a file our collector wrote under an older format.
    A standing condition is still reported on every run, because dropping it would make a
    run on complete data and one on deficient data look alike — but as one line with its
    count, its explanation living in the check catalog. This replaces the spreadless
    advisory, which guessed the missing quote from venue and format version and so stayed
    silent for converted history declaring format 1.7.0.

    Args:
        scenarios: The scenarios whose mounted input files to count

    Returns:
        One finding per deficiency present, in the order the deficiencies are declared
    """
    findings: List[ValidationFinding] = []
    total_files = 0
    found: Dict[DataDeficiency, Counter] = {}
    for scenario in scenarios:
        versions = scenario.data_format_versions
        for position, (origin_class, deficiencies) in enumerate(
                zip(scenario.origin_classes, scenario.data_deficiencies)):
            total_files += 1
            group = _input_group(origin_class, versions[position] if position < len(versions) else None)
            for deficiency in deficiencies:
                found.setdefault(deficiency, Counter())[group] += 1

    for deficiency in DataDeficiency:
        groups = found.get(deficiency)
        # `synthetic` is said before the run, per scenario, where both pipelines mount
        # their signals — an AutoTrader session has no post-run counter of this kind
        if not groups or deficiency == DataDeficiency.SYNTHETIC:
            continue
        declaration = DEFICIENCY_DECLARATIONS[deficiency]
        breakdown = ' · '.join(f'{count} {group}' for group, count in sorted(groups.items()))
        findings.append(_finding(declaration.check, ValidationDomain.DATA, (
            f'{declaration.title} — {sum(groups.values())} of {total_files} input file(s): '
            f'{declaration.consequence}\n'
            f'  → {breakdown}')))
    return findings


def _input_group(origin_class: str, data_format_version: Optional[str]) -> str:
    """
    Name the group an input file is counted in, for a deficiency finding's breakdown.

    Args:
        origin_class: The file's resolved origin class
        data_format_version: The tick file's declared format, None for a signal file

    Returns:
        'external', 'collected at format X', or 'signal'
    """
    if origin_class == OriginClass.EXTERNAL.value:
        return 'external'
    if data_format_version is None:
        return 'signal'
    return f'collected at format {data_format_version}'
