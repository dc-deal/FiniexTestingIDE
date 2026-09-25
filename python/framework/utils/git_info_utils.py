"""
FiniexTestingIDE - Git Info Utilities

Single source of truth for reading version-control state from the working tree.
Used by run reports, certificates and performance snapshots so the git lookup
lives in ONE place instead of being re-derived per consumer.

READ THIS BEFORE ADDING A CALLER — the full read is EXPENSIVE, and the cost is the
filesystem rather than git. Measured 2026-08-30: `get_git_info()` ~2.0 s, of which
`git status` is ~1.8 s, because a single `stat()` costs ~2.1 ms on this project's 9p
mount instead of the 1-2 µs an ext4 volume would take. A bare `os.stat()` loop over the
tracked files is SLOWER than `git status` itself, so no git library can help — the only
lever is calling it less. Two consequences, neither optional:
  - need only the commit? call `get_git_commit()` (68 ms), never the full read
  - the full read is CACHED per process (below)
"""

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from python.framework.types.git_info_types import GitInfo, RepoStatus

# Settings pinned on EVERY call, so a developer's git configuration cannot change what a read
# answers (#551). Measured in review: `status.showUntrackedFiles=no` hides an untracked strategy,
# and `diff.noprefix=true` makes a stored patch unappliable. A pin is cheaper than a check.
_PINNED_CONFIG = (
    '-c', 'core.quotePath=true',
    '-c', 'core.fsmonitor=false',
    '-c', 'color.ui=never',
    '-c', 'status.showUntrackedFiles=all',
    '-c', 'diff.noprefix=false',
    '-c', 'diff.mnemonicPrefix=false',
    '-c', 'diff.relative=false',
    '-c', 'diff.interHunkContext=0',
    '-c', 'diff.suppressBlankEmpty=false',
)

# Every path handed to git is a NAME, never a pattern (#551). Measured in review: an untracked file
# named `configs/*` or `:(icase)configs` widened a path list back onto the credential files the
# patch had excluded. Literal pathspecs turn that off for every call.
_LITERAL_PATHSPECS = '--literal-pathspecs'

# Environment variables that are dropped before every call, for the same reason the settings are
# pinned: `GIT_DIFF_OPTS` and `GIT_EXTERNAL_DIFF` override the rendering (measured: zero context, a
# patch that no longer applies); `GIT_DIR`, `GIT_WORK_TREE` and their kin — set inside a git hook —
# silently point `-C <root>` at ANOTHER repository; `GIT_CONFIG_*` would inject settings the pins
# do not cover; the pathspec switches would undo `--literal-pathspecs`. Named one by one rather than
# every `GIT_*`, so a variable nobody listed here — `GIT_CEILING_DIRECTORIES`, git's own test hooks —
# keeps its documented meaning.
_DROPPED_GIT_ENV = frozenset({
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_DIFF_OPTS', 'GIT_EXTERNAL_DIFF',
    'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_GLOB_PATHSPECS',
    'GIT_NOGLOB_PATHSPECS', 'GIT_ICASE_PATHSPECS', 'GIT_LITERAL_PATHSPECS',
})
_DROPPED_GIT_ENV_PREFIXES = ('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')

# The rendering of a stored patch, pinned for the same reason: full object ids, no colour, no
# external or text-conversion drivers, no rename detection, a fixed algorithm and context.
_PATCH_FORMAT = (
    '--binary', '--full-index', '--no-color', '--no-ext-diff', '--no-textconv', '--no-renames',
    '--src-prefix=a/', '--dst-prefix=b/', '--diff-algorithm=myers', '-U3',
    '--ignore-submodules=none', '--inter-hunk-context=0', '-O/dev/null',
)


@lru_cache(maxsize=1)
def get_git_commit() -> Optional[str]:
    """
    Get the short commit hash of THIS repository — the one the running code is imported from.

    Addressed by the code's own location rather than the process cwd (#551): a run started with
    its cwd in one checkout while `PYTHONPATH` imports the code from another would otherwise name
    the wrong commit, and the header would contradict its own code identity.

    The cheap read — one subprocess, ~68 ms against the ~2.0 s of the full one. Cached for
    the same reason and with the same consequence as `get_git_info`: the checked-out commit
    does not change under a running process, and a scripted test must clear it.

    Returns:
        Short commit hash, or None if git is unavailable or not in a repo
    """
    root = get_framework_root()
    if root is None:
        return None
    try:
        result = _git(root, ['rev-parse', '--short', 'HEAD'])
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _drop_untracked_under(status_lines: List[str], directory: str) -> List[str]:
    """
    Remove untracked entries that live under one directory.

    Exists for a self-inflicted wound: a certificate run writes its artifact into its
    reports directory, so the very next run reads its own output as an uncommitted change
    and reports the tree dirty although every line of code is committed. Only UNTRACKED
    entries are dropped — a modified tracked file under the same directory is a real
    change and still counts.

    Args:
        status_lines: Porcelain lines as git printed them
        directory: Directory whose untracked entries do not count

    Returns:
        The lines that remain
    """
    prefix = Path(directory).as_posix().rstrip('/') + '/'
    kept = []
    for line in status_lines:
        path = line[3:].strip().strip('"')
        if line.startswith('??') and path.startswith(prefix):
            continue
        kept.append(line)
    return kept


@lru_cache(maxsize=None)
def get_git_info(ignore_untracked_under: Optional[str] = None) -> Optional[GitInfo]:
    """
    Get full git repository information (branch, commit, date, message, dirty).

    CACHED per process, keyed on the argument — a certificate asks a different question
    than a run report and must keep its own answer. The cache is what makes the cost
    bearable: one CLI run used to pay it 2-3 times, and a pytest process once per run it
    executed. The working tree is read ONCE, at the first call.

    That is a semantic choice, not only a speed one: a long live session then reports the
    tree state it STARTED from, which is the honest answer — it describes the code that
    ran, not the code that happens to be checked out when the session ends.

    A test that scripts the git subprocess must call `clear_git_caches()` between cases, or
    the second case reads the first case's answer — clearing this cache alone is not enough,
    because the status half is read through `get_repo_status`, which caches on its own.

    Args:
        ignore_untracked_under: Directory whose untracked files do not make the tree
            dirty; a certificate passes its own reports directory so it is not dirtied
            by the artifact of the previous run

    Returns:
        GitInfo with the working-tree state, or None if git is unavailable
        or not in a git repo
    """
    try:
        root = get_framework_root()
        commit = get_git_commit()
        if root is None:
            return None
        if commit is None:
            return None
        branch = _checked(root, ['rev-parse', '--abbrev-ref', 'HEAD'])
        commit_date = datetime.fromisoformat(
            _checked(root, ['log', '-1', '--format=%cI'])).astimezone(timezone.utc)
        commit_message = _checked(root, ['log', '-1', '--format=%s'])

        # Check for uncommitted changes — through the per-root read, so a process that has
        # already captured this repository's code identity (#551) does not pay the ~1.8 s
        # `git status` a second time.
        repo_status = get_repo_status(root)
        if repo_status is None:
            return None
        status_lines = list(repo_status.status_lines)
        if ignore_untracked_under:
            status_lines = _drop_untracked_under(status_lines, ignore_untracked_under)

        return GitInfo(
            branch=branch,
            commit=commit,
            date=commit_date,
            message=commit_message,
            dirty=bool(status_lines),
            uncommitted_count=len(status_lines)
        )

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or not in a git repo - not critical
        return None


def _git(root: str, args: List[str], text: bool = True,
         env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """
    Run one git command inside a given repository, with the pinned configuration.

    Args:
        root: Directory git is pointed at with `-C`
        args: The git arguments after the pinned options
        text: Decode stdout as text; False keeps the raw bytes a binary patch needs
        env: Variables handed to git on purpose — a temporary index is handed over this way;
            the inherited variables in `_DROPPED_GIT_ENV` are dropped

    Returns:
        The completed process; the caller decides what a non-zero exit means
    """
    clean_env = {key: value for key, value in os.environ.items()
                 if key not in _DROPPED_GIT_ENV and not key.startswith(_DROPPED_GIT_ENV_PREFIXES)}
    clean_env.update(env or {})
    return subprocess.run(['git', '-C', root, _LITERAL_PATHSPECS, *_PINNED_CONFIG, *args],
                          capture_output=True, text=text, timeout=30, env=clean_env)


def _checked(root: str, args: List[str]) -> str:
    """
    Run a git command that must succeed and answer with one line of text.

    Args:
        root: The repository
        args: The git arguments

    Returns:
        Stripped stdout
    """
    result = _git(root, args)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result.stdout.strip()


@lru_cache(maxsize=1)
def git_available() -> bool:
    """
    Whether a git binary can be run at all — kept apart from "this is no repository" (#551).

    A path with no repository and a host with no git look alike from `rev-parse`; they are not
    alike to a reader, because only the first is a statement about the code.

    Returns:
        True when `git --version` answers
    """
    try:
        return subprocess.run(['git', '--version'], capture_output=True,
                              timeout=5).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@lru_cache(maxsize=1)
def get_framework_root() -> Optional[str]:
    """
    The top level of THIS repository — found from this module's own location, never the cwd.

    The ONE place the framework root is decided (#551): the header's commit, the ledger's branch
    and dirty flag and the code identity all read it, so no record can pair one tree's commit
    with another tree's state.

    Returns:
        The repository's top-level directory, or None when git cannot answer
    """
    return get_repo_toplevel(str(Path(__file__).resolve().parent))


@lru_cache(maxsize=None)
def get_repo_toplevel(path: str) -> Optional[str]:
    """
    Find the repository a path belongs to (#551).

    A strategy file names no repository; this is how its owning one is found. Cached per path,
    like every read here, because a run asks once per component and the answer cannot change
    under a running process.

    Args:
        path: A file or directory

    Returns:
        The repository's top-level directory, or None when the path is in no repository or
        git is unavailable
    """
    target = Path(path)
    directory = target if target.is_dir() else target.parent
    try:
        result = _git(str(directory), ['rev-parse', '--show-toplevel'])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@lru_cache(maxsize=None)
def get_repo_status(root: str) -> Optional[RepoStatus]:
    """
    Read one repository's commit and dirty state, addressed by its root (#551).

    The same `git status` `get_git_info` runs, so the same cost applies to the same tree:
    measured 2026-09-24, ~1.8 s on `/app` and ~0.12 s on `user_algos/`. CACHED per root — a
    process pays each repository once, and the answer is the tree state it STARTED from.

    Nothing a user configured may make it answer "clean": untracked files are listed one by one
    whatever `status.showUntrackedFiles` says, submodules are never ignored, and an index entry
    flagged assume-unchanged or skip-worktree counts as a change — git stops LOOKING at such a
    file, which is the opposite of the file being unchanged.

    Args:
        root: The repository's top-level directory

    Returns:
        The repository's state, or None when git is unavailable or the root is no repository
    """
    try:
        status = _git(root, ['status', '--porcelain=v1', '-z', '--untracked-files=all',
                             '--ignore-submodules=none', '--no-renames'])
        if status.returncode != 0:
            return None
        head = _git(root, ['rev-parse', '--short', 'HEAD'])
        branch = _git(root, ['rev-parse', '--abbrev-ref', 'HEAD'])
        flagged = _git(root, ['ls-files', '-v', '-z'])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if flagged.returncode != 0:
        # Without the flag listing nobody knows which entries git has stopped looking at — an
        # unknown state, never a clean one.
        return None

    lines = [entry for entry in status.stdout.split('\0') if entry.strip()]
    paths = [entry[3:] for entry in lines]
    untracked = [entry[3:] for entry in lines if entry.startswith('??')]
    # `ls-files -v` tags an assume-unchanged entry with a LOWERCASE letter and a skip-worktree
    # entry with 'S'; either means git is not looking at that file.
    hidden = [entry[2:] for entry in flagged.stdout.split('\0')
              if len(entry) > 2 and (entry[0].islower() or entry[0] == 'S')]
    for path in hidden:
        if path not in paths:
            lines.append(f'!h {path}')
            paths.append(path)
    return RepoStatus(
        root=root,
        commit=head.stdout.strip() if head.returncode == 0 else None,
        branch=branch.stdout.strip() if branch.returncode == 0 else None,
        dirty=bool(lines),
        status_lines=lines,
        changed_paths=paths,
        untracked_paths=untracked,
        hidden_paths=hidden,
    )


@lru_cache(maxsize=None)
def get_repo_patch(root: str, exclude: Tuple[str, ...] = ()) -> Optional[bytes]:
    """
    Everything that separates a repository's working tree from its commit, as one patch (#551).

    One `git diff` against HEAD, limited to the paths `git status` named, over a TEMPORARY copy
    of the index: untracked files are added there as intent-to-add and the assume-unchanged /
    skip-worktree flags are cleared there, so the repository's own index is never touched and a
    single call renders tracked changes, deletions and new files alike. The rendering is pinned
    (`_PATCH_FORMAT`, `_PINNED_CONFIG`) against every setting known to break it or move it, so the
    patch applies under any configuration. The bytes are one RENDERING, though, and a setting not
    pinned here may still reorder a hunk header; the code's identity is the content digest in the
    code identity, never these bytes.
    Measured 2026-09-24 on `/app` with 76 changes: ~1.2 s, where one `--no-index` call per
    untracked file took ~4.4 s.

    Args:
        root: The repository's top-level directory
        exclude: Repository-relative paths left out of the patch (credential homes)

    Returns:
        The patch bytes (empty for a clean tree), or None when git could not render one
    """
    status = get_repo_status(root)
    if status is None:
        return None
    paths = [path for path in status.changed_paths
             if path not in exclude and not path.endswith('/')]
    if not paths:
        return b''
    try:
        index_path = _checked(root, ['rev-parse', '--path-format=absolute', '--git-path', 'index'])
        scratch = tempfile.mkdtemp(prefix='finiex_patch_index_')
        try:
            temp_index = os.path.join(scratch, 'index')
            if os.path.exists(index_path):
                # copy2 keeps the index's mtime: git trusts an entry's stat data only when it is
                # older than the index file, and a fresh mtime would make a racily-clean entry
                # (edited in the same second, same size) read as unchanged.
                shutil.copy2(index_path, temp_index)
            env = {'GIT_INDEX_FILE': temp_index}
            untracked = [path for path in status.untracked_paths if path in paths]
            if untracked and _git(root, ['add', '--intent-to-add', '--', *untracked],
                                  env=env).returncode != 0:
                return None
            hidden = [path for path in status.hidden_paths if path in paths]
            if hidden and not _unhide_entries(root, hidden, env):
                return None
            head = _git(root, ['rev-parse', '--verify', '--quiet', 'HEAD'])
            base = 'HEAD' if head.returncode == 0 else _empty_tree(root)
            if base is None:
                return None
            diff = _git(root, ['diff', *_PATCH_FORMAT, base, '--', *paths], text=False, env=env)
            if diff.returncode != 0:
                return None
            return diff.stdout
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError,
            OSError):
        return None


def _unhide_entries(root: str, hidden: List[str], env: dict) -> bool:
    """
    Make the temporary index LOOK at entries flagged assume-unchanged or skip-worktree.

    Each entry is re-registered with its own mode and blob, which clears both flags and zeroes its
    stat data. The flags alone are not enough, measured 2026-09-24: `update-index` applies only the
    FIRST of `--no-assume-unchanged --no-skip-worktree` to a path, and a flag-free entry still
    carries the stat data git recorded before it stopped looking — an edit of the same size in the
    same second then matches it and never reaches the diff. Zeroed stat data forces a content read.

    Args:
        root: The repository's top-level directory
        hidden: Repository-relative paths git was told not to look at
        env: The environment naming the temporary index

    Returns:
        True when every entry was re-registered
    """
    listed = _git(root, ['ls-files', '--stage', '-z', '--', *hidden], env=env)
    if listed.returncode != 0:
        return False
    arguments = []
    for entry in listed.stdout.split('\0'):
        if not entry:
            continue
        meta, path = entry.split('\t', 1)
        mode, blob, _stage = meta.split(' ')
        arguments += ['--cacheinfo', f'{mode},{blob},{path}']
    if not arguments:
        return True
    return _git(root, ['update-index', *arguments], env=env).returncode == 0


def _empty_tree(root: str) -> Optional[str]:
    """
    The empty tree's id in this repository's own object format — SHA-1 or SHA-256.

    Args:
        root: The repository's top-level directory

    Returns:
        The id, or None when git cannot compute it
    """
    result = _git(root, ['hash-object', '-t', 'tree', '/dev/null'])
    return result.stdout.strip() if result.returncode == 0 else None


@lru_cache(maxsize=None)
def list_repo_files(root: str, relative_dir: str) -> Optional[Tuple[str, ...]]:
    """
    The files git would consider part of a directory: tracked plus untracked, minus ignored.

    This is what a package's content digest is taken over — git's ignore rules decide what is
    code, so a `__pycache__` beside a strategy never changes its identity. CACHED like every
    read here, so every run of a process describes the tree the process started from.

    Args:
        root: The repository's top-level directory
        relative_dir: The directory, relative to `root`

    Returns:
        Repository-relative paths in sorted order, or None when git is unavailable
    """
    try:
        result = _git(root, ['ls-files', '--cached', '--others', '--exclude-standard', '-z',
                             '--', relative_dir])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return tuple(sorted({entry for entry in result.stdout.split('\0') if entry}))


@lru_cache(maxsize=None)
def list_ignored_files(root: str, relative_dir: str) -> Optional[Tuple[str, ...]]:
    """
    Files in a directory that the repository IGNORES — code no commit can contain (#551).

    A strategy in a directory its repository ignores is invisible to `git status` and to the
    package listing alike, so without this it would read as committed code with an empty digest.

    Args:
        root: The repository's top-level directory
        relative_dir: The directory, relative to `root`

    Returns:
        Repository-relative paths in sorted order, or None when git is unavailable
    """
    try:
        result = _git(root, ['ls-files', '--others', '--ignored', '--exclude-standard', '-z',
                             '--', relative_dir])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return tuple(sorted({entry for entry in result.stdout.split('\0') if entry}))


def clear_git_caches() -> None:
    """
    Empty every per-process git cache in this module at once.

    A test that scripts the git subprocess must clear BEFORE and AFTER its case (§42): before, or
    it reads the previous case's answer; after, or its fake answer reaches every later test in the
    process. With per-repository reads (#551) there are several caches, and clearing one of them
    leaves the others holding the fake.
    """
    get_git_commit.cache_clear()
    get_git_info.cache_clear()
    git_available.cache_clear()
    get_framework_root.cache_clear()
    get_repo_toplevel.cache_clear()
    get_repo_status.cache_clear()
    get_repo_patch.cache_clear()
    list_repo_files.cache_clear()
    list_ignored_files.cache_clear()
