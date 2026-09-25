"""
Code identity against real, temporary git repositories (#551).

A run header used to record `git_commit` of this repository and nothing else about the code that
ran. A strategy may live in another repository entirely — `user_algos/` is its own, ignored by
this one — so a run of an uncommitted strategy read as clean and carried no trace of its code
beyond the version string its author chose to declare.

These tests pin the builder that closes that gap: which repositories a run records, when a
component counts as versioned, what the diff hash is taken over, what the patch may and may not
carry, and when a capture is allowed to repeat itself. The git reads underneath are pinned on
their own in `test_git_repo_identity.py`.

Repositories are created in `tmp_path` rather than read from this working tree: the assertions
are about the rules, and a test reading the real tree would pass or fail by whatever the
developer happened to have open. The framework's own repository is redirected to a temporary one
for the same reason — and because `git status` on this tree costs ~1.8 s (§42), which a rule test
has no reason to pay.
"""

import hashlib
import inspect
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.store.run_patch_store import PATCH_SUFFIX, RunPatchStore
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentRole,
    RepositoryState,
)
from python.framework.utils import code_identity_builder
from python.framework.utils.code_identity_builder import (
    build_code_identity,
    clear_package_digest_cache,
    verify_component_digests,
)
from python.framework.utils.git_info_utils import clear_git_caches, get_repo_patch
from python.framework.workers.core.rsi_worker import RsiWorker
from tests.shared.git_test_repos import (
    commit_all,
    make_repo,
    restore_on_fresh_clone,
    run_git,
    sha256_repositories_supported,
    working_tree,
    write_files,
)
from tests.shared.recording_logger import RecordingLogger

# Real CORE components, copied into temporary packages so they load from a PATH the way a user
# strategy does. Their declared versions are what the component entries must carry.
_LOGIC_SOURCE = Path(inspect.getfile(SimpleConsensus))
_WORKER_SOURCE = Path(inspect.getfile(RsiWorker))

# The digest of nothing at all — what a package whose files were all skipped would carry.
_EMPTY_DIGEST = hashlib.sha256(b'').hexdigest()


class _RecordingSink:
    """A patch sink that keeps what it was handed and answers an in-memory reference."""

    def __init__(self):
        self.calls: List[Tuple[str, bytes]] = []
        self.roots: List[str] = []

    def __call__(self, root: str, patch_hash: str, patch: bytes) -> Optional[str]:
        """
        Record one stored patch.

        Args:
            root: The repository the patch belongs to
            patch_hash: The key the builder stores the patch under
            patch: The patch bytes

        Returns:
            The reference the builder records
        """
        self.roots.append(root)
        self.calls.append((patch_hash, patch))
        return f'memory/{patch_hash}'


def _fresh() -> None:
    """Forget every cached read — what a NEW process would see. Both halves, never one."""
    clear_git_caches()
    clear_package_digest_cache()


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Every read here is cached per process; a test repository must never see a stale one."""
    _fresh()
    yield
    _fresh()


@pytest.fixture(autouse=True)
def framework_repo(tmp_path, monkeypatch) -> Path:
    """
    Stand a temporary repository in for this one as the builder's FRAMEWORK repository.

    Only the builder's answer to "which repository is the framework" is redirected; every other
    path is answered by git as usual, which is what the tests below exercise. Autouse, so no test
    here can reach this working tree's state by forgetting to ask for it.
    """
    repo = make_repo(tmp_path / 'framework', {'.gitignore': '__pycache__/\n',
                                              'app.py': 'VALUE = 1\n'})
    monkeypatch.setattr(code_identity_builder, 'get_framework_root', lambda: str(repo))
    return repo


def _algo_repo(tmp_path: Path, commit_package: bool) -> Path:
    """
    An algo repository with one strategy package, committed or left untracked.

    Ignores bytecode caches the way a real algo repository does — loading the strategy writes a
    `__pycache__` beside it, and that is not code.

    Args:
        tmp_path: The test's temporary directory
        commit_package: Whether the package is part of the commit

    Returns:
        The repository's top-level directory
    """
    repo = make_repo(tmp_path / 'algos', {'.gitignore': '__pycache__/\n', 'README.md': 'algos\n'})
    write_files(repo, {'my_strategy/my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8'),
                       'my_strategy/helpers.py': 'THRESHOLD = 0.6\n'})
    if commit_package:
        commit_all(repo, 'add my_strategy')
    return repo


def _strategy(decision: Path, workers: Optional[Dict[str, str]] = None) -> Dict:
    """
    A strategy_config naming a decision logic by path, as a profile does.

    Args:
        decision: The decision logic's file
        workers: Worker instance name → type

    Returns:
        The strategy_config
    """
    return {'decision_logic_type': str(decision), 'worker_instances': workers or {}}


def _package_strategy(repo: Path) -> Dict:
    """The strategy_config of the package `_algo_repo` creates."""
    return _strategy(repo / 'my_strategy' / 'my_strategy.py')


def _restores(repo: Path, commit: Optional[str], patch: bytes, target: Path) -> bool:
    """
    Whether the recorded commit plus the stored patch reproduce a working tree byte for byte.

    Args:
        repo: The repository the run ran from
        commit: The commit the header recorded
        patch: The stored patch
        target: Where the fresh checkout is created

    Returns:
        True when the restored tree equals the original
    """
    return working_tree(restore_on_fresh_clone(repo, commit, patch, target)) == working_tree(repo)


def _state_of(identity: CodeIdentity, root: Path) -> RepositoryState:
    """
    The recorded state of one repository, looked up by its root.

    Args:
        identity: The captured code identity
        root: The repository's top-level directory

    Returns:
        That repository's state — the framework's or one of the others
    """
    states = [identity.framework] + list(identity.repositories)
    return next(state for state in states if state.root == str(root))


class TestTheFrameworkRepositoryIsAlwaysStated:
    """
    This repository's state is never missing from a capture. A dirty tree records WHAT separates
    it from its commit and where the patch lies; an unreadable one says it could not tell.
    """

    def test_a_dirty_framework_records_its_delta_and_stores_its_patch(self, framework_repo,
                                                                     tmp_path):
        write_files(framework_repo, {'app.py': 'VALUE = 2\n', 'extra.py': 'EXTRA = 1\n'})
        sink = _RecordingSink()

        identity = build_code_identity([], patch_sink=sink)

        framework = identity.framework
        assert framework.root == str(framework_repo) and framework.in_repository is True
        assert framework.commit == run_git(framework_repo, 'rev-parse', '--short', 'HEAD')
        assert framework.dirty is True
        assert framework.changes == [' M app.py', '?? extra.py']
        assert framework.diff_hash and len(framework.diff_hash) == 64
        assert len(sink.calls) == 1
        assert sink.roots == [str(framework_repo)], 'the sink learns whose patch it keeps'
        patch_hash, patch = sink.calls[0]
        assert patch_hash == hashlib.sha256(patch).hexdigest(), 'stored under its own bytes'
        assert framework.patch_ref == f'memory/{patch_hash}'
        assert framework.restorable is True
        assert identity.is_dirty() is True
        assert _restores(framework_repo, framework.commit, patch, tmp_path / 'fresh')

    def test_a_clean_framework_is_restorable_from_its_commit_alone(self, framework_repo):
        sink = _RecordingSink()

        identity = build_code_identity([], patch_sink=sink)

        framework = identity.framework
        assert framework.dirty is False and framework.restorable is True
        assert framework.diff_hash is None and framework.patch_ref is None
        assert sink.calls == [], 'a clean tree has no patch to keep'
        assert identity.is_dirty() is False

    def test_without_git_the_state_is_unknown_and_never_clean(self, tmp_path, monkeypatch):
        """
        No git binary is not "no repository": the state is UNKNOWN, it still names where the code
        was loaded from, and a guard reading it must not see a clean tree.
        """
        package = tmp_path / 'loose' / 'my_strategy'
        package.mkdir(parents=True)
        shutil.copy(_LOGIC_SOURCE, package / 'my_strategy.py')
        monkeypatch.setattr(code_identity_builder, 'get_framework_root', lambda: None)
        monkeypatch.setenv('PATH', str(tmp_path / 'no_binaries_here'))

        identity = build_code_identity([_strategy(package / 'my_strategy.py')])

        framework = identity.framework
        assert framework is not None and framework.in_repository is None
        assert (Path(framework.root) / 'python/framework/utils/code_identity_builder.py'
                ).resolve() == Path(code_identity_builder.__file__).resolve()
        assert framework.restorable is False
        assert [(s.root, s.in_repository) for s in identity.repositories] == [
            (str(package), None)]
        assert identity.is_dirty() is True

    def test_a_checkout_git_refuses_to_read_is_unknown_not_unversioned(self, tmp_path,
                                                                      monkeypatch):
        """
        `rev-parse` fails alike for a directory in no repository and for a checkout git refuses
        (dubious ownership on a container mount). Only the first is a statement about the code,
        so a `.git` above the package with no answer from git records "unknown" — under the
        checkout holding it, which is what a `safe.directory` entry has to name. The refusal is
        produced here with `GIT_CEILING_DIRECTORIES`, which stops the search before the checkout.
        """
        repo = _algo_repo(tmp_path, commit_package=True)
        monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(repo))

        identity = build_code_identity([_package_strategy(repo)])

        assert identity.components[0].repository is None
        assert [(s.root, s.in_repository) for s in identity.repositories] == [(str(repo), None)]
        assert identity.is_dirty() is True


class TestTheDiffIdentifiesAndRestoresTheTree:
    """A dirty tree stays identifiable by its diff hash, and restorable from its patch."""

    def test_the_diff_hash_is_stable_across_captures(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.7\n', 'notes.txt': 'n\n'})

        first = _state_of(build_code_identity([_package_strategy(repo)]), repo).diff_hash
        _fresh()
        second = _state_of(build_code_identity([_package_strategy(repo)]), repo).diff_hash

        assert first and first == second

    def test_a_different_tree_has_a_different_hash(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.7\n'})
        before = _state_of(build_code_identity([_package_strategy(repo)]), repo).diff_hash

        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.8\n'})
        _fresh()

        assert _state_of(build_code_identity([_package_strategy(repo)]), repo).diff_hash != before

    def test_the_stored_patch_restores_the_tree_on_a_fresh_checkout(self, tmp_path):
        """
        The property the patch store exists for: the commit plus the stored patch IS the code
        that ran. Covers a modified file, a deleted file, an untracked text file in a new
        directory and an untracked binary file — through the real store, keyed as recorded.
        """
        repo = _algo_repo(tmp_path, commit_package=True)
        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.7\n',
                           'my_strategy/extra/notes.txt': 'uncommitted\n',
                           'my_strategy/model.bin': bytes(range(256))})
        (repo / 'README.md').unlink()
        store = RunPatchStore(tmp_path / 'run_patches')

        identity = build_code_identity([_package_strategy(repo)],
                                       patch_sink=lambda root, key, patch: store.put(key, patch))
        state = identity.repositories[0]
        assert state.dirty and state.diff_hash and state.patch_ref and state.restorable

        patch_key = Path(state.patch_ref).name.removesuffix(PATCH_SUFFIX)
        assert _restores(repo, state.commit, store.get(patch_key), tmp_path / 'fresh')

    def test_a_clean_repository_records_no_diff(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        sink = _RecordingSink()

        identity = build_code_identity([_package_strategy(repo)], patch_sink=sink)

        state = identity.repositories[0]
        assert state.dirty is False and state.restorable is True
        assert state.diff_hash is None and state.patch_ref is None
        assert sink.calls == [], 'a clean tree has no patch to keep'

    def test_the_diff_hash_does_not_depend_on_how_git_renders_a_diff(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.7\n', 'notes.txt': 'n\n'})
        plain = _state_of(build_code_identity([_package_strategy(repo)]), repo).diff_hash

        for key, value in (('diff.noprefix', 'true'), ('color.diff', 'always'),
                           ('color.ui', 'always'), ('diff.context', '0')):
            run_git(repo, 'config', key, value)
        _fresh()
        configured = _state_of(build_code_identity([_package_strategy(repo)]), repo).diff_hash

        assert plain and configured == plain

    def test_the_same_delta_has_the_same_hash_in_a_sha1_and_a_sha256_repository(self,
                                                                                tmp_path):
        """
        A patch carries blob ids, whose length and value depend on the object format — so two
        repositories holding the same code render different patch bytes. The diff hash is taken
        over content and must not see the difference.
        """
        if not sha256_repositories_supported(tmp_path):
            pytest.skip('the installed git cannot create a SHA-256 repository')
        committed = {'.gitignore': '__pycache__/\n', 'a.py': 'A = 1\n', 'b.py': 'B = 1\n'}
        changes = {'a.py': 'A = 2\n', 'c.py': 'C = 1\n'}
        sha1 = make_repo(tmp_path / 'sha1', committed)
        sha256 = make_repo(tmp_path / 'sha256', committed, object_format='sha256')
        for repo in (sha1, sha256):
            write_files(repo, changes)
            write_files(repo, {'my_strategy/my_strategy.py':
                               _LOGIC_SOURCE.read_text(encoding='utf-8')})
        assert get_repo_patch(str(sha1)) != get_repo_patch(str(sha256)), 'precondition'

        hashes = [_state_of(build_code_identity([_strategy(repo / 'my_strategy' /
                                                           'my_strategy.py')]), repo).diff_hash
                  for repo in (sha1, sha256)]

        assert hashes[0] and hashes[0] == hashes[1]


class TestAComponentNoCommitCanContainIsUnversioned:
    """
    A strategy in a directory its repository IGNORES is invisible to `git status` and to the
    package listing alike. Read naively it is committed code with an empty digest — the exact
    shape of the operator's own setup, where this repository ignores `user_algos/`.
    """

    def test_a_strategy_in_a_directory_the_framework_ignores(self, framework_repo):
        write_files(framework_repo, {'.gitignore': '__pycache__/\nuser_algos/\n'})
        commit_all(framework_repo, 'ignore user_algos')
        package = framework_repo / 'user_algos' / 'my_strategy'
        write_files(package, {'my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8'),
                              'helpers.py': 'THRESHOLD = 0.6\n'})
        config = _strategy(package / 'my_strategy.py')

        identity = build_code_identity([config])

        component = identity.components[0]
        assert identity.framework.dirty is False, 'precondition: git status sees nothing'
        assert component.repository is None
        assert component.package_digest and component.package_digest != _EMPTY_DIGEST
        assert [(s.root, s.in_repository) for s in identity.repositories] == [
            (str(package), False)]
        assert identity.is_dirty() is True

        write_files(package, {'helpers.py': 'THRESHOLD = 0.9\n'})
        _fresh()
        assert build_code_identity([config]).components[0].package_digest != (
            component.package_digest), 'an edit to the ignored package moves its digest'

    def test_a_strategy_file_its_own_repository_ignores(self, tmp_path):
        repo = make_repo(tmp_path / 'algos', {'.gitignore': '__pycache__/\nmy_strategy.py\n',
                                              'README.md': 'algos\n'})
        package = repo / 'my_strategy'
        write_files(package, {'my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8')})
        assert run_git(repo, 'status', '--porcelain', '-uall') == '', 'precondition'
        config = _strategy(package / 'my_strategy.py')

        identity = build_code_identity([config])

        component = identity.components[0]
        assert component.repository is None
        assert component.package_digest and component.package_digest != _EMPTY_DIGEST
        assert [(s.root, s.in_repository) for s in identity.repositories] == [
            (str(package), False)]
        assert identity.is_dirty() is True

        write_files(package, {'my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8')
                              + '\n# tuned\n'})
        _fresh()
        assert build_code_identity([config]).components[0].package_digest != (
            component.package_digest), 'an edit to the ignored file moves its digest'


class TestCredentialHomesNeverReachThePatch:
    """
    A real key pasted into a tracked placeholder would otherwise be copied into `run_patches/` on
    every run start — before the credential guard ever sees it. The paths are recorded, their
    content is neither stored nor read, and the rest of the tree stays restorable.
    """

    _PLACEHOLDER = 'configs/credentials/peers/peer_credentials.json'
    _UNTRACKED = 'configs/credentials/venues/venue_credentials.json'

    def _dirty_with_credentials(self, framework_repo: Path, secret: str) -> None:
        """Commit a placeholder, then change it, add an untracked key file and edit code."""
        write_files(framework_repo, {self._PLACEHOLDER: '{"token": ""}\n'})
        commit_all(framework_repo, 'placeholder')
        write_files(framework_repo, {self._PLACEHOLDER: f'{{"token": "{secret}"}}\n',
                                     self._UNTRACKED: f'{{"secret": "{secret}"}}\n',
                                     'app.py': 'VALUE = 2\n'})

    def test_the_patch_carries_the_code_and_none_of_the_credentials(self, framework_repo,
                                                                    tmp_path):
        self._dirty_with_credentials(framework_repo, 'FAKE-SECRET-1')
        sink = _RecordingSink()

        framework = build_code_identity([], patch_sink=sink).framework

        patch = sink.calls[0][1]
        assert framework.patch_excluded == sorted([self._PLACEHOLDER, self._UNTRACKED])
        assert b'FAKE-SECRET' not in patch and b'credentials/' not in patch
        assert b'+VALUE = 2' in patch
        assert framework.restorable is True

        restored = working_tree(restore_on_fresh_clone(framework_repo, framework.commit, patch,
                                                       tmp_path / 'fresh'))
        expected = working_tree(framework_repo)
        expected[self._PLACEHOLDER] = b'{"token": ""}\n'
        del expected[self._UNTRACKED]
        assert restored == expected, 'everything but the credential homes comes back'

    def test_the_diff_hash_marks_them_without_reading_them(self, framework_repo):
        self._dirty_with_credentials(framework_repo, 'FAKE-SECRET-1')
        first = build_code_identity([]).framework.diff_hash

        write_files(framework_repo, {self._PLACEHOLDER: '{"token": "FAKE-SECRET-2"}\n',
                                     self._UNTRACKED: '{"secret": "FAKE-SECRET-2"}\n'})
        _fresh()
        other_secret = build_code_identity([]).framework.diff_hash

        write_files(framework_repo, {self._PLACEHOLDER: '{"token": ""}\n'})
        (framework_repo / self._UNTRACKED).unlink()
        _fresh()
        credentials_unchanged = build_code_identity([]).framework.diff_hash

        assert other_secret == first, 'the content of a credential home is never read'
        assert credentials_unchanged != first, 'but that it changed is recorded'


class TestWhatAPatchCannotCarry:
    """A repository inside the repository: its content is in no patch, so nothing is restorable."""

    def test_an_untracked_nested_repository_is_recorded_but_not_restorable(self, framework_repo):
        nested = make_repo(framework_repo / 'vendor' / 'lib', {'lib.py': 'LIB = 1\n'})
        write_files(framework_repo, {'app.py': 'VALUE = 2\n'})
        sink = _RecordingSink()

        framework = build_code_identity([], patch_sink=sink).framework

        assert nested.exists()
        assert framework.dirty is True and '?? vendor/lib/' in framework.changes
        assert framework.patch_ref is not None, 'the rest of the delta is still kept'
        assert framework.restorable is False

    def test_a_submodule_with_edits_inside_is_not_restorable(self, framework_repo, tmp_path):
        """
        A submodule appears in `git status` as ONE path without a trailing slash, and the patch
        renders it as a `Subproject commit …-dirty` line — the edits inside it are in no patch.
        """
        library = make_repo(tmp_path / 'lib', {'lib.py': 'LIB = 1\n'})
        run_git(framework_repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
                str(library), 'vendor')
        commit_all(framework_repo, 'add vendor')
        write_files(framework_repo, {'vendor/lib.py': 'LIB = 2\n'})
        sink = _RecordingSink()

        framework = build_code_identity([], patch_sink=sink).framework

        assert framework.dirty is True and framework.changes == [' M vendor']
        assert framework.restorable is False


class TestTheComponentIsIdentifiedByItsPackage:
    """A strategy is several modules; what is recorded is its package, by content."""

    def test_the_entry_names_the_file_its_repository_and_its_declared_version(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)

        identity = build_code_identity([_package_strategy(repo)])

        component = identity.components[0]
        assert component.role == ComponentRole.DECISION
        assert component.version and component.version == SimpleConsensus.get_metadata().version
        assert Path(component.source_path) == repo / 'my_strategy' / 'my_strategy.py'
        assert component.repository == str(repo)
        assert [state.root for state in identity.repositories] == [str(repo)]

    def test_the_same_code_has_the_same_digest_committed_or_not(self, tmp_path):
        """
        Content, not git's tree id: a tree id describes the COMMIT, so the same code would carry
        two identities depending on whether somebody had typed `git commit` yet.
        """
        repo = _algo_repo(tmp_path, commit_package=False)
        uncommitted = build_code_identity([_package_strategy(repo)])

        commit_all(repo, 'add my_strategy')
        _fresh()
        committed = build_code_identity([_package_strategy(repo)])

        assert uncommitted.repositories[0].dirty is True
        assert committed.repositories[0].dirty is False
        assert (uncommitted.components[0].package_digest
                == committed.components[0].package_digest)

    def test_a_change_to_a_sibling_module_changes_the_digest(self, tmp_path):
        """The threshold lives in a helper, the version string does not move — the digest does."""
        repo = _algo_repo(tmp_path, commit_package=True)
        before = build_code_identity([_package_strategy(repo)]).components[0]

        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.9\n'})
        _fresh()
        after = build_code_identity([_package_strategy(repo)]).components[0]

        assert after.version == before.version
        assert after.package_digest != before.package_digest

    def test_a_bytecode_cache_is_not_code(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        before = build_code_identity([_package_strategy(repo)]).components[0]

        write_files(repo, {'my_strategy/__pycache__/stale.cpython-312.pyc': b'\x00\x01'})
        _fresh()
        after = build_code_identity([_package_strategy(repo)]).components[0]

        assert after.package_digest == before.package_digest

    def test_a_file_at_a_repository_root_is_its_own_package(self, tmp_path):
        """
        The repository as a whole is not the package of a file lying in its root: a README or a
        second strategy beside it must not move its identity, while its own edit must.
        """
        repo = make_repo(tmp_path / 'algos', {
            '.gitignore': '__pycache__/\n',
            'my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8'),
            'other_strategy.py': 'OTHER = 1\n'})
        config = _strategy(repo / 'my_strategy.py')
        before = build_code_identity([config]).components[0].package_digest

        write_files(repo, {'other_strategy.py': 'OTHER = 2\n', 'README.md': 'new\n'})
        _fresh()
        sibling_changed = build_code_identity([config]).components[0].package_digest

        write_files(repo, {'my_strategy.py': _LOGIC_SOURCE.read_text(encoding='utf-8')
                           + '\n# tuned\n'})
        _fresh()
        own_change = build_code_identity([config]).components[0].package_digest

        assert before and before != _EMPTY_DIGEST
        assert sibling_changed == before
        assert own_change != before

    def test_a_component_in_no_repository_is_identified_but_never_clean(self, tmp_path):
        """
        Code under no version control cannot be restored from any commit. It still gets a digest
        — two runs can be compared by it — and the identity says it is not reproducible.
        """
        package = tmp_path / 'loose' / 'my_strategy'
        package.mkdir(parents=True)
        shutil.copy(_LOGIC_SOURCE, package / 'my_strategy.py')

        identity = build_code_identity([_strategy(package / 'my_strategy.py')])

        component = identity.components[0]
        assert component.repository is None and component.package_digest
        assert [(s.root, s.in_repository) for s in identity.repositories] == [
            (str(package), False)]
        assert identity.is_dirty() is True

    def test_a_component_named_by_several_scenarios_is_recorded_once(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)

        identity = build_code_identity([_package_strategy(repo), _package_strategy(repo)])

        assert len(identity.components) == 1
        assert len(identity.repositories) == 1


class TestEveryComponentOfARunIsRecorded:
    """
    A run is a decision logic plus its worker instances, and they need not share a home: CORE
    components live in this repository, a path component in whichever one holds its file.
    """

    def test_core_and_path_components_each_name_their_version_file_and_repository(
            self, framework_repo, tmp_path):
        repo = make_repo(tmp_path / 'algos', {
            '.gitignore': '__pycache__/\n',
            'my_workers/my_rsi_worker.py': _WORKER_SOURCE.read_text(encoding='utf-8')})
        worker_file = repo / 'my_workers' / 'my_rsi_worker.py'
        config = {'decision_logic_type': 'CORE/simple_consensus',
                  'worker_instances': {'rsi_fast': 'CORE/rsi', 'rsi_own': str(worker_file)}}

        identity = build_code_identity([config])

        by_name = {component.name: component for component in identity.components}
        assert set(by_name) == {'CORE/simple_consensus', 'rsi_fast', 'rsi_own'}

        decision = by_name['CORE/simple_consensus']
        assert decision.role == ComponentRole.DECISION and decision.type == 'CORE/simple_consensus'
        assert decision.version and decision.version == SimpleConsensus.get_metadata().version
        assert decision.repository == str(framework_repo)
        assert (Path(decision.repository) / decision.source_path).resolve() == _LOGIC_SOURCE
        assert decision.package_digest is None, 'covered by the framework state'

        core_worker = by_name['rsi_fast']
        assert core_worker.role == ComponentRole.WORKER and core_worker.type == 'CORE/rsi'
        assert core_worker.version and core_worker.version == RsiWorker.get_metadata().version
        assert core_worker.repository == str(framework_repo)
        assert (Path(core_worker.repository) / core_worker.source_path).resolve() == (
            _WORKER_SOURCE)

        path_worker = by_name['rsi_own']
        assert path_worker.role == ComponentRole.WORKER and path_worker.type == str(worker_file)
        assert path_worker.version == RsiWorker.get_metadata().version
        assert Path(path_worker.source_path) == worker_file
        assert path_worker.repository == str(repo)
        assert path_worker.package_digest and path_worker.package_digest != _EMPTY_DIGEST

        assert [state.root for state in identity.repositories] == [str(repo)], (
            'the framework is stated once, as the framework')


class TestAComponentThatCannotLoadIsRecordedWithoutASource:
    """
    The capture runs BEFORE the pipeline, which then loads the same component inside the
    session's startup handling and fails there with the factory's message and a STARTUP FAILED
    block. A module that raises at import must reach that path — an exception escaping from the
    capture would end the run with neither a record nor that block.
    """

    _BROKEN_AT_IMPORT = 'VALUE = UNDEFINED_NAME + 1\n'
    _BROKEN_METADATA = (
        'from python.framework.decision_logic.core import simple_consensus as base\n'
        '\n'
        '\n'
        'class BrokenMetadata(base.SimpleConsensus):\n'
        '    @classmethod\n'
        '    def get_metadata(cls):\n'
        "        raise RuntimeError('metadata unavailable')\n")

    @pytest.mark.parametrize('role, source, marker', [
        (ComponentRole.DECISION, _BROKEN_AT_IMPORT, 'UNDEFINED_NAME'),
        (ComponentRole.WORKER, _BROKEN_AT_IMPORT, 'UNDEFINED_NAME'),
        (ComponentRole.DECISION, _BROKEN_METADATA, 'metadata unavailable'),
    ])
    def test_a_module_that_raises_is_recorded_without_a_source(self, tmp_path, monkeypatch,
                                                               role, source, marker):
        broken = tmp_path / 'loose' / 'broken_component.py'
        write_files(broken.parent, {broken.name: source})
        logger = RecordingLogger()
        monkeypatch.setattr(code_identity_builder, 'get_global_logger', lambda: logger)
        if role is ComponentRole.DECISION:
            config = {'decision_logic_type': str(broken), 'worker_instances': {'rsi': 'CORE/rsi'}}
        else:
            config = {'decision_logic_type': 'CORE/simple_consensus',
                      'worker_instances': {'broken': str(broken)}}

        identity = build_code_identity([config])

        entry = next(component for component in identity.components if component.role is role
                     and component.type == str(broken))
        assert entry.source_path is None and entry.version == ''
        assert entry.repository is None and entry.package_digest is None
        assert str(broken) in logger.text_at('warning') and marker in logger.text_at('warning')
        others = [component for component in identity.components if component is not entry]
        assert others and all(component.version for component in others), (
            'the components beside it are still recorded')

    def test_a_missing_file_is_recorded_without_a_source(self, tmp_path):
        config = _strategy(tmp_path / 'missing.py')

        component = build_code_identity([config]).components[0]

        assert component.source_path is None and component.version == ''


class TestTheCaptureDescribesTheTreeItStartedFrom:
    """
    Git's reads are cached per process; the package digests are read FRESH at every capture. A
    later capture in the same process — the next sweep combination — that finds a package moved
    since the previous one re-reads the repositories, so one header never pairs fresh component
    content with a stale "clean". `verify_component_digests` is the same fresh read, used between
    the capture and the load.
    """

    def test_a_second_capture_after_an_edit_describes_the_edited_tree(self, tmp_path):
        """
        Measured in review before this held: combination 2 recorded version 9.9.99 and the
        package digest of combination 1 over a repository still reading clean.
        """
        repo = _algo_repo(tmp_path, commit_package=True)
        first = build_code_identity([_package_strategy(repo)])

        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.9\n'})
        second = build_code_identity([_package_strategy(repo)])

        assert second.components[0].package_digest != first.components[0].package_digest
        assert first.repositories[0].dirty is False
        assert second.repositories[0].dirty is True
        assert second.is_dirty()

    def test_a_second_capture_without_an_edit_repeats_the_first(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        first = build_code_identity([_package_strategy(repo)])
        second = build_code_identity([_package_strategy(repo)])

        assert second.components[0].package_digest == first.components[0].package_digest
        assert second.repositories[0].dirty is first.repositories[0].dirty is False

    def test_verification_is_quiet_while_nothing_moved(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        identity = build_code_identity([_package_strategy(repo)])

        assert verify_component_digests(identity) == []

    def test_verification_names_the_component_whose_package_was_edited(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        identity = build_code_identity([_package_strategy(repo)])

        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.9\n'})

        assert verify_component_digests(identity) == [identity.components[0]]

    def test_verification_names_the_component_whose_package_gained_a_file(self, tmp_path):
        """A new module beside the strategy is code the header does not describe."""
        repo = _algo_repo(tmp_path, commit_package=True)
        identity = build_code_identity([_package_strategy(repo)])

        write_files(repo, {'my_strategy/new_helper.py': 'NEW = 1\n'})

        assert verify_component_digests(identity) == [identity.components[0]]

    def test_verification_covers_a_package_in_no_repository(self, tmp_path):
        package = tmp_path / 'loose' / 'my_strategy'
        package.mkdir(parents=True)
        shutil.copy(_LOGIC_SOURCE, package / 'my_strategy.py')
        identity = build_code_identity([_strategy(package / 'my_strategy.py')])

        write_files(package, {'helpers.py': 'THRESHOLD = 0.9\n'})

        assert verify_component_digests(identity) == [identity.components[0]]

    def test_a_new_process_sees_the_edit(self, tmp_path):
        repo = _algo_repo(tmp_path, commit_package=True)
        first = build_code_identity([_package_strategy(repo)])

        write_files(repo, {'my_strategy/helpers.py': 'THRESHOLD = 0.9\n'})
        _fresh()
        again = build_code_identity([_package_strategy(repo)])

        assert again.components[0].package_digest != first.components[0].package_digest
        assert again.repositories[0].dirty is True


class TestAnUnansweredQuestionIsNeverClean:
    """
    Two degraded paths used to fail OPEN on the money path, both reproduced in review: a failed
    ignore listing counted as "nothing ignored", and a package that could not be read was dropped
    from the identity. Either way a strategy nobody could vouch for read as committed code.
    """

    def test_a_failed_ignore_listing_records_the_package_as_unknown(self, tmp_path, monkeypatch):
        repo = _algo_repo(tmp_path, commit_package=True)
        monkeypatch.setattr(code_identity_builder, 'list_ignored_files', lambda *_: None)

        identity = build_code_identity([_package_strategy(repo)])

        assert identity.components[0].repository is None
        assert identity.is_dirty()

    def test_an_unreadable_package_records_the_package_as_unknown(self, tmp_path, monkeypatch):
        repo = _algo_repo(tmp_path, commit_package=True)
        monkeypatch.setattr(code_identity_builder, '_compute_package_digest', lambda *_: None)

        identity = build_code_identity([_package_strategy(repo)])

        assert identity.components[0].repository is None
        assert identity.components[0].package_digest is None
        assert [state.in_repository for state in identity.repositories] == [None]
        assert identity.is_dirty()

    def test_a_component_unresolvable_at_capture_counts_as_moved_at_the_guard(self, tmp_path):
        """
        A strategy mid-edit (a SyntaxError) at the capture and fixed before the pipeline loads it
        reached the guard with nothing on record. Reaching the guard proves the pipeline loaded
        it after all — from a file the header does not describe.
        """
        missing = tmp_path / 'not_yet' / 'my_strategy.py'
        identity = build_code_identity([_strategy(missing)])

        assert identity.components[0].source_path is None
        assert verify_component_digests(identity) == [identity.components[0]]
