"""
Run patch store tests (#551).

The store's one promise is that a run from uncommitted code can still be restored to the code
that ran. That rests on three properties, and each fails SILENTLY when broken: an entry whose
bytes are not its name looks exactly like a correct one until somebody applies it, a rewrite of
an existing entry is invisible unless something compares inodes, and a registration pointing at
the operator's tree works perfectly while the suite fills it with patches of code under test.
"""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.run_patch_errors import (
    RunPatchCorruptError,
    RunPatchHashMismatchError,
)
from python.framework.store.run_patch_store import (
    FOREIGN_PATCH_DIR,
    PATCH_SUFFIX,
    RunPatchStore,
)
from python.framework.store.store_catalog import StoreCatalog
from python.framework.types.store_types import RetrievalForm, StoreId, StoreKind
from python.framework.utils.git_info_utils import (
    clear_git_caches,
    get_repo_patch,
    get_repo_status,
)

_PATCH = b'diff --git a/my_strategy.py b/my_strategy.py\n-THRESHOLD = 0.6\n+THRESHOLD = 0.5\n'


def _hash(patch: bytes) -> str:
    """
    The key a patch is filed under.

    Args:
        patch: The patch bytes

    Returns:
        SHA256 hex digest
    """
    return hashlib.sha256(patch).hexdigest()


def _git(repo: Path, *args: str) -> None:
    """
    Run one git command inside a throwaway repository — test setup only.

    Args:
        repo: The repository directory
        args: The git arguments
    """
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=test@test',
                    '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True)


class TestContentAddressing:
    """The key IS the content — written, verified and read back as such."""

    def test_a_patch_round_trips_byte_for_byte(self, tmp_path):
        store = RunPatchStore(tmp_path)
        store.put(_hash(_PATCH), _PATCH)
        assert store.get(_hash(_PATCH)) == _PATCH

    def test_the_reference_is_the_configured_root_plus_the_file_name(self, tmp_path, monkeypatch):
        """Under the default configuration a header reads `run_patches/<hash>.patch`."""
        monkeypatch.chdir(tmp_path)
        ref = RunPatchStore(Path('run_patches')).put(_hash(_PATCH), _PATCH)
        assert ref == f'run_patches/{_hash(_PATCH)}{PATCH_SUFFIX}'
        assert (tmp_path / ref).read_bytes() == _PATCH

    def test_a_patch_under_the_wrong_hash_is_refused_and_nothing_is_written(self, tmp_path):
        """A name that is not the content would tie a run to code it never ran."""
        store = RunPatchStore(tmp_path)
        wrong = _hash(b'some other diff')
        with pytest.raises(RunPatchHashMismatchError) as caught:
            store.put(wrong, _PATCH)
        assert caught.value.actual == _hash(_PATCH)
        assert list(tmp_path.iterdir()) == []

    def test_equal_diffs_are_one_entry_however_many_runs_ran_them(self, tmp_path):
        store = RunPatchStore(tmp_path)
        refs = {store.put(_hash(_PATCH), _PATCH) for _ in range(3)}
        assert len(refs) == 1
        assert len(list(tmp_path.glob(f'*{PATCH_SUFFIX}'))) == 1

    def test_different_diffs_are_different_entries(self, tmp_path):
        store = RunPatchStore(tmp_path)
        other = _PATCH + b'+ADDED = True\n'
        assert store.put(_hash(_PATCH), _PATCH) != store.put(_hash(other), other)
        assert store.get(_hash(_PATCH)) == _PATCH
        assert store.get(_hash(other)) == other

    def test_the_file_name_a_header_names_is_the_key_a_restore_checks(self, tmp_path):
        """
        The documented restore verifies the file against its own NAME before `git apply` — the
        same check `get()` makes. That holds only while the name is the digest of the bytes, and
        the name is all a `patch_ref` carries: the header's `diff_hash` digests the changed
        content, not this rendering, and is not a key here.
        """
        store = RunPatchStore(tmp_path)
        ref = Path(store.put(_hash(_PATCH), _PATCH))

        assert hashlib.sha256(ref.read_bytes()).hexdigest() == ref.stem
        assert store.get(ref.stem) == _PATCH

    def test_an_empty_patch_is_an_ordinary_entry(self, tmp_path):
        """A dirty tree whose diff is empty still has a hash; the store does not special-case it."""
        store = RunPatchStore(tmp_path)
        store.put(_hash(b''), b'')
        assert store.get(_hash(b'')) == b''


class TestImmutability:
    """A RECORD is written once — and the one exception is damage, never a new version."""

    def test_a_second_put_of_the_same_patch_does_not_rewrite_the_file(self, tmp_path):
        """
        Asserted on the inode, because an atomic write REPLACES the file: a rewrite with equal
        bytes changes nothing a content comparison can see, and everything a concurrent reader
        holding the old file can.
        """
        store = RunPatchStore(tmp_path)
        ref = store.put(_hash(_PATCH), _PATCH)
        before = os.stat(ref)
        store.put(_hash(_PATCH), _PATCH)
        after = os.stat(ref)
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)

    def test_the_write_leaves_no_temporary_file(self, tmp_path):
        store = RunPatchStore(tmp_path)
        store.put(_hash(_PATCH), _PATCH)
        assert [p.name for p in tmp_path.iterdir()] == [f'{_hash(_PATCH)}{PATCH_SUFFIX}']

    def test_a_damaged_entry_is_refused_on_read(self, tmp_path):
        """Serving it would restore code that never ran; None would claim it was never stored."""
        store = RunPatchStore(tmp_path)
        ref = store.put(_hash(_PATCH), _PATCH)
        Path(ref).write_bytes(_PATCH[:10])
        with pytest.raises(RunPatchCorruptError) as caught:
            store.get(_hash(_PATCH))
        assert caught.value.actual == _hash(_PATCH[:10])

    def test_a_damaged_entry_is_repaired_by_the_next_put_of_its_patch(self, tmp_path):
        """The name fixes the content, so the verified bytes are the only right answer."""
        store = RunPatchStore(tmp_path)
        ref = store.put(_hash(_PATCH), _PATCH)
        Path(ref).write_bytes(b'truncated')
        store.put(_hash(_PATCH), _PATCH)
        assert store.get(_hash(_PATCH)) == _PATCH


class TestReading:
    """What a reader is told when there is nothing — or nothing valid — to read."""

    def test_an_unknown_hash_is_none(self, tmp_path):
        assert RunPatchStore(tmp_path).get(_hash(_PATCH)) is None

    def test_a_store_that_was_never_written_is_none_rather_than_an_error(self, tmp_path):
        assert RunPatchStore(tmp_path / 'never_created').get(_hash(_PATCH)) is None

    @pytest.mark.parametrize('key', [
        '../../etc/passwd', 'ABC', '', _hash(_PATCH).upper(), _hash(_PATCH) + '\n'])
    def test_a_key_that_is_not_a_digest_is_refused(self, tmp_path, key):
        """The key becomes a file name — a key that is not a digest could name one outside."""
        with pytest.raises(ValueError):
            RunPatchStore(tmp_path).get(key)


class TestRestoration:
    """The property the store exists for, end to end against a real repository."""

    def test_a_stored_patch_restores_the_tree_it_was_taken_from(self, tmp_path):
        repo = tmp_path / 'my_algos'
        repo.mkdir()
        _git(repo, 'init', '--quiet')
        (repo / 'my_strategy.py').write_text('THRESHOLD = 0.6\n', encoding='utf-8')
        _git(repo, 'add', 'my_strategy.py')
        _git(repo, 'commit', '--quiet', '-m', 'first')

        (repo / 'my_strategy.py').write_text('THRESHOLD = 0.5\n', encoding='utf-8')
        (repo / 'my_helper.py').write_text('LOOKBACK = 21\n', encoding='utf-8')

        clear_git_caches()
        try:
            patch = get_repo_patch(str(repo))
        finally:
            clear_git_caches()
        assert patch

        store = RunPatchStore(tmp_path / 'run_patches')
        store.put(_hash(patch), patch)

        _git(repo, 'checkout', '--quiet', '--', '.')
        (repo / 'my_helper.py').unlink()
        assert (repo / 'my_strategy.py').read_text(encoding='utf-8') == 'THRESHOLD = 0.6\n'

        restored = tmp_path / 'restored.patch'
        restored.write_bytes(store.get(_hash(patch)))
        _git(repo, 'apply', str(restored))
        assert (repo / 'my_strategy.py').read_text(encoding='utf-8') == 'THRESHOLD = 0.5\n'
        assert (repo / 'my_helper.py').read_text(encoding='utf-8') == 'LOOKBACK = 21\n'


class TestAForeignRepositoryKeepsItsOwnPatches:
    """
    A repository other than this one keeps its patches INSIDE ITSELF (#551), so private strategy
    code never enters this project's tree. What makes that safe fails silently when broken: a patch
    directory git can see turns the tree dirty, and the next real-money start from a freshly
    committed repository refuses over a file the operator never wrote.
    """

    @staticmethod
    def _committed_repo(path: Path) -> Path:
        """
        A repository holding one committed strategy file.

        Args:
            path: Where to create it

        Returns:
            The repository directory
        """
        path.mkdir()
        _git(path, 'init', '--quiet')
        (path / 'my_strategy.py').write_text('THRESHOLD = 0.6\n', encoding='utf-8')
        _git(path, 'add', 'my_strategy.py')
        _git(path, 'commit', '--quiet', '-m', 'first')
        return path

    def test_the_home_is_inside_the_repository(self, tmp_path, real_foreign_patch_homes):
        ref = RunPatchStore.inside_repository(str(tmp_path)).put(_hash(_PATCH), _PATCH)
        assert Path(ref) == tmp_path / FOREIGN_PATCH_DIR / f'{_hash(_PATCH)}{PATCH_SUFFIX}'
        assert Path(ref).read_bytes() == _PATCH

    def test_keeping_a_patch_never_dirties_the_repository(self, tmp_path,
                                                          real_foreign_patch_homes):
        """
        The operator's ordinary path: a dirty tree runs, its patch is kept, the change is
        committed with `add -A` — and the tree reads CLEAN, with no patch in the commit.
        """
        repo = self._committed_repo(tmp_path / 'my_algos')
        (repo / 'my_strategy.py').write_text('THRESHOLD = 0.5\n', encoding='utf-8')
        RunPatchStore.inside_repository(str(repo)).put(_hash(_PATCH), _PATCH)

        _git(repo, 'add', '-A')
        _git(repo, 'commit', '--quiet', '-m', 'second')
        clear_git_caches()
        try:
            status = get_repo_status(str(repo))
        finally:
            clear_git_caches()

        assert status is not None and status.dirty is False, status.status_lines
        assert (repo / FOREIGN_PATCH_DIR / '.gitignore').read_text(encoding='utf-8') == '*\n'
        committed = subprocess.run(['git', '-C', str(repo), 'ls-files'], check=True,
                                   capture_output=True, text=True).stdout.split()
        assert committed == ['my_strategy.py'], 'no patch reached the commit'

    def test_a_removed_ignore_file_comes_back_with_the_next_patch(self, tmp_path,
                                                                  real_foreign_patch_homes):
        """Checked on every put — also one that finds its patch already kept and writes nothing."""
        store = RunPatchStore.inside_repository(str(tmp_path))
        store.put(_hash(_PATCH), _PATCH)
        marker = tmp_path / FOREIGN_PATCH_DIR / '.gitignore'
        marker.unlink()

        store.put(_hash(_PATCH), _PATCH)
        assert marker.read_text(encoding='utf-8') == '*\n'

    def test_an_existing_ignore_file_is_left_as_it_is(self, tmp_path, real_foreign_patch_homes):
        marker = tmp_path / FOREIGN_PATCH_DIR / '.gitignore'
        marker.parent.mkdir()
        marker.write_text('# kept on purpose\n*\n', encoding='utf-8')

        RunPatchStore.inside_repository(str(tmp_path)).put(_hash(_PATCH), _PATCH)
        assert marker.read_text(encoding='utf-8') == '# kept on purpose\n*\n'

    def test_this_repositorys_store_writes_no_ignore_file(self, tmp_path):
        """`run_patches/` is covered by this repository's own ignore rules."""
        RunPatchStore(tmp_path).put(_hash(_PATCH), _PATCH)
        assert [entry.name for entry in tmp_path.iterdir()] == [
            f'{_hash(_PATCH)}{PATCH_SUFFIX}']

    def test_the_suite_never_files_a_patch_in_a_real_foreign_repository(self):
        """The operator's own `user_algos/` is such a repository; the session fixture redirects it."""
        operator_repository = Path('user_algos').resolve()
        ref = RunPatchStore.inside_repository(str(operator_repository)).put(_hash(_PATCH), _PATCH)
        assert not Path(ref).resolve().is_relative_to(operator_repository)


class TestRegistration:
    """The store as the catalog sees it — and as the configuration hands it out."""

    def test_it_is_a_record_opened_by_id_with_no_index_and_says_so(self):
        descriptor = StoreCatalog().get(StoreId.RUN_PATCHES)
        assert descriptor.kind is StoreKind.RECORD
        assert descriptor.form is RetrievalForm.DOCUMENT
        assert descriptor.index_path is None
        assert '#535' in descriptor.note, 'the lifetime question has an owner and says which'

    def test_the_suite_never_files_a_patch_in_the_operators_tree(self):
        """The suite runs from a dirty tree, so without the session fixture every run would."""
        configured = Path(AppConfigManager().get_run_patches_path()).resolve()
        assert configured != Path('run_patches').resolve()

    def test_the_catalog_counts_entries_under_the_configured_root(self, tmp_path, monkeypatch):
        """
        One root, read by both: the store a pipeline builds from the configuration and the
        catalog's registration. A root of its own rather than the session's, which other suites
        may already have written into.
        """
        monkeypatch.setattr(AppConfigManager, 'get_run_patches_path', lambda self: str(tmp_path))
        store = RunPatchStore(Path(AppConfigManager().get_run_patches_path()))
        second = _PATCH + b'+ADDED = True\n'
        store.put(_hash(_PATCH), _PATCH)
        store.put(_hash(second), second)
        (tmp_path / 'stray.tmp').write_bytes(b'half-written')

        rows = {row.store_id: row for row in StoreCatalog().status()}
        assert rows[StoreId.RUN_PATCHES].root == str(tmp_path)
        assert rows[StoreId.RUN_PATCHES].entries == 2, 'a temporary file is not an entry'
