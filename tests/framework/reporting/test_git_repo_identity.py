"""
The git reads behind a run's code identity, against real temporary repositories (#551).

`git_info_utils` answers three questions per repository: which commit, is the working tree
different from it, and what patch puts that difference back. A run header records the answers,
and the parity backtest after a live run relies on them — so none of them may depend on how the
developer configured git, and none may read "clean" where something changed.

Measured in review before these tests existed: `status.showUntrackedFiles=no` hid an untracked
strategy, an assume-unchanged file was invisible to `git status`, and `diff.noprefix=true` made a
stored patch unappliable. Each case below builds the repository that produced one of those
answers and pins the answer the pinned reads give instead.

Real repositories in `tmp_path`, never this working tree: the subject is what git answers, and a
test reading `/app` would pass or fail by whatever the developer happened to have open. The two
exceptions read `/app` on purpose and cheaply (`rev-parse`, never `status`): they pin that the
framework root is found from the CODE's location rather than from the cwd.
"""

import functools
import os
import subprocess
from pathlib import Path

import pytest

from python.framework.utils import git_info_utils
from python.framework.utils.git_info_utils import (
    clear_git_caches,
    get_framework_root,
    get_git_commit,
    get_git_info,
    get_repo_patch,
    get_repo_status,
    get_repo_toplevel,
    git_available,
    list_ignored_files,
    list_repo_files,
)
from tests.shared.git_test_repos import (
    make_repo,
    restore_on_fresh_clone,
    run_git,
    sha256_repositories_supported,
    working_tree,
    write_files,
)

# A dirty tree with every kind of change a patch has to carry: a modified file, a deleted file,
# an untracked text file in a new directory and an untracked binary file.
_COMMITTED = {'a.py': 'A = 1\n', 'gone.py': 'GONE = 1\n', 'keep.txt': 'keep\n'}
_CHANGES = {'a.py': 'A = 2\n', 'pkg/new.py': 'NEW = 1\n', 'model.bin': bytes(range(256))}

# Settings under which an unpinned `git diff` renders a patch `git apply` cannot take back, or
# renders different bytes for the same tree: no a/ b/ prefixes, colour escapes, zero context
# (a -U0 patch needs --unidiff-zero), mnemonic prefixes, an external diff driver.
_HOSTILE_DIFF_CONFIG = {
    'diff.noprefix': 'true',
    'diff.mnemonicPrefix': 'true',
    'color.diff': 'always',
    'color.ui': 'always',
    'diff.context': '0',
    'diff.renames': 'copies',
}


@pytest.fixture(autouse=True)
def _fresh_git_caches():
    """Every read here is cached per process; a test repository must never see a stale answer."""
    clear_git_caches()
    yield
    clear_git_caches()


def _dirty_repo(path: Path, object_format=None) -> Path:
    """
    A repository with one commit and every kind of change on top of it.

    Args:
        path: Where the repository is created
        object_format: 'sha256' for a SHA-256 repository, None for git's default

    Returns:
        The repository's top-level directory
    """
    repo = make_repo(path, _COMMITTED, object_format=object_format)
    write_files(repo, _CHANGES)
    (repo / 'gone.py').unlink()
    return repo


def _restores(repo: Path, patch: bytes, target: Path) -> bool:
    """
    Whether the stored patch, applied onto a fresh checkout of the recorded commit, reproduces
    the working tree byte for byte.

    Args:
        repo: The repository the patch was taken from
        patch: The patch bytes
        target: Where the fresh checkout is created

    Returns:
        True when the restored tree equals the original
    """
    commit = get_repo_status(str(repo)).commit
    restored = restore_on_fresh_clone(repo, commit, patch, target)
    return working_tree(restored) == working_tree(repo)


def _skip_without_sha256(tmp_path: Path) -> None:
    """Skip a SHA-256 case on a git that cannot create such a repository."""
    if not sha256_repositories_supported(tmp_path):
        pytest.skip('the installed git cannot create a SHA-256 repository')


class TestARepositoryIsReadByItsOwnRoot:
    """`git -C <root>`, never the process cwd — a strategy's repository is not this one."""

    def test_a_clean_repository_names_its_commit_and_nothing_else(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})

        status = get_repo_status(str(repo))

        assert status.commit == run_git(repo, 'rev-parse', '--short', 'HEAD')
        assert status.dirty is False and status.status_lines == []

    def test_a_modified_tracked_file_makes_it_dirty(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        write_files(repo, {'a.py': 'A = 2\n'})

        status = get_repo_status(str(repo))

        assert status.dirty is True
        assert status.status_lines == [' M a.py']
        assert status.changed_paths == ['a.py'] and status.untracked_paths == []

    def test_an_untracked_file_makes_it_dirty(self, tmp_path):
        """A strategy file nobody added is code that ran and is in no commit."""
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        write_files(repo, {'b.py': 'B = 1\n'})

        status = get_repo_status(str(repo))

        assert status.status_lines == ['?? b.py']
        assert status.untracked_paths == ['b.py']

    def test_an_untracked_directory_is_listed_file_by_file(self, tmp_path):
        """
        git's default names a new directory as ONE entry; the patch needs every file in it, and
        the delta digest reads each one — a directory entry would carry no content at all.
        """
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        write_files(repo, {'pkg/x.py': 'X = 1\n', 'pkg/sub/y.py': 'Y = 1\n'})

        assert sorted(get_repo_status(str(repo)).untracked_paths) == ['pkg/sub/y.py', 'pkg/x.py']

    def test_an_ignored_file_does_not(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'.gitignore': '*.log\n', 'a.py': 'A = 1\n'})
        write_files(repo, {'run.log': 'noise\n'})

        assert get_repo_status(str(repo)).dirty is False

    def test_a_path_in_no_repository_has_no_toplevel(self, tmp_path):
        loose = tmp_path / 'loose'
        loose.mkdir()

        assert get_repo_toplevel(str(loose)) is None
        assert get_repo_status(str(loose)) is None


class TestTheFrameworkRootIsTheCodesOwn:
    """
    The header's commit and the code identity's framework state must describe ONE tree. A run
    started with its cwd in one checkout while the code is imported from another would otherwise
    name the cwd's commit beside the imported code's state.
    """

    def _code_checkout(self) -> Path:
        """The repository this module is imported from, asked independently of the reader."""
        module_dir = Path(git_info_utils.__file__).resolve().parent
        return Path(run_git(module_dir, 'rev-parse', '--show-toplevel'))

    def test_the_root_is_found_from_the_module_not_from_the_cwd(self, tmp_path, monkeypatch):
        elsewhere = make_repo(tmp_path / 'elsewhere', {'a.py': 'A = 1\n'})
        monkeypatch.chdir(elsewhere)

        root = get_framework_root()

        assert Path(root) == self._code_checkout()
        assert Path(root) != elsewhere

    def test_the_commit_is_the_code_checkouts_not_the_cwds(self, tmp_path, monkeypatch):
        elsewhere = make_repo(tmp_path / 'elsewhere', {'a.py': 'A = 1\n'})
        monkeypatch.chdir(elsewhere)

        commit = get_git_commit()

        assert commit == run_git(self._code_checkout(), 'rev-parse', '--short', 'HEAD')
        assert commit != run_git(elsewhere, 'rev-parse', '--short', 'HEAD')


class TestNoGitIsUnknownNeverAnError:
    """
    A host without a git binary is not a repository without changes. Every read answers "could
    not tell" — and none of them raises, because the capture runs before a session's own startup
    handling and an exception there would escape without a STARTUP FAILED block.
    """

    def test_every_read_answers_none_without_a_git_binary(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        write_files(repo, {'a.py': 'A = 2\n'})
        monkeypatch.setenv('PATH', str(tmp_path / 'no_binaries_here'))

        assert git_available() is False
        assert get_repo_toplevel(str(repo)) is None
        assert get_repo_status(str(repo)) is None
        assert get_repo_patch(str(repo)) is None
        assert list_ignored_files(str(repo), '.') is None


class TestAUserConfigurationCannotHideAChange:
    """Nothing a developer configured may make a read answer "clean" where something changed."""

    def test_untracked_files_are_listed_although_the_repository_hides_them(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        run_git(repo, 'config', 'status.showUntrackedFiles', 'no')
        write_files(repo, {'pkg/new_strategy.py': 'NEW = 1\n'})
        assert run_git(repo, 'status', '--porcelain') == '', 'precondition: plain git hides it'

        status = get_repo_status(str(repo))

        assert status.dirty is True
        assert status.status_lines == ['?? pkg/new_strategy.py']

    @pytest.mark.parametrize('flag, letter', [('--assume-unchanged', 'h'),
                                              ('--skip-worktree', 'S')])
    def test_a_file_git_was_told_not_to_look_at_counts_as_changed(self, tmp_path, flag, letter):
        """
        Both flags make git stop LOOKING at a file — the opposite of the file being unchanged.
        Plain `git status` reports nothing for the edit; the read reports it as `!h <path>`.
        """
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n', 'b.py': 'B = 1\n'})
        run_git(repo, 'update-index', flag, 'a.py')
        write_files(repo, {'a.py': 'A = 2\n'})
        assert run_git(repo, 'status', '--porcelain') == '', 'precondition: plain git hides it'
        assert run_git(repo, 'ls-files', '-v', 'a.py') == f'{letter} a.py'

        status = get_repo_status(str(repo))

        assert status.dirty is True
        assert status.status_lines == ['!h a.py']
        assert status.hidden_paths == ['a.py'] and status.changed_paths == ['a.py']

    @pytest.mark.parametrize('flag', ['--assume-unchanged', '--skip-worktree'])
    def test_the_patch_carries_the_hidden_edit(self, tmp_path, flag):
        """
        The index still holds the stat data git recorded before it stopped looking. An edit of
        the same size whose mtime matches it — the ordinary case for an edit within the same
        second, forced here with `utime` and `core.trustctime=false` so the case cannot depend on
        timing — is invisible to a diff that trusts that stat data.
        """
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n', 'b.py': 'B = 1\n'})
        run_git(repo, 'config', 'core.trustctime', 'false')
        run_git(repo, 'update-index', flag, 'a.py')
        recorded = os.stat(repo / 'a.py')
        write_files(repo, {'a.py': 'A = 2\n', 'c.py': 'C = 1\n'})
        os.utime(repo / 'a.py', ns=(recorded.st_atime_ns, recorded.st_mtime_ns))

        patch = get_repo_patch(str(repo))

        assert b'+A = 2' in patch
        assert _restores(repo, patch, tmp_path / 'fresh')

    def test_a_submodule_the_repository_ignores_still_counts(self, tmp_path):
        """`diff.ignoreSubmodules=all` would make an edited submodule checkout read as clean."""
        library = make_repo(tmp_path / 'lib', {'lib.py': 'LIB = 1\n'})
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        run_git(repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
                str(library), 'vendor')
        run_git(repo, 'commit', '-q', '-m', 'add vendor')
        run_git(repo, 'config', 'diff.ignoreSubmodules', 'all')
        run_git(repo, 'config', 'submodule.vendor.ignore', 'all')
        write_files(repo, {'vendor/lib.py': 'LIB = 2\n'})
        assert run_git(repo, 'status', '--porcelain') == '', 'precondition: plain git hides it'

        status = get_repo_status(str(repo))

        assert status.dirty is True
        assert status.changed_paths == ['vendor']


class TestThePatchRendersTheSameUnderAnyConfiguration:
    """The rendering is pinned: same tree, same bytes, and always a patch `git apply` takes back."""

    def _configure(self, repo: Path, tmp_path: Path) -> None:
        """Apply every hostile diff setting, plus an external diff driver that prints garbage."""
        for key, value in _HOSTILE_DIFF_CONFIG.items():
            run_git(repo, 'config', key, value)
        driver = tmp_path / 'external_diff.sh'
        driver.write_text('#!/bin/sh\necho NOT A PATCH\n', encoding='utf-8')
        driver.chmod(0o755)
        run_git(repo, 'config', 'diff.external', str(driver))

    def test_the_patch_restores_the_tree_under_a_hostile_configuration(self, tmp_path):
        repo = _dirty_repo(tmp_path / 'r')
        self._configure(repo, tmp_path)

        patch = get_repo_patch(str(repo))

        assert b'\x1b[' not in patch, 'no colour escapes'
        assert b'NOT A PATCH' not in patch, 'no external driver'
        assert _restores(repo, patch, tmp_path / 'fresh')

    def test_the_same_tree_renders_the_same_bytes_with_and_without_it(self, tmp_path):
        repo = _dirty_repo(tmp_path / 'r')
        plain = get_repo_patch(str(repo))

        self._configure(repo, tmp_path)
        clear_git_caches()
        configured = get_repo_patch(str(repo))

        assert plain and configured == plain


class TestTheRepositorysOwnIndexIsNeverTouched:
    """
    The patch is rendered over a TEMPORARY copy of the index. Adding an untracked file to the
    real one as intent-to-add would turn the developer's `??` into ` A` — a run start changing
    what `git status` says about their tree.
    """

    def test_the_index_is_byte_identical_after_a_patch(self, tmp_path):
        repo = _dirty_repo(tmp_path / 'r')
        run_git(repo, 'update-index', '--assume-unchanged', 'keep.txt')
        write_files(repo, {'keep.txt': 'changed\n'})
        index = repo / '.git' / 'index'
        before = index.read_bytes()

        assert get_repo_patch(str(repo))

        assert index.read_bytes() == before
        assert run_git(repo, 'ls-files', '-v', 'keep.txt') == 'h keep.txt'
        assert '?? pkg/new.py' in run_git(repo, 'status', '--porcelain', '-uall').splitlines()


class TestTheTemporaryIndexKeepsGitsRacyCheck:
    """
    git trusts an index entry's stat data only when the entry is OLDER than the index file; an
    entry as new as the index ("racily clean") is compared by content. A copy of the index with a
    fresh mtime would turn such an entry into a trusted one — and an edit of the same size in the
    same second would vanish from the patch while `git status` still named it.
    """

    def test_an_edit_only_a_content_check_can_see_reaches_the_patch(self, tmp_path):
        """
        Built deterministically: every stamp is set to one past instant, `core.trustctime` is off,
        and a held `index.lock` stops `git status` from rewriting the index — which it would
        otherwise do, smudging the entry and hiding the case.
        """
        past = 1_600_000_000
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n', 'b.py': 'B = 1\n'})
        run_git(repo, 'config', 'core.trustctime', 'false')
        os.utime(repo / 'a.py', (past, past))
        run_git(repo, 'update-index', '--refresh')
        write_files(repo, {'a.py': 'A = 2\n'})
        os.utime(repo / 'a.py', (past, past))
        os.utime(repo / '.git' / 'index', (past, past))
        (repo / '.git' / 'index.lock').write_bytes(b'')

        status = get_repo_status(str(repo))
        patch = get_repo_patch(str(repo))

        assert status.status_lines == [' M a.py'], 'precondition: status sees it by content'
        assert b'+A = 2' in patch


class TestAPatchWithoutAnOrdinaryHead:
    """A repository with no commit yet, and one in SHA-256 object format."""

    def test_an_unborn_head_with_staged_files_yields_a_patch_against_the_empty_tree(self,
                                                                                   tmp_path):
        repo = tmp_path / 'r'
        repo.mkdir()
        run_git(repo, 'init', '-q')
        write_files(repo, {'staged.py': 'S = 1\n', 'loose.txt': 'untracked\n'})
        run_git(repo, 'add', 'staged.py')

        status = get_repo_status(str(repo))
        patch = get_repo_patch(str(repo))

        assert status.commit is None and status.dirty is True
        assert b'staged.py' in patch and b'loose.txt' in patch
        assert _restores(repo, patch, tmp_path / 'fresh')

    def test_a_sha256_repository_yields_a_patch_that_restores_it(self, tmp_path):
        _skip_without_sha256(tmp_path)
        repo = _dirty_repo(tmp_path / 'r', object_format='sha256')

        patch = get_repo_patch(str(repo))

        assert _restores(repo, patch, tmp_path / 'fresh')

    def test_an_unborn_sha256_repository_diffs_against_its_own_empty_tree(self, tmp_path):
        """The empty tree has a different id in each object format; the SHA-1 one does not exist
        in a SHA-256 repository, so a hard-coded constant would render nothing."""
        _skip_without_sha256(tmp_path)
        repo = tmp_path / 'r'
        repo.mkdir()
        run_git(repo, 'init', '-q', '--object-format=sha256')
        write_files(repo, {'staged.py': 'S = 1\n'})
        run_git(repo, 'add', 'staged.py')

        patch = get_repo_patch(str(repo))

        assert patch and _restores(repo, patch, tmp_path / 'fresh')


class TestIgnoredFilesAreListed:
    """What a repository ignores is code no commit of it can contain — listed file by file."""

    def test_every_file_of_an_ignored_directory_is_listed(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'.gitignore': 'user_algos/\n', 'a.py': 'A = 1\n'})
        write_files(repo, {'user_algos/my_strategy/my_strategy.py': 'S = 1\n',
                           'user_algos/my_strategy/helpers.py': 'H = 1\n'})

        ignored = list_ignored_files(str(repo), 'user_algos/my_strategy')

        assert ignored == ('user_algos/my_strategy/helpers.py',
                           'user_algos/my_strategy/my_strategy.py')

    def test_a_directory_nothing_ignores_lists_nothing(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'pkg/a.py': 'A = 1\n'})
        write_files(repo, {'pkg/b.py': 'B = 1\n'})

        assert list_ignored_files(str(repo), 'pkg') == ()


class TestTheReadsAreRunAtMostOncePerProcess:
    """
    §42: the reads are expensive on this project's tree, so each is cached per repository and a
    process describes the tree it STARTED from. `clear_git_caches` is the one way back.
    """

    def test_a_second_read_answers_the_first_state(self, tmp_path):
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        first = get_repo_status(str(repo))

        write_files(repo, {'a.py': 'A = 2\n'})

        assert get_repo_status(str(repo)) is first
        clear_git_caches()
        assert get_repo_status(str(repo)).dirty is True

    def test_clearing_reaches_every_cache(self, tmp_path, monkeypatch):
        """
        A cache the clear misses keeps a scripted test's FAKE answer for every later test in the
        process. So every read is filled, the caches are cleared once, and each read must go back
        to git. The framework root is pointed at a temporary repository so that the full read
        costs this tree's `git status` nothing.
        """
        repo = make_repo(tmp_path / 'r', {'a.py': 'A = 1\n'})
        write_files(repo, {'a.py': 'A = 2\n'})
        monkeypatch.setattr(git_info_utils, 'get_framework_root',
                            functools.lru_cache(maxsize=1)(lambda: str(repo)))
        # Ordered so each read's own git call is the only one it can make: the status is cached
        # before the patch and the full read ask for it.
        reads = {
            'git_available': git_available,
            'get_repo_toplevel': lambda: get_repo_toplevel(str(repo)),
            'get_repo_status': lambda: get_repo_status(str(repo)),
            'get_repo_patch': lambda: get_repo_patch(str(repo)),
            'list_ignored_files': lambda: list_ignored_files(str(repo), '.'),
            'list_repo_files': lambda: list_repo_files(str(repo), '.'),
            'get_git_commit': get_git_commit,
            'get_git_info': get_git_info,
        }
        for read in reads.values():
            read()

        clear_git_caches()

        calls = []
        real_run = subprocess.run
        monkeypatch.setattr(git_info_utils.subprocess, 'run',
                            lambda command, **kwargs: calls.append(command) or real_run(
                                command, **kwargs))
        stale = []
        for name, read in reads.items():
            before = len(calls)
            read()
            if len(calls) == before:
                stale.append(name)
        assert stale == [], f'answered from a cache the clear did not reach: {stale}'


class TestPathsAreNamesNeverPatterns:
    """
    An untracked file whose NAME is a glob or a pathspec magic widened a path list back onto the
    credential files the patch had excluded — measured in review with `configs/*` and
    `:(icase)configs`. Literal pathspecs make every path a name.
    """

    @pytest.mark.parametrize('odd_name', ['configs/*', 'configs/*.json', ':(icase)configs'])
    def test_an_oddly_named_file_does_not_widen_the_patch_onto_excluded_paths(self, tmp_path,
                                                                             odd_name):
        repo = make_repo(tmp_path / 'repo', {'configs/credentials/key.json': '{"k": ""}\n',
                                              'app.py': 'A = 1\n'})
        write_files(repo, {'configs/credentials/key.json': '{"k": "S3CRET"}\n',
                           odd_name: 'odd\n', 'app.py': 'A = 2\n'})

        patch = get_repo_patch(str(repo), ('configs/credentials/key.json',))

        assert patch is not None
        assert b'S3CRET' not in patch
        assert odd_name.encode() in patch


class TestTheEnvironmentCannotRedirectARead:
    """
    `GIT_DIFF_OPTS` rendered a zero-context patch that no longer applied, and `GIT_DIR` — set
    inside a git hook — pointed `-C <root>` at another repository. Both are dropped per call.
    """

    def test_a_diff_option_in_the_environment_does_not_change_the_patch(self, tmp_path,
                                                                        monkeypatch):
        repo = _dirty_repo(tmp_path / 'repo')
        plain = get_repo_patch(str(repo), ())
        clear_git_caches()
        monkeypatch.setenv('GIT_DIFF_OPTS', '--unified=0')

        assert get_repo_patch(str(repo), ()) == plain

    def test_a_git_dir_in_the_environment_does_not_answer_for_another_repository(self, tmp_path,
                                                                                monkeypatch):
        clean = make_repo(tmp_path / 'clean', {'a.py': 'A = 1\n'})
        dirty = _dirty_repo(tmp_path / 'dirty')
        monkeypatch.setenv('GIT_DIR', str(clean / '.git'))
        monkeypatch.setenv('GIT_WORK_TREE', str(clean))

        assert get_repo_status(str(dirty)).dirty is True


class TestTheBranchIsReadWithTheCommit:
    """A ledger row written at the END pairs its commit with the branch read beside it at start."""

    def test_the_status_names_the_checked_out_branch(self, tmp_path):
        repo = make_repo(tmp_path / 'repo', {'a.py': 'A = 1\n'})
        run_git(repo, 'checkout', '-q', '-b', 'feature-x')

        status = get_repo_status(str(repo))

        assert (status.branch, status.commit) == ('feature-x',
                                                  run_git(repo, 'rev-parse', '--short', 'HEAD'))
