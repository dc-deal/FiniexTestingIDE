"""
FiniexTestingIDE - Certificate Tree State
A certificate run must not be dirtied by its own artifact (#466, #468).

Found by running the release gate twice in one evening. `git status --porcelain` counts
UNTRACKED files, and every certificate run writes its artifact into its reports directory,
so the second run read the first run's output as an uncommitted change and recorded the
tree as dirty although every line of code was committed. Measured across three runs the
same night: 0 → 1 → 2 uncommitted, exactly tracking the artifacts written.

Harmless while rehearsing (`dev` is exempt from the dirty gate) and not harmless at all on
a declared release, which is precisely when it would fire: a release run that fails once
would fail its repeat for a second, unrelated reason — and the message points at the code,
which was never the problem.

Runs against a scripted `git`, not against this repository: the assertion is about the
rule, and a test that reads the real working tree would pass or fail by whatever the
developer happened to have open. The last class runs the certificate identity itself against
THROWAWAY repositories, because since #551 it reads the run's code identity — digest, patch and
the unknown-is-dirty rule — and a scripted git cannot render a patch.
"""

import subprocess
from pathlib import Path

import pytest

from python.framework.reporting.certificates.certificate_identity_builder import (
    build_certificate_identity,
)
from python.framework.store.run_patch_store import RunPatchStore
from python.framework.utils import code_identity_builder, git_info_utils, run_origin_builder
from python.framework.utils.code_identity_builder import clear_package_digest_cache
from python.framework.utils.git_info_utils import clear_git_caches, get_git_info
from tests.shared.git_test_repos import make_repo, write_files

REPORTS_DIR = 'tests/live_signal_feed/reports'


class FakeCompleted:
    """One scripted `git` answer."""

    def __init__(self, stdout: str):
        """
        Hold the output a scripted git call returns.

        Args:
            stdout: What the call writes to standard output
        """
        self.stdout = stdout
        self.returncode = 0


def _nul_separated(lines: str) -> str:
    """
    Render line-per-entry output the way git prints it under `-z`.

    Args:
        lines: One entry per line

    Returns:
        The entries, each terminated by a NUL byte
    """
    return ''.join(f'{line}\0' for line in lines.split('\n') if line)


def scripted_git(status_output: str, flagged_output: str = ''):
    """
    Build a `subprocess.run` stand-in answering the calls get_git_info makes.

    The reader pins its configuration on every call (`-c key=value` pairs and
    `--literal-pathspecs`, #551) and addresses the repository with `-C <root>`; all of it is
    dropped before the lookup, because the answer does
    not depend on them here. Anything else unexpected fails loudly — a new git call the stand-in
    does not know is exactly what this suite must notice.

    Args:
        status_output: What `git status --porcelain` reports, one entry per line
        flagged_output: What `git ls-files -v` reports, one entry per line — a lowercase tag
            marks assume-unchanged, `S` skip-worktree

    Returns:
        A callable with subprocess.run's signature
    """
    answers = {
        ('git', '--version'): 'git version 2.43.0',
        ('git', 'rev-parse', '--show-toplevel'): '/repo',
        ('git', 'rev-parse', '--short', 'HEAD'): 'abc1234',
        ('git', 'rev-parse', '--abbrev-ref', 'HEAD'): 'dev-v-1-4',
        ('git', 'log', '-1', '--format=%cI'): '2026-08-28T22:40:00+00:00',
        ('git', 'log', '-1', '--format=%s'): 'rework signal polling',
        ('git', 'status', '--porcelain=v1', '-z', '--untracked-files=all',
         '--ignore-submodules=none', '--no-renames'): _nul_separated(status_output),
        ('git', 'ls-files', '-v', '-z'): _nul_separated(flagged_output),
    }

    def run(command, **_kwargs):
        rest = list(command[1:])
        while rest and rest[0] in ('-C', '-c', '--literal-pathspecs'):
            rest = rest[1:] if rest[0] == '--literal-pathspecs' else rest[2:]
        key = ('git', *rest)
        if key not in answers:
            raise AssertionError(f'unscripted git call: {command}')
        return FakeCompleted(answers[key])

    return run


@pytest.fixture
def scripted(monkeypatch):
    """
    Install a scripted git for one test.

    Returns:
        A callable taking the porcelain output the test wants git to report, and optionally
        the `ls-files -v` output naming entries git was told not to look at
    """
    def install(status_output: str, flagged_output: str = ''):
        # The reader caches per process (§41), so each scripted case must start from an
        # empty cache — otherwise the second test reads the first test's answer and the
        # suite goes green while asserting nothing.
        clear_git_caches()
        monkeypatch.setattr(subprocess, 'run', scripted_git(status_output, flagged_output))
        monkeypatch.setattr(git_info_utils.subprocess, 'run',
                            scripted_git(status_output, flagged_output))
    yield install
    # And clear it again on the way out: monkeypatch removes the scripted subprocess, but
    # the SCRIPTED ANSWER would stay in the cache and reach every later test in this
    # process — a fake commit hash nobody would think to look for.
    clear_git_caches()


class TestOwnArtifactDoesNotDirtyTheTree:
    """The self-inflicted wound, pinned from both sides."""

    def test_an_untracked_artifact_in_the_reports_dir_is_not_a_dirty_tree(self, scripted):
        scripted(f'?? {REPORTS_DIR}/signal_feed_report_dev_2026-08-28_224049.json')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is False
        assert info.uncommitted_count == 0

    def test_without_the_exemption_the_same_artifact_still_counts(self, scripted):
        """The default is unchanged — every other consumer of this reader sees it."""
        scripted(f'?? {REPORTS_DIR}/signal_feed_report_dev_2026-08-28_224049.json')

        info = get_git_info()

        assert info.dirty is True
        assert info.uncommitted_count == 1


class TestTheExemptionStaysNarrow:
    """
    It drops ONE thing: untracked files under one directory.

    An exemption that swallowed more would be worse than the defect, because the check it
    weakens is the one asserting that a certificate names the commit which produced it.
    """

    def test_a_modified_tracked_file_in_the_reports_dir_still_counts(self, scripted):
        """A committed certificate someone edited is a real change, not an artifact."""
        scripted(f' M {REPORTS_DIR}/signal_feed_report_1.4.0_2026-08-26_203236.json')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 1

    def test_an_untracked_file_elsewhere_still_counts(self, scripted):
        """A new module that is not committed IS code the recorded commit does not have."""
        scripted('?? python/framework/signal_data/transport/signal_new_source.py')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 1

    def test_a_sibling_directory_sharing_the_prefix_is_not_exempt(self, scripted):
        """`reports_archive/` must not ride along on `reports/`."""
        scripted('?? tests/live_signal_feed/reports_archive/old.json')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True

    def test_the_artifact_is_dropped_while_real_changes_survive_beside_it(self, scripted):
        """The mixed case is the realistic one: a run writing while work is in progress."""
        scripted(
            f'?? {REPORTS_DIR}/signal_feed_report_dev_2026-08-28_224049.json\n'
            ' M python/framework/signal_data/transport/signal_stream_source.py\n'
            '?? python/cli/signal_mock_stream_cli.py')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 2

    def test_a_hidden_edit_in_the_reports_dir_still_counts(self, scripted):
        """
        An entry flagged assume-unchanged is a change git cannot see, not an artifact — the
        exemption drops untracked files only, and the reader reports the flagged entry itself.
        """
        scripted('', flagged_output=f'h {REPORTS_DIR}/signal_feed_report_1.4.0.json\n'
                                    'H python/framework/signal_data/signal_reader.py')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 1


class TestTheCertificateReadsTheRunsCodeIdentity:
    """
    A certificate answers "is this code exactly one commit" through the run's code identity
    (#551) — the answer the live real-money guard gives — and keeps a patch where it is not.

    Before, it read the git state on its own, and an unreadable tree counted as CLEAN: a declared
    release certified from a checkout git could not read passed the dirty check, with
    `git_commit: 'unknown'` in the committed record.
    """

    _REPORTS = 'reports'
    _ARTIFACT = 'reports/benchmark_report_dev_2026-09-25_101500.json'

    @pytest.fixture(autouse=True)
    def framework_repo(self, tmp_path, monkeypatch) -> Path:
        """
        A throwaway repository standing in for this one, holding a committed reports directory.

        Redirected at BOTH readers of the framework root — the capture and the patch sink — so
        the patch is filed as this repository's, where a certificate's patch belongs.

        Returns:
            The repository
        """
        clear_git_caches()
        clear_package_digest_cache()
        repo = make_repo(tmp_path / 'framework', {'app.py': 'VALUE = 1\n',
                                                  'reports/.gitkeep': ''})
        monkeypatch.setattr(code_identity_builder, 'get_framework_root', lambda: str(repo))
        monkeypatch.setattr(run_origin_builder, 'get_framework_root', lambda: str(repo))
        yield repo
        clear_git_caches()
        clear_package_digest_cache()

    def _identity(self, release_version: str = '1.4.0'):
        """
        Build the identity a certificate would carry.

        Args:
            release_version: The declared version

        Returns:
            The certificate identity
        """
        return build_certificate_identity(release_version=release_version,
                                          reports_dir=self._REPORTS)

    def test_its_own_artifact_is_neither_a_change_nor_in_the_patch(self, framework_repo):
        write_files(framework_repo, {self._ARTIFACT: '{"status": "PASSED"}\n'})

        identity = self._identity()

        assert identity.git_dirty is False and identity.uncommitted_count == 0
        assert identity.code_identity.framework.diff_hash is None
        assert identity.dirty_tree_warning() is None

    def test_a_dirty_tree_is_digested_and_its_patch_kept_without_the_artifact(
            self, framework_repo):
        write_files(framework_repo, {'app.py': 'VALUE = 2\n'})
        without_artifact = self._identity('dev').code_identity.framework.diff_hash
        clear_git_caches()
        write_files(framework_repo, {self._ARTIFACT: '{"status": "PASSED"}\n'})

        identity = self._identity('dev')

        framework = identity.code_identity.framework
        assert identity.git_dirty is True and identity.uncommitted_count == 1
        assert framework.diff_hash == without_artifact, 'the artifact is not part of the delta'
        patch_key = Path(framework.patch_ref).name.removesuffix('.patch')
        patch = RunPatchStore(Path(framework.patch_ref).parent).get(patch_key)
        assert b'+VALUE = 2' in patch and b'benchmark_report' not in patch
        assert framework.restorable is True
        assert identity.to_dict()['code_identity']['framework']['patch_ref'] == framework.patch_ref

    def test_a_modified_committed_file_in_the_reports_dir_still_counts(self, framework_repo):
        write_files(framework_repo, {'reports/.gitkeep': 'edited\n'})

        identity = self._identity()

        assert identity.git_dirty is True and identity.uncommitted_count == 1
        assert identity.dirty_tree_warning().startswith('DIRTY TREE')

    def test_an_unreadable_tree_is_refused_for_a_declared_release(self, tmp_path, monkeypatch):
        """The fail-open this closes: `git_dirty` used to be False wherever git gave no answer."""
        monkeypatch.setattr(code_identity_builder, 'get_framework_root', lambda: None)
        monkeypatch.setenv('PATH', str(tmp_path / 'no_binaries_here'))

        identity = self._identity()

        assert identity.git_commit == 'unknown' and identity.git_dirty is True
        assert identity.dirty_tree_warning().startswith('TREE STATE UNKNOWN')
        assert self._identity('dev').dirty_tree_warning() is None, 'a rehearsal stays exempt'
