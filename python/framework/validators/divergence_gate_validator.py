"""
The divergence gate (#512) — whether a backtest may be compared with the sessions of a segment.

A divergence measured between a live session and a backtest that does not compute what the
session would have computed measures a difference of code, configuration or window, not parity.
The gate refuses such a pair before anything is measured, and names the file, the component, the
value or the instant that refuses it:

1. code path — every file the backtest's code path reaches, at the backtest's commit and at the
   session's, is unchanged, changed in docstrings only, or declared for the session's release;
2. strategy — every component the same code, the strategy's repositories on the same commit;
3. strategy configuration — the same rendered value at every path;
4. broker document — the same frozen broker configuration;
5. window — the backtest read the window as it was measured, not to a tick limit;
6. no order feature allowed beyond the venue, and every scenario recorded its state frames.

A backtest records an order event only where an order moved, so a missing order-event stream is
an answer — no order — and is never refused here.
"""

from datetime import timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from python.framework.constants.divergence_gate_constants import (
    ALLOWED_CHANGES_BY_RELEASE,
    GATE_ENTRY_DIRECTORIES,
    GATE_ENTRY_POINTS,
    GATE_EXCLUDED_PREFIXES,
    GATE_SOURCE_DIRECTORY,
    GATE_SOURCE_SUFFIX,
    WINDOW_EDGE_TOLERANCE_S,
)
from python.framework.types.api.divergence_report_types import CompareWindow
from python.framework.types.divergence_capture_types import (
    BacktestRunCapture,
    LiveSessionCapture,
)
from python.framework.types.divergence_gate_types import (
    DivergenceGateResult,
    GateChangeCategory,
    GateCodePath,
    GateFileChange,
    GateFileDeclaration,
    GateFileVerdict,
)
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    RepositoryState,
    repository_labels,
)
from python.framework.types.validation_types import Severity, ValidationDomain, ValidationFinding
from python.framework.utils.git_info_utils import (
    commits_agree,
    get_framework_root,
    read_tree_at_commit,
)
from python.framework.utils.import_closure_utils import import_closure, syntax_digest

CODE_UNKNOWN_CHECK = 'divergence_gate_code_unknown'
UNCOMMITTED_CHECK = 'divergence_gate_uncommitted'
SESSIONS_DISAGREE_CHECK = 'divergence_gate_sessions_disagree'
COMMIT_UNREADABLE_CHECK = 'divergence_gate_commit_unreadable'
CODE_PATH_CHECK = 'divergence_gate_code_path'
STRATEGY_CHECK = 'divergence_gate_strategy'
STRATEGY_CONFIG_CHECK = 'divergence_gate_strategy_config'
BROKER_CONFIG_CHECK = 'divergence_gate_broker_config'
WINDOW_CHECK = 'divergence_gate_window'
FEATURE_BEYOND_VENUE_CHECK = 'divergence_gate_feature_beyond_venue'
FRAMES_MISSING_CHECK = 'divergence_gate_frames_missing'

# How many differing values of one configuration a refusal names before it only counts the rest
_NAMED_CONFIG_PATHS = 10

# A source tree read at one commit: path → text, None where the repository does not hold it
TreeReader = Callable[[str], Optional[Dict[str, str]]]


def check_divergence_gate(
        sessions: Sequence[LiveSessionCapture], backtest: BacktestRunCapture,
        window: CompareWindow, read_tree: Optional[TreeReader] = None,
        allowed_changes: Mapping[str, Mapping[str, Mapping[str, Any]]] = ALLOWED_CHANGES_BY_RELEASE
) -> DivergenceGateResult:
    """
    Whether a backtest may be compared with the sessions of one segment's window.

    Args:
        sessions: The sessions whose days the window compares
        backtest: The backtest run of that window
        window: The window, as the live side placed it
        read_tree: Commit → the source tree it holds; this repository's when omitted
        allowed_changes: Release → file → its declaration; the gate's own when omitted

    Returns:
        Every refusal, and the code path judged file by file where it could be
    """
    if not sessions:
        raise ValueError('The gate compares a backtest with the sessions of a window, and none '
                         'was given.')
    findings: List[ValidationFinding] = []
    code_path = _check_code(sessions, backtest, read_tree or _read_framework_tree,
                            allowed_changes, findings)
    _check_strategy(sessions, backtest, findings)
    _check_strategy_config(sessions, backtest, findings)
    _check_broker_config(sessions, backtest, findings)
    _check_window(backtest, window, findings)
    _check_backtest_records(backtest, findings)
    return DivergenceGateResult(findings=findings, code_path=code_path)


def judge_code_path(release: str, release_tree: Mapping[str, str],
                    backtest_tree: Mapping[str, str],
                    declarations: Mapping[str, Mapping[str, Any]]) -> List[GateFileVerdict]:
    """
    Every file of the backtest's code path at two commits, judged against a release's
    declarations.

    Args:
        release: The release the declarations belong to, for the record
        release_tree: The source tree at the session's commit
        backtest_tree: The source tree at the backtest's commit
        declarations: File → its declaration for that release

    Returns:
        One verdict per file of either closure, by path
    """
    in_release = set(_closure_of(release_tree))
    in_backtest = set(_closure_of(backtest_tree))
    verdicts: List[GateFileVerdict] = []
    for path in sorted(in_release | in_backtest):
        new_source = backtest_tree.get(path) if path in in_backtest else None
        verdicts.append(GateFileVerdict(
            path=path,
            change=_change_of(release_tree.get(path) if path in in_release else None, new_source),
            declaration=_declaration_of(declarations.get(path), release, path),
            syntax_digest=syntax_digest(new_source) if new_source is not None else None))
    return verdicts


def _closure_of(tree: Mapping[str, str]) -> List[str]:
    """
    The backtest's code path in one source tree.

    Args:
        tree: Path → text

    Returns:
        The closure's paths, sorted
    """
    entry_points = list(GATE_ENTRY_POINTS) + sorted(
        path for path in tree if path.rsplit('/', 1)[0] in GATE_ENTRY_DIRECTORIES)
    return import_closure(entry_points, tree.get, GATE_EXCLUDED_PREFIXES)


def _change_of(old_source: Optional[str], new_source: Optional[str]) -> GateFileChange:
    """
    How one file of the path differs between the two commits.

    Args:
        old_source: Its text in the session's path; None where that path does not hold it
        new_source: Its text in the backtest's path; None where that path does not hold it

    Returns:
        The change
    """
    if old_source is None:
        return GateFileChange.ENTERED_PATH
    if new_source is None:
        return GateFileChange.LEFT_PATH
    if old_source == new_source:
        return GateFileChange.UNCHANGED
    if syntax_digest(old_source) == syntax_digest(new_source):
        return GateFileChange.DOCSTRINGS_ONLY
    return GateFileChange.CHANGED


def _declaration_of(entry: Optional[Mapping[str, Any]], release: str,
                    path: str) -> Optional[GateFileDeclaration]:
    """
    One file's declaration, typed.

    Args:
        entry: Its entry in the release's declarations; None where it has none
        release: The release, for a malformed entry's message
        path: The file, as above

    Returns:
        The declaration; None where there is none
    """
    if entry is None:
        return None
    try:
        return GateFileDeclaration(
            categories=tuple(GateChangeCategory(name) for name in entry['categories']),
            syntax_digest=entry['syntax_digest'])
    except (KeyError, ValueError) as e:
        raise ValueError(f'The gate declaration of {path} for release {release} is malformed: '
                         f'{e}') from e


def _check_code(sessions: Sequence[LiveSessionCapture], backtest: BacktestRunCapture,
                read_tree: TreeReader,
                allowed_changes: Mapping[str, Mapping[str, Mapping[str, Any]]],
                findings: List[ValidationFinding]) -> Optional[GateCodePath]:
    """
    Check 1: the backtest's code path against the session's release.

    Args:
        sessions: The window's sessions
        backtest: The backtest run
        read_tree: Commit → its source tree
        allowed_changes: Release → file → its declaration
        findings: Where a refusal is added

    Returns:
        The code path judged; None where it could not be
    """
    session_side = _session_code(sessions, findings)
    backtest_framework = _committed_framework(
        backtest.code_identity, f'the backtest {backtest.run_id}', backtest.run_id, findings)
    if session_side is None or backtest_framework is None:
        return None
    release, session_commit = session_side
    trees: Dict[str, Dict[str, str]] = {}
    sides = ((session_commit, 'the session'), (backtest_framework.commit, 'the backtest'))
    for commit, side in sides:
        tree = read_tree(commit)
        if tree is None:
            findings.append(_refusal(
                COMMIT_UNREADABLE_CHECK, ValidationDomain.RELEASE,
                f'The framework commit {commit} {side} ran is not in this repository, so its code '
                'path cannot be read.', backtest.run_id))
            return None
        trees[commit] = tree
    files = judge_code_path(release, trees[session_commit], trees[backtest_framework.commit],
                            allowed_changes.get(release, {}))
    code_path = GateCodePath(release=release, session_commit=session_commit,
                             backtest_commit=backtest_framework.commit, files=files)
    for verdict in code_path.refused():
        findings.append(_refusal(CODE_PATH_CHECK, ValidationDomain.RELEASE,
                                 _code_path_message(verdict, release), verdict.path))
    return code_path


def _session_code(sessions: Sequence[LiveSessionCapture],
                  findings: List[ValidationFinding]) -> Optional[Tuple[str, str]]:
    """
    The release and the framework commit the window's sessions ran — one of each.

    Args:
        sessions: The window's sessions
        findings: Where a refusal is added

    Returns:
        (release, commit); None where they cannot be read or disagree
    """
    stands: Set[Tuple[str, str]] = set()
    for session in sessions:
        framework = _committed_framework(session.code_identity, f'the session {session.run_id}',
                                         session.run_id, findings)
        if framework is None:
            return None
        if not session.app_version:
            findings.append(_refusal(
                CODE_UNKNOWN_CHECK, ValidationDomain.RELEASE,
                f'The session {session.run_id} names no release on its ledger rows.',
                session.run_id))
            return None
        stands.add((session.app_version, framework.commit))
    if len(stands) > 1:
        findings.append(_refusal(
            SESSIONS_DISAGREE_CHECK, ValidationDomain.RELEASE,
            'The sessions of the window ran different code: ' + ', '.join(
                f'release {release} at {commit}' for release, commit in sorted(stands)) + '.'))
        return None
    return next(iter(stands))


def _committed_framework(identity: Optional[CodeIdentity], who: str, scope: str,
                         findings: List[ValidationFinding]) -> Optional[RepositoryState]:
    """
    One side's framework state, where it is exactly one known commit.

    Args:
        identity: The side's code identity; None where it could not be read
        who: The side, for the message
        scope: The run the finding concerns
        findings: Where a refusal is added

    Returns:
        The framework state; None where it is unknown or not one commit
    """
    if identity is None or identity.framework is None:
        findings.append(_refusal(CODE_UNKNOWN_CHECK, ValidationDomain.RELEASE,
                                 f'Which code {who} ran cannot be read from its header.', scope))
        return None
    if not identity.framework.is_committed():
        findings.append(_refusal(
            UNCOMMITTED_CHECK, ValidationDomain.RELEASE,
            f'{who.capitalize()} ran a framework that is not one commit '
            f'({_state_text(identity.framework)}), so nobody can say which code ran.', scope))
        return None
    return identity.framework


def _code_path_message(verdict: GateFileVerdict, release: str) -> str:
    """
    Why one file of the code path refuses the comparison.

    Args:
        verdict: The refused file
        release: The session's release

    Returns:
        One sentence naming the file, how it differs and what the release declares
    """
    how = {GateFileChange.CHANGED: 'changed',
           GateFileChange.ENTERED_PATH: "entered the backtest's code path",
           GateFileChange.LEFT_PATH: "left the backtest's code path"}[verdict.change]
    how = f'{how} since release {release}'
    if verdict.declaration is None:
        return f'{verdict.path} {how} and is not declared for it.'
    categories = ', '.join(verdict.declaration.categories)
    return (f'{verdict.path} {how}; it is declared ({categories}) at the syntax tree '
            f"{verdict.declaration.syntax_digest[:12]}, and the backtest's commit holds "
            f'{(verdict.syntax_digest or "none")[:12]} — it changed again after its declaration.')


def _check_strategy(sessions: Sequence[LiveSessionCapture], backtest: BacktestRunCapture,
                    findings: List[ValidationFinding]) -> None:
    """
    Check 2: every component the same code, every strategy repository on the same commit.

    Args:
        sessions: The window's sessions
        backtest: The backtest run
        findings: Where a refusal is added
    """
    if backtest.code_identity is None:
        return
    backtest_components = _components_of(backtest.code_identity)
    backtest_repositories = _strategy_repositories_of(backtest.code_identity)
    for label, state in backtest_repositories.items():
        if not state.is_committed():
            findings.append(_refusal(
                STRATEGY_CHECK, ValidationDomain.ALGO,
                f'The backtest {backtest.run_id} ran the {label} repository uncommitted '
                f'({_state_text(state)}).', backtest.run_id))
    for session in sessions:
        if session.code_identity is None:
            continue
        for problem in _component_differences(_components_of(session.code_identity),
                                              backtest_components):
            findings.append(_refusal(STRATEGY_CHECK, ValidationDomain.ALGO,
                                     f'{problem} (session {session.run_id}).', session.run_id))
        for problem in _repository_differences(
                _strategy_repositories_of(session.code_identity), backtest_repositories):
            findings.append(_refusal(STRATEGY_CHECK, ValidationDomain.ALGO,
                                     f'{problem} (session {session.run_id}).', session.run_id))


def _components_of(identity: CodeIdentity) -> Dict[Tuple[str, str], ComponentIdentity]:
    """
    A side's components by role and name.

    Args:
        identity: Its code identity

    Returns:
        (role, name) → the component
    """
    return {(component.role.value, component.name): component
            for component in identity.components}


def _component_differences(session: Mapping[Tuple[str, str], ComponentIdentity],
                           backtest: Mapping[Tuple[str, str], ComponentIdentity]) -> List[str]:
    """
    How the session's components and the backtest's differ.

    A component that carries a digest is compared by it, wherever its file lay; one that carries
    none — a component of this repository, whose code the code path covers — by its type.

    Args:
        session: The session's components by role and name
        backtest: The backtest's, as above

    Returns:
        One sentence per difference; empty where they agree
    """
    problems: List[str] = []
    for key in sorted(set(session) | set(backtest)):
        role, name = key
        ours, theirs = session.get(key), backtest.get(key)
        if ours is None or theirs is None:
            side = 'session' if theirs is None else 'backtest'
            problems.append(f'The {role} {name} runs only in the {side}')
        elif ours.package_digest != theirs.package_digest:
            problems.append(f'The {role} {name} is different code: digest '
                            f'{_short(ours.package_digest)} in the session, '
                            f'{_short(theirs.package_digest)} in the backtest')
        elif ours.package_digest is None and ours.type != theirs.type:
            problems.append(f'The {role} {name} is {ours.type} in the session and {theirs.type} '
                            'in the backtest')
    return problems


def _strategy_repositories_of(identity: CodeIdentity) -> Dict[str, RepositoryState]:
    """
    A side's other repositories by the word a reader knows them by — never by their root, which
    differs between the machine a session ran on and this one.

    Args:
        identity: Its code identity

    Returns:
        Label → the repository's state
    """
    labels = repository_labels(state.root for state in identity.repositories)
    return {labels[state.root]: state for state in identity.repositories}


def _repository_differences(session: Mapping[str, RepositoryState],
                            backtest: Mapping[str, RepositoryState]) -> List[str]:
    """
    How the session's strategy repositories and the backtest's differ.

    Args:
        session: The session's repositories by label
        backtest: The backtest's, as above

    Returns:
        One sentence per difference; empty where they agree
    """
    problems: List[str] = []
    for label in sorted(set(session) | set(backtest)):
        ours, theirs = session.get(label), backtest.get(label)
        if ours is None or theirs is None:
            side = 'session' if theirs is None else 'backtest'
            problems.append(f'The {label} repository is loaded only in the {side}')
        elif not ours.is_committed():
            problems.append(f'The session ran the {label} repository uncommitted '
                            f'({_state_text(ours)})')
        elif theirs.is_committed() and not commits_agree(ours.commit, theirs.commit):
            problems.append(f'The {label} repository is at {ours.commit} in the session and at '
                            f'{theirs.commit} in the backtest')
    return problems


def _check_strategy_config(sessions: Sequence[LiveSessionCapture], backtest: BacktestRunCapture,
                           findings: List[ValidationFinding]) -> None:
    """
    Check 3: the strategy as its components were built, value by value.

    Both sides are compared as rendered — every default filled in, each component named the way
    its configuration names it — because that is the one form both pipelines record alike; a
    ledger row keeps the strategy as each pipeline was handed it.

    Args:
        sessions: The window's sessions
        backtest: The backtest run
        findings: Where a refusal is added
    """
    for session in sessions:
        if session.rendered_strategy_config is None:
            findings.append(_refusal(
                STRATEGY_CONFIG_CHECK, ValidationDomain.CONFIG,
                f'The rendered strategy of the session {session.run_id} cannot be read '
                f'({session.unreadable.get("rendered_config", "its rows name none")}).',
                session.run_id))
            continue
        for scenario in backtest.scenarios:
            if scenario.rendered_strategy_config is None:
                continue
            paths = _differing_paths(session.rendered_strategy_config,
                                     scenario.rendered_strategy_config, '')
            if paths:
                named = ', '.join(paths[:_NAMED_CONFIG_PATHS])
                more = len(paths) - _NAMED_CONFIG_PATHS
                findings.append(_refusal(
                    STRATEGY_CONFIG_CHECK, ValidationDomain.CONFIG,
                    f'The strategy differs between the session {session.run_id} and the '
                    f'scenario {scenario.name} at {named}'
                    + (f' and {more} more' if more > 0 else '') + '.', scenario.name))
    for scenario in backtest.scenarios:
        if scenario.rendered_strategy_config is None:
            findings.append(_refusal(
                STRATEGY_CONFIG_CHECK, ValidationDomain.CONFIG,
                f'The rendered strategy of the scenario {scenario.name} cannot be read '
                f'({backtest.unreadable.get("rendered_config", "its run names none")}).',
                scenario.name))


def _differing_paths(session: Any, backtest: Any, path: str) -> List[str]:
    """
    Every path at which two configurations hold different values.

    Args:
        session: The session's value at this path
        backtest: The backtest's value at this path
        path: Where both stand, dotted; '' at the top

    Returns:
        The differing paths, a key present on one side only included
    """
    if isinstance(session, dict) and isinstance(backtest, dict):
        paths: List[str] = []
        for key in sorted(set(session) | set(backtest), key=str):
            child = f'{path}.{key}' if path else str(key)
            if key not in session or key not in backtest:
                paths.append(child)
            else:
                paths += _differing_paths(session[key], backtest[key], child)
        return paths
    if isinstance(session, list) and isinstance(backtest, list) and len(session) == len(backtest):
        paths = []
        for position, (ours, theirs) in enumerate(zip(session, backtest)):
            paths += _differing_paths(ours, theirs, f'{path}[{position}]')
        return paths
    return [] if session == backtest else [path or '(the whole configuration)']


def _check_broker_config(sessions: Sequence[LiveSessionCapture], backtest: BacktestRunCapture,
                         findings: List[ValidationFinding]) -> None:
    """
    Check 4: the same frozen broker configuration.

    Args:
        sessions: The window's sessions
        backtest: The backtest run
        findings: Where a refusal is added
    """
    backtest_ids = sorted({config_id for row in backtest.rows
                           for config_id in row.broker_config_id.split(',') if config_id})
    for session in sessions:
        if backtest_ids != [session.broker_config_id] or not session.broker_config_id:
            findings.append(_refusal(
                BROKER_CONFIG_CHECK, ValidationDomain.BROKER,
                f'The session {session.run_id} traded under the broker configuration '
                f'{session.broker_config_id or "(none recorded)"}, the backtest under '
                f'{", ".join(backtest_ids) or "(none recorded)"}.', session.run_id))


def _check_window(backtest: BacktestRunCapture, window: CompareWindow,
                  findings: List[ValidationFinding]) -> None:
    """
    Check 5: every scenario read the window as it was measured, not to a tick limit.

    A tick feed and the archive do not stamp the same instant, and a pause shorter than the gap
    bound is no gap, so either edge may fall short by that bound.

    Args:
        backtest: The backtest run
        window: The window, as the live side placed it
        findings: Where a refusal is added
    """
    if window.start is None or window.end is None:
        findings.append(_refusal(
            WINDOW_CHECK, ValidationDomain.DATA,
            f'The window has no {"start" if window.start is None else "end"} '
            f'({window.end_cause.value}), so nothing can be compared in it.'))
        return
    if not backtest.scenarios:
        findings.append(_refusal(WINDOW_CHECK, ValidationDomain.DATA,
                                 f'The backtest {backtest.run_id} records no scenario.',
                                 backtest.run_id))
    tolerance = timedelta(seconds=WINDOW_EDGE_TOLERANCE_S)
    for scenario in backtest.scenarios:
        problems: List[str] = []
        if scenario.declared_window is None:
            problems.append('its header declares no window for it')
        elif scenario.declared_window.end_date is None:
            problems.append("it was set to end on a tick limit, not on the window's end")
        if scenario.first_tick is None or scenario.last_tick is None:
            problems.append('it records no first and last tick')
        else:
            if scenario.first_tick > window.start + tolerance:
                problems.append(f'its first tick {scenario.first_tick.isoformat()} comes more '
                                f"than {WINDOW_EDGE_TOLERANCE_S} s after the window's start "
                                f'{window.start.isoformat()}')
            if scenario.last_tick < window.end - tolerance:
                problems.append(f'its last tick {scenario.last_tick.isoformat()} comes more than '
                                f"{WINDOW_EDGE_TOLERANCE_S} s before the window's end "
                                f'{window.end.isoformat()}')
        if problems:
            findings.append(_refusal(
                WINDOW_CHECK, ValidationDomain.DATA,
                f'The scenario {scenario.name} did not read the window: '
                + '; '.join(problems) + '.', scenario.name))


def _check_backtest_records(backtest: BacktestRunCapture,
                            findings: List[ValidationFinding]) -> None:
    """
    Check 6: no order feature allowed beyond the venue, and every scenario's state frames.

    Args:
        backtest: The backtest run
        findings: Where a refusal is added
    """
    if not backtest.rows:
        findings.append(_refusal(
            FEATURE_BEYOND_VENUE_CHECK, ValidationDomain.CONFIG,
            f'The backtest {backtest.run_id} booked no ledger row, so what it allowed beyond '
            'the venue cannot be read.', backtest.run_id))
    for features in _distinct(row.features_beyond_venue for row in backtest.rows):
        if features:
            findings.append(_refusal(
                FEATURE_BEYOND_VENUE_CHECK, ValidationDomain.CONFIG,
                f'The backtest {backtest.run_id} allowed order features the venue does not '
                f'hold: {features}.', backtest.run_id))
    for scenario in backtest.scenarios:
        if not scenario.records_frames:
            findings.append(_refusal(
                FRAMES_MISSING_CHECK, ValidationDomain.DATA,
                f'The scenario {scenario.name} recorded no state frames, so its decisions and '
                "positions cannot be held against the session's.", scenario.name))


def _read_framework_tree(commit: str) -> Optional[Dict[str, str]]:
    """
    This repository's source tree at one commit.

    Args:
        commit: The commit

    Returns:
        Path → text; None where git cannot run or the commit is not held
    """
    root = get_framework_root()
    if root is None:
        return None
    return read_tree_at_commit(root, commit, GATE_SOURCE_DIRECTORY, GATE_SOURCE_SUFFIX)


def _refusal(check: str, domain: ValidationDomain, message: str,
             scope: str = '') -> ValidationFinding:
    """
    One refusal of the comparison.

    Args:
        check: The check's id
        domain: The area it belongs to
        message: What refuses it, for a person
        scope: What it concerns; '' for the pair as a whole

    Returns:
        The finding, an error
    """
    return ValidationFinding(severity=Severity.ERROR, check=check, domain=domain,
                             message=message, scope=scope)


def _state_text(state: RepositoryState) -> str:
    """
    A repository state that is not one commit, in a few words.

    Args:
        state: The state

    Returns:
        What it is instead
    """
    if state.in_repository is None:
        return 'git could not read it'
    if state.in_repository is False:
        return 'under no version control'
    if state.commit is None:
        return 'no commit recorded'
    return f'{state.uncommitted_count} uncommitted change(s) on {state.commit}'


def _short(digest: Optional[str]) -> str:
    """
    A digest shortened for a message.

    Args:
        digest: The digest; None where there is none

    Returns:
        Its first twelve characters, or 'none'
    """
    return digest[:12] if digest else 'none'


def _distinct(values: Iterable[str]) -> List[str]:
    """
    The distinct values of one column, in their first order.

    Args:
        values: The values

    Returns:
        Each once
    """
    return list(dict.fromkeys(values))
