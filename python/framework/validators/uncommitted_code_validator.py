"""
FiniexTestingIDE - Uncommitted Code Guard (#551)

A session that sends REAL orders must run code that a commit describes. A live run is only half
of the parity proof: afterwards a backtest over exactly the same period is run and the divergence
measured, and that comparison needs the code that ran. A run from a dirty tree can be tied to its
code only through the patch stored beside it, and a run from a tree git cannot read cannot be
tied to anything at all.

So the start is refused, unless the operator types `--allow-dirty` for a deliberate real-money
test from a working tree. The override is recorded in the header (`origin.allow_dirty`), the
patches are already stored by the capture, and the post-run validation reports it as a Tier-1
warning, so such a run can never be read as a clean one afterwards.

What decides is the EFFECTIVE `dry_run` — the merged value the session resolves, never the
profile field — because several real-money profiles live outside `production/` and a private
profile may live outside every profile root. A mock or dry-run session never reaches the guard's
refusal. The simulation never sends an order and is not guarded at all.

One description of the repositories serves the refusal, the session-log notice and the Tier-1
finding, so the three cannot describe the same tree three ways.

A second check closes the window between the capture and the load: the pipeline reads the
components' files from disk AFTER the identity was captured, so an edit in between would run code
the header does not describe. Real orders are refused then — `--allow-dirty` included, because the
patch it records would be the wrong one — and any other session logs a warning.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from python.framework.exceptions.code_identity_errors import (
    CodeChangedDuringStartupError,
    UncommittedCodeError,
)
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    RepositoryState,
)
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)

# Stable id of the Tier-1 finding a session started with `--allow-dirty` reports.
UNCOMMITTED_CODE_CHECK = 'uncommitted_code'

# Why a repository keeps the code from being one known commit — one per remedy the refusal offers.
_DIRTY = 'dirty'
_UNVERSIONED = 'unversioned'
_UNKNOWN = 'unknown'
_NOT_CAPTURED = 'not_captured'

# How a state git could not read is shown — "unknown", never "not under version control": only a
# repository git has ANSWERED for can be said to be absent.
_UNKNOWN_TEXT = 'state unknown (git unavailable or refused)'

# How many untracked paths a state line names before it abbreviates.
_MAX_UNTRACKED_NAMED = 3

# The porcelain prefix of an untracked path.
_UNTRACKED_PREFIX = '?? '

# One rendered repository: label, where it is, its state in words, why it blocks (None = it does
# not), and the captured state behind it (None when nothing was captured).
_RepositoryRow = Tuple[str, str, str, Optional[str], Optional[RepositoryState]]


def validate_committed_code(
    code_identity: Optional[CodeIdentity],
    real_orders: bool,
    allow_dirty: bool,
    profile_path: Optional[Path],
) -> bool:
    """
    Refuse to start a real-money session whose code no commit describes.

    An identity that was not captured, or a repository git could not read, counts as uncommitted:
    an identity that cannot be determined is not a clean one, and a guard reading "clean" there
    would pass exactly the run it exists to stop.

    Args:
        code_identity: The code the session is about to run, captured at its start; None when it
            was not captured
        real_orders: Whether the session sends real orders — the negation of the EFFECTIVE dry_run
        allow_dirty: Whether the operator passed `--allow-dirty`
        profile_path: The profile the session was started with, for the command the message
            suggests; None renders a placeholder

    Returns:
        True when the session runs real orders from uncommitted code because `--allow-dirty` let
        it through — the one case the post-run validation reports; False when there was nothing
        to override. Raises UncommittedCodeError when real orders would run from uncommitted code
        without the override
    """
    if not real_orders:
        return False
    if code_identity is not None and not code_identity.is_dirty():
        return False
    if allow_dirty:
        return True
    raise UncommittedCodeError(_refusal_message(code_identity, profile_path))


def describe_uncommitted_code(code_identity: Optional[CodeIdentity]) -> str:
    """
    Name every repository that keeps the code from being one known commit, with its patch.

    Args:
        code_identity: The code the session runs; None when it was not captured

    Returns:
        One clause per blocking repository, `; `-separated — e.g. `algo user_algos/ 2 changes ·
        untracked: my_bot/ (patch user_algos/.finiex_run_patches/4e01….patch)`
    """
    clauses = [f'{label} {where} {text} ({_patch_text(kind, state, where)})'
               for label, where, text, kind, state in _repository_rows(code_identity)
               if kind is not None]
    return '; '.join(clauses)


def build_uncommitted_code_finding(code_identity: Optional[CodeIdentity],
                                   scope: str) -> ValidationFinding:
    """
    The Tier-1 warning of a session that ran real orders from uncommitted code.

    Args:
        code_identity: The code the session ran; None when it was not captured
        scope: The scope the finding concerns

    Returns:
        The advisory finding
    """
    return ValidationFinding(
        severity=Severity.WARNING, check=UNCOMMITTED_CODE_CHECK,
        domain=ValidationDomain.SETUP, scope=scope,
        message=(
            f'REAL ORDERS FROM UNCOMMITTED CODE — the session was started with --allow-dirty '
            f'and ran code no commit describes: {describe_uncommitted_code(code_identity)}. '
            f'A parity backtest has to run that code — restored from its stored patch where one '
            f'exists — not the code of a commit.'))


def validate_code_unchanged_since_capture(
    moved: List[ComponentIdentity],
    real_orders: bool,
) -> Optional[str]:
    """
    Refuse real orders from code that changed between the capture and the load.

    `--allow-dirty` plays no part: it lets a session through on the promise that the recorded
    patch restores the code that ran, and here that patch describes code the session did not load.
    A session that sends no real orders is let through with a warning instead — its header is
    wrong, and the warning puts that in its own record, but no money moves on code nobody can name.

    Args:
        moved: The components whose package no longer matches its recorded digest
        real_orders: Whether the session sends real orders — the negation of the EFFECTIVE dry_run

    Returns:
        The warning a session without real orders logs, or None when nothing moved. Raises
        CodeChangedDuringStartupError when real orders would run from code that moved
    """
    if not moved:
        return None
    if real_orders:
        raise CodeChangedDuringStartupError(_moved_refusal_message(moved))
    return (f'CODE CHANGED DURING STARTUP — {_moved_files(moved)} changed after the code '
            f'identity was captured, so this run header does not describe the code that was '
            f'loaded. Start again once the edits are finished for a record that does.')


def _moved_files(moved: List[ComponentIdentity]) -> str:
    """
    The moved components as one phrase.

    Args:
        moved: The components whose package moved

    Returns:
        `decision my_strategy (/path/my_strategy.py), worker rsi_fast (/path/rsi.py)`
    """
    return ', '.join(f'{component.role} {component.name} ({component.source_path or component.type})'
                     for component in moved)


def _moved_refusal_message(moved: List[ComponentIdentity]) -> str:
    """
    The refusal for code that moved, laid out for the STARTUP FAILED block.

    Args:
        moved: The components whose package moved

    Returns:
        The multi-line message
    """
    label_width = max(len(str(component.role)) for component in moved) + 3
    # A component the capture could not resolve has no file on record; its type is what the
    # configuration named, and the pipeline loaded it after the capture anyway.
    table = [f'    {str(component.role):<{label_width}}'
             f"{component.source_path or component.type + ' (not resolvable at capture)'}"
             for component in moved]
    return '\n'.join([
        'This session would send REAL ORDERS (dry_run resolves to false), but the code it\n'
        '  loaded changed while it was starting — after its code identity was captured:',
        *table,
        "  The run header's digests and patch describe the code as it was at the capture, not",
        '  the code that was loaded, so this session could not be traced back to what it ran.',
        '  --allow-dirty does not help here: the patch it records would be the wrong one.',
        '  What to do:',
        '    • finish the edits — nothing may write to the strategy while a session starts —',
        '      then start again'])


def _refusal_message(code_identity: Optional[CodeIdentity],
                     profile_path: Optional[Path]) -> str:
    """
    The refusal, laid out for the STARTUP FAILED block (which indents its first line by two).

    Args:
        code_identity: The code the session would run, or None
        profile_path: The profile the session was started with, or None

    Returns:
        The multi-line message
    """
    rows = _repository_rows(code_identity)
    kinds = {kind for _, _, _, kind, _ in rows}
    if kinds & {_DIRTY, _UNVERSIONED}:
        lead = ('This session would send REAL ORDERS (dry_run resolves to false), but the code it\n'
                '  would run is not committed:')
    else:
        lead = ('This session would send REAL ORDERS (dry_run resolves to false), but which code\n'
                '  it would run cannot be determined:')

    label_width = max(len(label) for label, _, _, _, _ in rows) + 3
    where_width = max(len(where) for _, where, _, _, _ in rows) + 3
    table = [f'    {label:<{label_width}}{where:<{where_width}}{text}'
             for label, where, text, _, _ in rows]

    remedies = _remedies(rows)
    remedies[0][0] = f'{remedies[0][0]}        ← the normal path'
    profile = str(profile_path) if profile_path is not None else '<profile>'
    # The override may promise a patch only where one restores the code: a clean commit, or a dirty
    # tree whose COMPLETE patch was stored (`RepositoryState.restorable`). Promising it anywhere
    # else would send the operator into a run nobody can reproduce, believing the opposite.
    unrestorable = [f'{where} ({_unrestorable_reason(kind, state)})'
                    for _, where, _, kind, state in rows
                    if kind is not None and (state is None or not state.restorable)]
    if unrestorable:
        consequence = ['      the run then reports a Tier-1 warning — but nothing can restore the '
                       'code afterwards in',
                       f'      {" and ".join(unrestorable)}']
    else:
        consequence = ['      the run then stores its diff hash and patch and reports a Tier-1 '
                       'warning']

    lines = [lead, *table,
             '  A run from uncommitted code cannot be traced back to the code that ran — the parity',
             '  backtest afterwards would compare against code that no longer exists.',
             '  What to do:']
    for remedy in remedies:
        lines.append(f'    • {remedy[0]}')
        lines.extend(f'      {continuation}' for continuation in remedy[1:])
    lines += ['    • a deliberate real-money test from this tree:',
              f'        python python/cli/autotrader_cli.py run --config {profile} --allow-dirty',
              *consequence]
    return '\n'.join(lines)


def _remedies(rows: List[_RepositoryRow]) -> List[List[str]]:
    """
    One way forward per kind of blocking repository, commit first — the normal path.

    Args:
        rows: The rendered repositories

    Returns:
        The remedies, never empty — each its first line, then the lines that continue it
    """
    remedies = []
    dirty = _where_of(rows, _DIRTY)
    if dirty:
        remedies.append([f'commit the changes in {dirty}, then start again'])
    unversioned = _where_of(rows, _UNVERSIONED)
    if unversioned:
        remedies.append([f'put {unversioned} under version control and commit it, '
                         f'then start again'])
    unknown_roots = [state.root for _, _, _, kind, state in rows
                     if kind == _UNKNOWN and state is not None]
    if unknown_roots:
        # Two causes look alike from here: no git binary, and a git that REFUSES the checkout
        # because another user owns it ("dubious ownership") — common in a container on a mount.
        remedies.append(
            [f'make git able to read {" and ".join(unknown_roots)}, then start again',
             'is git installed? if it reports "dubious ownership", trust the checkout:',
             *[f'  git config --global --add safe.directory {root}' for root in unknown_roots]])
    if _where_of(rows, _NOT_CAPTURED):
        remedies.append(['find why the code identity was not captured (autotrader_global.log), '
                         'then start again'])
    return remedies


def _where_of(rows: List[_RepositoryRow], kind: str) -> str:
    """
    The repositories that block for one reason, as one phrase.

    Args:
        rows: The rendered repositories
        kind: The reason

    Returns:
        Their roots joined by ` and `, or '' when none blocks for that reason
    """
    return ' and '.join(where for _, where, _, row_kind, _ in rows if row_kind == kind)


def _repository_rows(code_identity: Optional[CodeIdentity]) -> List[_RepositoryRow]:
    """
    The framework first, then every other repository, each with its state in words.

    Args:
        code_identity: The captured code identity, or None

    Returns:
        One row per repository; a single not-captured row when nothing was captured
    """
    if code_identity is None:
        return [('code', 'this session', 'state unknown (code identity not captured)',
                 _NOT_CAPTURED, None)]
    framework = code_identity.framework
    framework_root = framework.root if framework is not None else None
    rows: List[_RepositoryRow] = []
    if framework is None:
        # The capture always records this repository, even where git could not read it; an
        # identity without it was built by hand or written before the field existed.
        rows.append(('framework', 'this repository', 'state unknown (not recorded)',
                     _NOT_CAPTURED, None))
    else:
        rows.append(_row('framework', framework.root, framework))
    for state in code_identity.repositories:
        rows.append(_row('algo', _display_root(state.root, framework_root), state))
    return rows


def _row(label: str, where: str, state: RepositoryState) -> _RepositoryRow:
    """
    Render one repository.

    Args:
        label: `framework` or `algo`
        where: How its root is shown
        state: Its captured state

    Returns:
        The row
    """
    if state.is_committed():
        return (label, where, 'clean', None, state)
    # `in_repository` None means git could not RUN — and a repository whose status git could not
    # read (commit None, not dirty) is just as unknown. Neither may read as "not under version
    # control", which is a statement only an answering git can make.
    if state.in_repository is None or (state.in_repository and not state.dirty):
        return (label, where, _UNKNOWN_TEXT, _UNKNOWN, state)
    if state.in_repository is False:
        return (label, where, 'not under version control', _UNVERSIONED, state)

    count = state.uncommitted_count or len(state.changes)
    parts = [f'{count} change{"" if count == 1 else "s"}']
    untracked = [line[len(_UNTRACKED_PREFIX):] for line in state.changes
                 if line.startswith(_UNTRACKED_PREFIX)]
    if untracked:
        named = ', '.join(untracked[:_MAX_UNTRACKED_NAMED])
        more = ', …' if len(untracked) > _MAX_UNTRACKED_NAMED else ''
        parts.append(f'untracked: {named}{more}')
    if state.commit is None:
        parts.insert(0, 'no commit yet')
    return (label, where, ' · '.join(parts), _DIRTY, state)


def _unrestorable_reason(kind: str, state: Optional[RepositoryState]) -> str:
    """
    Why a blocking repository's code cannot be put back after the run.

    Args:
        kind: Why the repository blocks
        state: Its captured state, or None

    Returns:
        The reason, in a few words
    """
    if kind == _UNVERSIONED:
        return 'no repository to diff against'
    if kind == _UNKNOWN:
        return 'git could not read it'
    if kind == _NOT_CAPTURED or state is None:
        return 'nothing was captured'
    if state.patch_ref:
        return 'its patch cannot cover a nested repository'
    return 'its patch could not be kept'


def _patch_text(kind: str, state: Optional[RepositoryState], where: str) -> str:
    """
    Where a blocking repository's uncommitted code can be restored from, if anywhere.

    Args:
        kind: Why the repository blocks
        state: Its captured state, or None
        where: How the repository's root is shown

    Returns:
        The patch reference, or why there is none
    """
    if state is not None and state.patch_ref:
        text = f'patch {_patch_location(state.patch_ref, where)}'
        if not state.restorable:
            text += ', incomplete: a nested repository is not in it'
        if state.patch_excluded:
            text += ', credential files left out'
        return text
    if state is not None and state.diff_hash:
        return f'diff {state.diff_hash[:12]}, patch NOT kept'
    if kind == _UNVERSIONED:
        return 'no patch: no repository to diff against'
    if kind == _UNKNOWN:
        return 'no patch: git unavailable or refused'
    if kind == _NOT_CAPTURED:
        return 'no patch: nothing was captured'
    return 'no patch: git produced none'


def _patch_location(patch_ref: str, where: str) -> str:
    """
    A patch reference as the operator finds the file.

    A relative reference resolves against its repository's root — which is how a foreign
    repository names the patch it keeps inside itself — so it is shown joined to that root.

    Args:
        patch_ref: The recorded reference
        where: How the repository's root is shown

    Returns:
        `user_algos/.finiex_run_patches/4e01….patch` for a foreign patch, `/app/run_patches/…` for
        this repository's; an absolute reference as it is
    """
    if Path(patch_ref).is_absolute():
        return patch_ref
    return f'{where.rstrip("/")}/{patch_ref}'


def _display_root(root: str, framework_root: Optional[str]) -> str:
    """
    A repository root as the operator knows it — relative to this repository when inside it.

    Args:
        root: The repository's root
        framework_root: This repository's root, or None

    Returns:
        `user_algos/` for a root inside this repository, otherwise the root as given
    """
    if framework_root is None:
        return root
    try:
        relative = Path(root).relative_to(framework_root)
    except ValueError:
        return root
    return f'{relative.as_posix()}/'
