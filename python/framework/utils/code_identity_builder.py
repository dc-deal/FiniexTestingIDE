"""
FiniexTestingIDE - Code Identity Builder (#551)

Assembles a run's `CodeIdentity` at its START: this repository's state, the state of every other
repository a component was loaded from, and one entry per component the run resolves.

Why at the start, and why here rather than in the ledger: the ledger row is the LAST thing a run
writes (§44), so a session killed before its close used to lose its component versions and its
dirty flag entirely. The header is written first. The ledger reads its provenance from the header
afterwards instead of computing it a second time.

All git access goes through `git_info_utils` (§42) and is cached per repository, so a process
pays each repository's `git status` once — and every run a process builds describes the tree the
process started from. The package digests are cached the same way, so one header never mixes a
cached tree state with a fresh component read. The one deliberately FRESH read is
`verify_component_digests`, which the live guard runs after the pipeline has loaded the code.
"""

import hashlib
import inspect
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from python.configuration.credential_guard import CREDENTIALS_DIR_NAME
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    ComponentRole,
    RepositoryState,
)
from python.framework.utils.git_info_utils import (
    clear_git_caches,
    get_framework_root,
    get_repo_patch,
    get_repo_status,
    get_repo_toplevel,
    git_available,
    list_ignored_files,
    list_repo_files,
)

# How many porcelain lines a state keeps — enough for a refusal to name what is uncommitted,
# bounded so a tree with thousands of changes does not inflate every header.
_MAX_CHANGES = 20

# Directories that are never code, for a package that lies in no repository and so has no
# ignore rules to ask — and for the ignored-file check, which must not count bytecode as code.
_NEVER_CODE_DIRS = frozenset({'__pycache__', '.git', '.pytest_cache'})

# A changed path under a directory named `credentials` — ANY such directory, a wider net than the
# credential guard's `configs/credentials` — is a credential home. Its content is never copied into
# the patch store: a real key pasted into a tracked placeholder would otherwise land in
# `run_patches/` on every run start, before the credential guard ever sees it.

# The namespace of the pre-registered components, as the factories resolve it.
_CORE_PREFIX = 'CORE/'

# The digest each path package had at the LAST capture in this process. A later capture — the next
# sweep combination — that finds a package moved since then re-reads the repositories instead of
# serving their cached state, so one header never pairs fresh component content with a stale
# "clean". Keyed by the component's resolved file.
_LAST_DIGESTS: Dict[str, Optional[str]] = {}

# Where this repository sits when git cannot say — this module's own checkout, derived from its
# location (python/framework/utils/ → the root), so a refusal can still name a path.
_MODULE_CHECKOUT = Path(__file__).resolve().parents[3]

# Receives (repository root, patch hash, patch bytes) and answers where the patch was stored — or
# None when it could not be, which leaves the diff hash recorded and the patch reference empty. The
# root is what lets the sink keep a patch beside the code it describes.
PatchSink = Callable[[str, str, bytes], Optional[str]]


def build_code_identity(
    strategy_configs: List[Dict],
    patch_sink: Optional[PatchSink] = None,
) -> CodeIdentity:
    """
    Capture which code a run is about to run.

    Args:
        strategy_configs: Every strategy_config the run will execute (one per scenario in a
            simulation, one for a live session) — the union of their components is recorded
        patch_sink: Stores the patch of a dirty repository and returns its reference; None
            records the diff hash without storing the patch

    Returns:
        The run's code identity
    """
    framework_root = get_framework_root()
    components = _resolve_components(strategy_configs, framework_root)
    if _moved_since_last_capture(components):
        # Code moved since this process last captured: the cached repository states describe a
        # tree that no longer exists. Read them again, at the cost of one `git status` each.
        clear_git_caches()
        framework_root = get_framework_root()
        components = _resolve_components(strategy_configs, framework_root)
    for component in components:
        if component.source_path is not None and component.package_digest is not None:
            _LAST_DIGESTS[component.source_path] = component.package_digest
    have_git = git_available()

    other_roots: List[str] = []
    outside: Dict[str, Optional[bool]] = {}
    for component in components:
        if component.source_path is None:
            # Not resolvable at capture: nothing to record here. The guard treats such a
            # component as moved if the pipeline later loads it after all.
            continue
        if component.type.startswith(_CORE_PREFIX):
            # A CORE component lives in THIS repository, whose state is the framework entry —
            # with git or without it, it is never a repository of its own.
            continue
        if component.repository is None and component.package_digest is None:
            # Its package could not be read: whatever git says about the directory, nobody can
            # vouch for this code — unknown, never "not under version control".
            outside.setdefault(str(Path(component.source_path).parent), None)
        elif component.repository is None:
            # No repository answered for it: under no version control, ignored by the one around
            # it — or git could not run or refused the checkout, in which case nobody knows.
            where, membership = _membership(str(Path(component.source_path).parent), have_git)
            outside.setdefault(where, membership)
        elif component.repository != framework_root and component.repository not in other_roots:
            other_roots.append(component.repository)

    repositories = [_repository_state(root, patch_sink) for root in other_roots]
    repositories += [RepositoryState(root=directory, in_repository=membership)
                     for directory, membership in outside.items()]
    return CodeIdentity(
        framework=_framework_state(framework_root, have_git, patch_sink),
        repositories=repositories,
        components=components,
    )


def verify_component_digests(identity: CodeIdentity) -> List[ComponentIdentity]:
    """
    Re-read every path component's package NOW and name the ones whose content moved.

    The capture reads the packages before the pipeline loads them; the pipeline then executes the
    files from disk again. An edit in between would run code the header does not describe. This
    is the fresh read that closes that window — deliberately uncached, and cheap: one package
    directory per component.

    Args:
        identity: The identity captured at the start

    Returns:
        The components whose package no longer matches its recorded digest — and every component
        the capture could not resolve at all
    """
    moved = []
    for component in identity.components:
        if component.source_path is None:
            # Unresolvable at capture, yet the session got as far as the guard — the pipeline
            # loaded it after all, from a file nobody described. That is code that moved.
            moved.append(component)
            continue
        if component.package_digest is None:
            continue
        source = Path(component.source_path)
        current = _compute_package_digest(str(source), component.repository)
        if current != component.package_digest:
            moved.append(component)
    return moved


def _framework_state(framework_root: Optional[str], have_git: bool,
                     patch_sink: Optional[PatchSink]) -> RepositoryState:
    """
    This repository's state — never None, so a refusal can always name where the code came from.

    Args:
        framework_root: The repository's top level, None when git could not resolve it
        have_git: Whether a git binary runs at all
        patch_sink: Stores the patch, or None

    Returns:
        The state; unknown (in_repository None) where git does not run or refuses the checkout,
        unversioned outside any repository
    """
    if framework_root is not None:
        return _repository_state(framework_root, patch_sink)
    root, membership = _membership(str(_MODULE_CHECKOUT), have_git)
    return RepositoryState(root=root, in_repository=membership)


def _membership(directory: str, have_git: bool) -> Tuple[str, Optional[bool]]:
    """
    What a directory no repository answered for is — absent from version control, or unknown.

    `rev-parse` fails alike for a directory in no repository and for a checkout git REFUSES to
    read ("detected dubious ownership" when another user owns it — common for a mount in a
    container), and only the first is a statement about the code. So a `.git` at or above the
    directory with no answer from git is a refusal: unknown, never "not under version control" —
    and the checkout holding that `.git` is what is recorded, because it is what a
    `safe.directory` entry has to name. Read only on this failure path, so its few `stat()` calls
    cost nothing on a working tree.

    Args:
        directory: The directory the code was loaded from
        have_git: Whether a git binary runs at all

    Returns:
        (the directory to record, membership): False when git answered — no repository, or one
        that ignores the directory — and None when git does not run or refuses the checkout
    """
    if have_git and get_repo_toplevel(directory) is not None:
        return directory, False
    path = Path(directory)
    checkout = next((candidate for candidate in (path, *path.parents)
                     if (candidate / '.git').exists()), None)
    if checkout is not None:
        return str(checkout), None
    return directory, None if not have_git else False


def _repository_state(root: str, patch_sink: Optional[PatchSink]) -> RepositoryState:
    """
    One repository's state; when it is dirty, its delta digested and its patch stored.

    Args:
        root: The repository's top-level directory
        patch_sink: Stores the patch, or None

    Returns:
        The repository's state; `commit` None when git could not read it
    """
    status = get_repo_status(root)
    if status is None:
        return RepositoryState(root=root, commit=None)
    state = RepositoryState(
        root=root,
        commit=status.commit,
        branch=status.branch,
        dirty=status.dirty,
        uncommitted_count=len(status.status_lines),
        changes=status.status_lines[:_MAX_CHANGES],
        restorable=not status.dirty and status.commit is not None,
    )
    if not status.dirty:
        return state

    excluded = sorted(path for path in status.changed_paths if _is_credential_path(path))
    state.patch_excluded = excluded
    state.diff_hash = _delta_digest(root, status.changed_paths, set(excluded))
    patch = get_repo_patch(root, tuple(excluded))
    if patch is None or patch_sink is None:
        return state
    state.patch_ref = patch_sink(root, hashlib.sha256(patch).hexdigest(), patch)
    # A nested repository shows as one directory entry — untracked with a trailing slash, or a
    # submodule's gitlink without one (its patch hunk is a `Subproject commit …-dirty` line); its
    # content cannot be in the patch, so a tree dirty through one is recorded, never claimed
    # restorable.
    nested = any(path.endswith('/') or _is_directory(Path(root) / path)
                 for path in status.changed_paths)
    state.restorable = state.patch_ref is not None and not nested
    return state


def _delta_digest(root: str, changed_paths: List[str], excluded: set) -> str:
    """
    SHA256 over the content of every changed path — what the working tree adds to its commit.

    Per path, in path order: the path, then its executable bit and a SHA256 of its bytes, a
    symlink's target, a deletion marker, or — for a credential home — only a marker that it
    changed. Git's rendering plays no part, so the same delta has the same digest on any machine,
    under any configuration and any git version.

    Args:
        root: The repository's top-level directory
        changed_paths: Every path `git status` named
        excluded: Paths whose content is deliberately not read

    Returns:
        The hex digest
    """
    digest = hashlib.sha256()
    base = Path(root)
    for relative in sorted(set(changed_paths)):
        target = base / relative
        if relative in excluded:
            entry = 'excluded'
        elif relative.endswith('/'):
            entry = 'nested-repository'
        elif target.is_symlink():
            entry = f'symlink:{target.readlink()}'
        elif target.is_file():
            executable = 'x' if target.stat().st_mode & 0o111 else '-'
            entry = f'{executable}:{hashlib.sha256(target.read_bytes()).hexdigest()}'
        elif not target.exists():
            entry = 'deleted'
        else:
            entry = 'directory'
        digest.update(f'{relative}\0{entry}\0'.encode('utf-8'))
    return digest.hexdigest()


def _is_directory(target: Path) -> bool:
    """
    Whether a changed path is a directory in its own right — a symlink to one is a file entry.

    Args:
        target: The path in the working tree

    Returns:
        True for a real directory
    """
    return target.is_dir() and not target.is_symlink()


def _is_credential_path(relative: str) -> bool:
    """
    Whether a repository-relative path lies in a credential home.

    Args:
        relative: The path as git names it

    Returns:
        True when any directory on the way to it is named `credentials`
    """
    return CREDENTIALS_DIR_NAME in Path(relative.rstrip('/')).parts[:-1]


def _resolve_components(strategy_configs: List[Dict],
                        framework_root: Optional[str]) -> List[ComponentIdentity]:
    """
    Resolve every decision logic and worker instance the configurations name, once each.

    Resolution is the factories' own — the same path the run takes — so the file recorded is the
    file that is loaded.

    Args:
        strategy_configs: The run's strategy configurations
        framework_root: This repository's top-level directory

    Returns:
        One entry per distinct (role, name, type)
    """
    logger = get_global_logger()
    decision_factory = DecisionLogicFactory(logger)
    worker_factory = WorkerFactory(logger)
    seen = set()
    components: List[ComponentIdentity] = []

    for strategy_config in strategy_configs:
        decision_type = (strategy_config or {}).get('decision_logic_type', '')
        entries = [(ComponentRole.DECISION, decision_type, decision_type)] if decision_type else []
        for name, worker_type in (strategy_config or {}).get('worker_instances', {}).items():
            entries.append((ComponentRole.WORKER, name, worker_type))
        for role, name, type_string in entries:
            key = (role, name, type_string)
            if key in seen:
                continue
            seen.add(key)
            resolver = (decision_factory.resolve_logic_class if role is ComponentRole.DECISION
                        else worker_factory.resolve_worker_class)
            components.append(_component_identity(role, name, type_string, resolver,
                                                  framework_root))
    return components


def _component_identity(role: ComponentRole, name: str, type_string: str, resolver: Callable,
                        framework_root: Optional[str]) -> ComponentIdentity:
    """
    Identify one component: its declared version, its file, its repository and — for a component
    loaded from a path — a digest of its whole package.

    ANY failure to resolve degrades to an entry without a source, never to an error. The capture
    runs before the pipeline; the pipeline then loads the same component inside the session's
    startup handling and fails there with the factory's own message and a STARTUP FAILED block. A
    component module that raises at import (a NameError, a failing `get_metadata()`) must reach
    that path, not escape from here without a record.

    Args:
        role: Decision logic or worker
        name: Worker instance name, or the decision type
        type_string: The type the configuration named
        resolver: The factory method that resolves it
        framework_root: This repository's top-level directory

    Returns:
        The component's identity
    """
    try:
        cls, source_path = resolver(type_string)
        version = cls.get_metadata().version
    except Exception as error:
        get_global_logger().warning(
            f"Code identity: could not resolve {role} '{type_string}' ({error}) — recorded "
            f'without a source; the pipeline reports the failure itself')
        return ComponentIdentity(role=role, name=name, type=type_string)

    if source_path is None:
        # A pre-registered CORE component: its file is inside this repository, whose state
        # already covers it, so no digest is needed.
        core_file = Path(inspect.getfile(cls)).resolve()
        return ComponentIdentity(role=role, name=name, type=type_string, version=version,
                                 source_path=_relative_to(core_file, framework_root),
                                 repository=framework_root)

    source = Path(source_path).resolve()
    repository = get_repo_toplevel(str(source))
    if repository is not None and _package_is_ignored(source, repository):
        # The repository around it ignores it, so no commit can contain it: it is unversioned
        # code, whatever `git status` says about the rest of the tree.
        repository = None
    digest = _compute_package_digest(str(source), repository)
    if digest is None:
        # A package that could not be read cannot be vouched for by its repository's state: it is
        # recorded as outside any repository, which the capture then states as unknown.
        repository = None
    return ComponentIdentity(
        role=role, name=name, type=type_string, version=version,
        source_path=str(source), repository=repository, package_digest=digest,
    )


def _package_scope(source: Path, repository: Optional[str]) -> Tuple[Path, str]:
    """
    The directory a component's package is taken over, and that directory relative to its
    repository. A file directly in a repository's root is its own package — the repository as a
    whole is not.

    Args:
        source: The component's resolved file
        repository: Its repository, or None

    Returns:
        (package directory, repository-relative spec: the directory, or the file itself at root)
    """
    package_dir = source.parent
    if repository is None:
        return package_dir, str(package_dir)
    relative_dir = _relative_to(package_dir, repository)
    if relative_dir == '.':
        return package_dir, _relative_to(source, repository)
    return package_dir, relative_dir


def _package_is_ignored(source: Path, repository: str) -> bool:
    """
    Whether the component's file — or any other code file in its package — is ignored by the
    repository around it.

    Args:
        source: The component's resolved file
        repository: The repository `rev-parse` answered for it

    Returns:
        True when the package holds code no commit of that repository can contain — and when git
        could not answer, because an unanswered question is not a "no"
    """
    _, spec = _package_scope(source, repository)
    ignored = list_ignored_files(repository, spec)
    if ignored is None:
        return True
    return any(not _NEVER_CODE_DIRS.intersection(Path(entry).parts) and not entry.endswith('.pyc')
               for entry in ignored)


def _moved_since_last_capture(components: List[ComponentIdentity]) -> bool:
    """
    Whether any path package differs from what the previous capture in this process recorded.

    Args:
        components: This capture's freshly resolved components

    Returns:
        True when a package this process has captured before now digests differently
    """
    return any(component.source_path in _LAST_DIGESTS
               and _LAST_DIGESTS[component.source_path] != component.package_digest
               for component in components if component.source_path is not None)


def _compute_package_digest(source: str, repository: Optional[str]) -> Optional[str]:
    """
    SHA256 over a package's files — path and content, in path order — read NOW.

    Inside a repository git's ignore rules decide which files are code; outside one, everything
    but caches is. Content rather than git's tree id, so the same code has the same digest whether
    it is committed or not.

    Args:
        source: The component's resolved file
        repository: The repository it belongs to, or None

    Returns:
        The hex digest, or None when the files could not be listed or read
    """
    source_path = Path(source)
    package_dir, spec = _package_scope(source_path, repository)
    try:
        if repository is not None:
            # The UNCACHED listing: a file added after the capture is exactly what the fresh
            # verification must see. The capture itself is cached one level up, per package.
            listed = list_repo_files.__wrapped__(repository, spec)
            if listed is None:
                return None
            files = [(entry, Path(repository) / entry) for entry in listed]
        else:
            files = sorted(
                (str(path.relative_to(package_dir)), path) for path in package_dir.rglob('*')
                if path.is_file() and not _NEVER_CODE_DIRS.intersection(path.parts)
                and path.suffix != '.pyc')

        digest = hashlib.sha256()
        for relative, path in files:
            if not path.is_file():
                continue
            digest.update(relative.encode('utf-8') + b'\0')
            digest.update(path.read_bytes() + b'\0')
        return digest.hexdigest()
    except OSError as error:
        get_global_logger().warning(f'Code identity: package of {source} unreadable ({error})')
        return None


def _relative_to(path: Path, root: Optional[str]) -> str:
    """
    A path relative to a root when it lies under it, otherwise the path as given.

    Args:
        path: The path to express
        root: The root, or None

    Returns:
        The relative path, or the absolute one
    """
    if root is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


def clear_package_digest_cache() -> None:
    """Forget what earlier captures in this process recorded — the builder's own half of
    `clear_git_caches`, for a test that must start from a process that captured nothing."""
    _LAST_DIGESTS.clear()
