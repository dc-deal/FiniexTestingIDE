"""
The git reads a behaviour comparison needs (#641), on throwaway repositories: a revision resolved,
the paths between two commits, a clone at a commit that leaves its source untouched, and files read
as given commits hold them.
"""

from pathlib import Path
from typing import Dict, Tuple

import pytest

from python.framework.utils.git_info_utils import (
    changed_paths_between,
    clear_git_caches,
    clone_at_commit,
    get_repo_status,
    read_files_at_commits,
    resolve_commit,
)
from tests.shared.git_test_repos import commit_all, make_repo, run_git, write_files


@pytest.fixture(autouse=True)
def _fresh_git_caches():
    clear_git_caches()
    yield
    clear_git_caches()


@pytest.fixture
def repo(tmp_path: Path) -> Tuple[Path, str, str]:
    root = make_repo(tmp_path / 'src', {'python/a.py': 'x = 1\n', 'docs/a.md': 'old\n'})
    first = run_git(root, 'rev-parse', 'HEAD')
    write_files(root, {'docs/a.md': 'new\n'})
    run_git(root, 'mv', 'python/a.py', 'python/b.py')
    commit_all(root, 'move and document')
    return root, first, run_git(root, 'rev-parse', 'HEAD')


def _snapshot(directory: Path) -> Dict[str, Tuple[bytes, int]]:
    return {str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in directory.rglob('*') if path.is_file()}


class TestResolveCommit:

    def test_a_branch_and_an_abbreviation_resolve_to_the_full_hash(self, repo):
        root, first, second = repo
        branch = run_git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
        assert resolve_commit(str(root), branch) == second
        assert resolve_commit(str(root), first[:8]) == first

    def test_an_unknown_revision_is_none(self, repo):
        assert resolve_commit(str(repo[0]), 'no-such-branch') is None


class TestChangedPathsBetween:

    def test_a_rename_names_both_sides(self, repo):
        root, first, second = repo
        assert sorted(changed_paths_between(str(root), first, second)) == [
            'docs/a.md', 'python/a.py', 'python/b.py']

    def test_one_commit_against_itself_is_no_change(self, repo):
        root, first, _ = repo
        assert changed_paths_between(str(root), first, first) == []

    def test_an_unknown_commit_is_none(self, repo):
        assert changed_paths_between(str(repo[0]), '0' * 40, repo[2]) is None


class TestCloneAtCommit:

    def test_the_clone_stands_at_the_commit_and_is_clean(self, repo, tmp_path):
        root, first, _ = repo
        target = tmp_path / 'clone'
        assert clone_at_commit(str(root), first, target) == ''
        assert (target / 'python' / 'a.py').exists() and not (target / 'python' / 'b.py').exists()
        status = get_repo_status(str(target))
        assert status is not None and not status.dirty and first.startswith(status.commit)

    def test_the_source_repository_is_never_written(self, repo, tmp_path):
        root, first, _ = repo
        before = _snapshot(root / '.git')
        assert clone_at_commit(str(root), first, tmp_path / 'clone') == ''
        assert _snapshot(root / '.git') == before

    def test_an_unknown_commit_fails(self, repo, tmp_path):
        failure = clone_at_commit(str(repo[0]), '0' * 40, tmp_path / 'clone')
        assert failure.startswith('the checkout failed: ')
        assert failure != 'the checkout failed: no message'


class TestReadFilesAtCommits:

    def test_each_file_is_read_as_its_commit_holds_it(self, repo):
        root, first, second = repo
        texts = read_files_at_commits(str(root), [(first, 'python/a.py'), (second, 'python/a.py'),
                                                  (second, 'python/b.py')])
        assert texts == {(first, 'python/a.py'): 'x = 1\n', (second, 'python/a.py'): None,
                         (second, 'python/b.py'): 'x = 1\n'}

    def test_nothing_asked_reads_nothing(self, repo):
        assert read_files_at_commits(str(repo[0]), []) == {}
